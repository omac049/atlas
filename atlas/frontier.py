"""Approval frontier — which blocked pairs are closest, and did their text move?

Every blocked settlement candidate is waiting on the same kind of thing: a venue
publishing terms it does not publish today. The project's standing rule is to wait
for that text rather than infer it, which has meant waiting *passively* — nothing
told anyone when a venue republished its rules, so a cleared blocker could sit
unnoticed until the settled overlap aged out of the catalog.

This module is read-only reporting over evidence already in the store. It ranks the
frontier by how far each pair is from approval and flags pairs whose published rules
text has changed, so a human knows where to look. It never approves anything, never
relaxes a mismatch, and never feeds a verdict — `verify_equivalence` remains the only
thing that decides truth.
"""

from datetime import UTC, datetime, timedelta

from atlas.models import Market
from atlas.storage import AtlasStore
from atlas.validation import market_evidence_snapshot

# A venue republishing its terms is the only event that can clear a text blocker, so
# recent changes are surfaced first. The window is a review prompt, not a rule.
RULES_CHANGE_RECENT_DAYS = 14

# Codes that a venue could clear by publishing more complete terms. Everything else
# (a genuine scope, threshold, or signed-line divergence) describes contracts that
# are simply not the same bet, and no amount of waiting changes that.
TEXT_CLEARABLE_CODES = frozenset(
    {
        "SETTLEMENT_GUARANTEE_UNKNOWN",
        "REVISION_POLICY_MISMATCH",
        "SETTLEMENT_POLICY_MISMATCH",
        "RESOLUTION_SOURCE_MISMATCH",
    }
)


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _leg_rules_state(
    market_id: str,
    history: dict[str, list[dict[str, object]]],
    now: datetime,
) -> dict[str, object]:
    versions = history.get(market_id, [])
    latest = versions[-1] if versions else None
    changed_at = str(latest["first_observed_at"]) if latest and len(versions) > 1 else None
    parsed_change = _parse_timestamp(changed_at) if changed_at else None
    return {
        "market_id": market_id,
        "rules_versions": len(versions),
        # No snapshot means no baseline to compare against, so a text change on this
        # leg would go undetected. That is a blind spot, not a quiet "no change".
        "rules_monitored": bool(versions),
        "current_rules_hash": str(latest["rules_hash"]) if latest else None,
        "rules_changed_at": changed_at,
        "rules_changed_recently": bool(
            parsed_change and now - parsed_change <= timedelta(days=RULES_CHANGE_RECENT_DAYS)
        ),
    }


def _candidate_frontier_entry(
    candidate: dict[str, object],
    history: dict[str, list[dict[str, object]]],
    now: datetime,
) -> dict[str, object]:
    codes = [str(code) for code in (candidate.get("mismatch_codes") or [])]
    text_clearable = [code for code in codes if code in TEXT_CLEARABLE_CODES]
    structural = [code for code in codes if code not in TEXT_CLEARABLE_CODES]
    kalshi = _leg_rules_state(str(candidate.get("kalshi_market_id") or ""), history, now)
    polymarket = _leg_rules_state(str(candidate.get("polymarket_market_id") or ""), history, now)
    return {
        "event_subject": str(candidate.get("event_subject") or ""),
        "market_type": str(candidate.get("market_type") or ""),
        "rule_distance": int(candidate.get("rule_distance") or 0),
        "queue_status": str(candidate.get("queue_status") or ""),
        "pair_status": str(candidate.get("pair_status") or ""),
        "next_gate": str(candidate.get("next_gate") or ""),
        "blocking_codes": codes,
        # Split so the report never implies that a structural divergence is
        # something a venue could fix by publishing more text.
        "text_clearable_codes": text_clearable,
        "structural_codes": structural,
        "blocked_only_on_venue_text": bool(codes) and not structural,
        "kalshi": kalshi,
        "polymarket": polymarket,
        "rules_changed_recently": bool(
            kalshi["rules_changed_recently"] or polymarket["rules_changed_recently"]
        ),
        # A pair is only genuinely "being watched" when both legs have a recorded
        # baseline; otherwise waiting for text alignment cannot detect it arriving.
        "rules_fully_monitored": bool(kalshi["rules_monitored"] and polymarket["rules_monitored"]),
        "unmonitored_legs": [
            leg
            for leg, state in (("kalshi", kalshi), ("polymarket", polymarket))
            if not state["rules_monitored"]
        ],
        "recheck_reason": (
            "PUBLISHED_RULES_CHANGED"
            if kalshi["rules_changed_recently"] or polymarket["rules_changed_recently"]
            else None
        ),
    }


def _frontier_rank(entry: dict[str, object]) -> tuple:
    # Changed text first (something actually happened), then pairs a venue could
    # still unblock, then fewest remaining mismatches.
    return (
        0 if entry["rules_changed_recently"] else 1,
        0 if entry["blocked_only_on_venue_text"] else 1,
        entry["rule_distance"],
        len(entry["blocking_codes"]),
        entry["event_subject"],
    )


async def capture_frontier_rules_evidence(
    store: AtlasStore,
    candidates: list[dict[str, object]],
    markets: list[Market],
) -> dict[str, int]:
    """Record a rules baseline for both legs of every blocked candidate.

    The validation universe only snapshots markets that are already `GUARANTEED`
    or that appear in a review pair, and it never sees Polymarket Global legs at
    all. Blocked frontier pairs are exactly the markets that fail those tests —
    a pair is blocked *because* a leg's guarantee is unknown — so the pairs the
    project most wants to watch for text alignment were the ones with no baseline
    to diff against. Snapshotting is observation only; it changes no verdict and
    grants no guarantee.
    """
    by_id: dict[str, Market] = {}
    for market in markets:
        for identifier in (market.market_id, market.venue_market_id):
            if identifier:
                by_id.setdefault(str(identifier), market)

    tracked: dict[str, Market] = {}
    missing = 0
    for candidate in candidates:
        if str(candidate.get("queue_status")) != "BLOCKED":
            continue
        for key in ("kalshi_market_id", "polymarket_market_id"):
            identifier = str(candidate.get(key) or "")
            if not identifier:
                continue
            market = by_id.get(identifier)
            if market is None:
                # The leg is not in this scan's catalogs (e.g. a Global market on a
                # cycle that did not fetch it). Counted, never silently dropped.
                missing += 1
                continue
            tracked[market.market_id] = market

    evidence = await store.save_market_evidence_snapshots(
        [market_evidence_snapshot(market, "APPROVAL_FRONTIER") for market in tracked.values()]
    )
    return {
        "frontier_legs_observed": evidence["observed"],
        "frontier_new_versions": evidence["new_versions"],
        "frontier_legs_unavailable": missing,
    }


async def approval_frontier(
    store: AtlasStore, *, limit: int = 200, now: datetime | None = None
) -> dict[str, object]:
    """Rank blocked candidates by proximity to approval and flag moved venue text."""
    now = now or datetime.now(UTC)
    candidates = await store.latest_settlement_candidates(limit=limit)
    blocked = [c for c in candidates if str(c.get("queue_status")) == "BLOCKED"]
    market_ids = [
        str(candidate.get(key) or "")
        for candidate in blocked
        for key in ("kalshi_market_id", "polymarket_market_id")
        if candidate.get(key)
    ]
    history = await store.rules_version_history(market_ids)
    entries = sorted(
        (_candidate_frontier_entry(candidate, history, now) for candidate in blocked),
        key=_frontier_rank,
    )
    return {
        "generated_at": now.isoformat(),
        "paper_only": True,
        "blocked_candidates": len(entries),
        "blocked_only_on_venue_text": sum(1 for e in entries if e["blocked_only_on_venue_text"]),
        "rules_changed_recently": sum(1 for e in entries if e["rules_changed_recently"]),
        "rules_change_window_days": RULES_CHANGE_RECENT_DAYS,
        # Pairs whose text could move without anyone noticing — the honest limit of
        # this report, surfaced rather than left implicit.
        "unmonitored_pairs": sum(1 for e in entries if not e["rules_fully_monitored"]),
        "entries": entries,
    }
