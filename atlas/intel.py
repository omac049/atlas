"""Contract Divergence Report — the contract-intelligence deliverable.

Atlas's durable value, after the 2026-08-20 owner decision to drop the
execution track, is what it knows about CONTRACTS, not prices: which pairs on
Kalshi and Polymarket look like the same bet but are not provably so, which
venue publishes complete settlement policy and which does not, which contracts
may settle months apart despite covering the same event, and which published
rules texts changed. This module assembles that knowledge — all of it already
persisted by the monitor — into one weekly report a person outside this repo
could read.

Read-only reporting, like `atlas.frontier`. It never approves anything, never
relaxes a mismatch, and never feeds a verdict; `verify_equivalence` remains the
only thing that decides truth. Every section states its own limits, because the
report's credibility IS the product: an overclaimed divergence is worse than a
missed one.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from atlas.frontier import approval_frontier
from atlas.storage import AtlasStore

# Human-readable translations of the blocker codes the report surfaces most.
# The code stays alongside the prose so a reader can grep the repo for it.
_BLOCKER_PROSE = {
    "SETTLEMENT_GUARANTEE_UNKNOWN": (
        "the venue does not publish enough settlement policy to prove what "
        "happens in every branch (missing data, cancellation, revision)"
    ),
    "SETTLEMENT_POLICY_MISMATCH": (
        "both venues publish settlement policy, but the published texts diverge"
    ),
    "REVISION_POLICY_MISMATCH": (
        "the venues publish different rules about data revisions"
    ),
    "RESOLUTION_SOURCE_MISMATCH": (
        "the venues name different authoritative sources for the same number"
    ),
    "FAMILY_POLICY_INCOMPLETE": (
        "the venue's rules text omits terms this contract family requires"
    ),
    "MISSING_CANCELLATION_POLICY": "no published cancellation terms",
    "MISSING_REVISION_POLICY": "no published revision terms",
    "DISCRETIONARY_FAIR_PRICE_SETTLEMENT": (
        "the venue reserves discretionary fair-price settlement, so no "
        "deterministic guarantee is possible"
    ),
}


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _family(subject: str) -> str:
    return subject.split("|")[0] if subject else "unknown"


def _completeness_scorecard(candidates: list[dict]) -> list[dict]:
    """Per family: does each venue's published text support a guarantee?

    Built from the guarantee reason codes the deterministic assessor already
    records on every settlement candidate. This is the report's sharpest
    finding in practice: for several macro families Polymarket publishes a
    COMPLETE_* policy while Kalshi's text yields FAMILY_POLICY_INCOMPLETE —
    a fact about published rules, not an inference.
    """
    per_family: dict[str, dict[str, Counter]] = defaultdict(
        lambda: {"kalshi": Counter(), "polymarket": Counter()}
    )
    for candidate in candidates:
        family = _family(str(candidate.get("event_subject") or ""))
        for venue in ("kalshi", "polymarket"):
            guarantee = candidate.get(f"{venue}_guarantee") or {}
            status = str(guarantee.get("status") or "UNKNOWN")
            per_family[family][venue][status] += 1
            for code in guarantee.get("reason_codes") or []:
                per_family[family][venue][str(code)] += 1
    rows = []
    for family, venues in sorted(per_family.items()):
        row = {"family": family}
        for venue, counts in venues.items():
            guaranteed = counts.get("GUARANTEED", 0)
            total = sum(
                counts.get(status, 0)
                for status in ("GUARANTEED", "UNKNOWN", "NON_GUARANTEED")
            )
            top_blockers = [
                code
                for code, _ in counts.most_common()
                if code in _BLOCKER_PROSE
            ][:2]
            row[venue] = {
                "legs_seen": total,
                "guaranteed": guaranteed,
                "top_blockers": top_blockers,
            }
        rows.append(row)
    return rows


def _timing_asymmetries(observations: list[dict]) -> list[dict]:
    """Pairs where one venue's published rules allow settling before its twin.

    Sourced from the descriptive settlement-timing annotation on radar
    observations. The canonical example: Kalshi's chamber-control contracts may
    settle on a consensus of media calls while the Polymarket twin waits on
    official certification — the "same" position frees its capital months
    apart. Observability only; the annotation gates nothing.
    """
    latest_by_pair: dict[tuple[str, str], dict] = {}
    for observation in observations:
        timing = observation.get("settlement_timing") or {}
        if not timing.get("asymmetric"):
            continue
        key = (
            str(observation.get("kalshi_market_id")),
            str(observation.get("polymarket_market_id")),
        )
        held = latest_by_pair.get(key)
        if held is None or str(observation.get("observed_at") or "") > str(
            held.get("observed_at") or ""
        ):
            latest_by_pair[key] = observation
    # One finding per (subject, early venue), not one per market-id pair: a
    # single election family fans out into D/R and cross-venue variants that
    # all carry the identical clause, and eight lines saying the same thing
    # read as padding, which is the opposite of credibility.
    grouped: dict[tuple[str, str], dict] = {}
    for observation in latest_by_pair.values():
        timing = observation["settlement_timing"]
        key = (
            str(observation.get("event_subject") or ""),
            str(timing.get("early_venue") or ""),
        )
        entry = grouped.setdefault(
            key,
            {
                "event_subject": observation.get("event_subject"),
                "early_venue": timing.get("early_venue"),
                "early_codes": sorted(set(timing.get("early_codes") or [])),
                "pairs": 0,
                "days_to_settlement_min": None,
                "days_to_settlement_max": None,
                "horizon_basis": timing.get("horizon_basis"),
            },
        )
        entry["pairs"] += 1
        days = _decimal(timing.get("days_to_settlement"))
        if days is not None:
            low = entry["days_to_settlement_min"]
            high = entry["days_to_settlement_max"]
            entry["days_to_settlement_min"] = str(
                days if low is None else min(Decimal(low), days)
            )
            entry["days_to_settlement_max"] = str(
                days if high is None else max(Decimal(high), days)
            )
    rows = sorted(grouped.values(), key=lambda row: str(row.get("event_subject") or ""))
    return rows


def _price_disagreements(observations: list[dict]) -> list[dict]:
    """The latest reading per twin-shaped pair, most positive gap first.

    Framed as DISAGREEMENT, not opportunity: these pairs are candidates whose
    equivalence is unproven, most gaps are on a venue that cannot be traded,
    and the floors mark which readings are inside quantization noise or dust
    depth. The interesting research fact is that two venues price the "same"
    claim differently at all — and per the 2026-08-20 finding, they mostly do
    NOT once the venue is one arbitrage can reach.
    """
    latest_by_pair: dict[tuple[str, str], dict] = {}
    for observation in observations:
        key = (
            str(observation.get("kalshi_market_id")),
            str(observation.get("polymarket_market_id")),
        )
        held = latest_by_pair.get(key)
        if held is None or str(observation.get("observed_at") or "") > str(
            held.get("observed_at") or ""
        ):
            latest_by_pair[key] = observation
    rows = []
    for observation in latest_by_pair.values():
        gap = _decimal(observation.get("best_gap"))
        if gap is None:
            continue
        rows.append(
            {
                "event_subject": observation.get("event_subject"),
                "kalshi_title": observation.get("kalshi_title"),
                "polymarket_title": observation.get("polymarket_title"),
                "polymarket_venue": observation.get("polymarket_venue", "polymarket_global"),
                "tradeable_venue_pair": bool(observation.get("tradeable_venue_pair")),
                "best_gap": str(gap),
                "meets_floors": bool(
                    observation.get("meets_tick_floor")
                    and observation.get("meets_size_floor")
                ),
                "verification_status": observation.get("verification_status"),
                "observed_at": observation.get("observed_at"),
            }
        )
    rows.sort(key=lambda row: Decimal(row["best_gap"]), reverse=True)
    return rows


def _clarity_section(scan: dict | None, *, max_rows: int = 10) -> dict | None:
    """The Settlement Clarity Score rollup, or ``None`` when no scan exists.

    Absence is not a zero: a week with no scan omits the section entirely rather
    than printing empty distributions that read like a clean catalog. The scan is
    passed in rather than read from disk so this stays a pure function.
    """
    aggregates = (scan or {}).get("aggregates") or {}
    per_venue = aggregates.get("per_venue") or {}
    if not per_venue:
        return None
    return {
        "scanned_at": scan.get("generated_at"),
        "scoring_version": scan.get("scoring_version"),
        "markets_graded": aggregates.get("markets_graded", 0),
        "per_venue": per_venue,
        "degraded_venues": scan.get("degraded_venues") or [],
        "scope": scan.get("scope") or {},
        "worst_offenders": (aggregates.get("worst") or [])[:max_rows],
        # The scan states its own limits; this section adds the one that matters
        # here — a grade is intelligence, never a verdict.
        "limits": [
            *(scan.get("limits") or []),
            ("a grade never feeds a verification verdict or an approval label; "
            "it decides nothing"),
        ],
    }


async def divergence_report(
    store: AtlasStore,
    *,
    now: datetime | None = None,
    max_rows: int = 25,
    clarity_scan: dict | None = None,
) -> dict:
    """Assemble the weekly Contract Divergence Report from persisted evidence."""
    now = now or datetime.now(UTC)
    candidates = await store.latest_settlement_candidates(limit=200)
    frontier = await approval_frontier(store, now=now)
    observations = await store.all_gap_observations()
    labels = await store.trusted_learning_counts()

    awaiting = [
        candidate
        for candidate in candidates
        if str(candidate.get("queue_status")) == "AWAITING_SETTLEMENT"
    ]
    text_only_entries = [
        entry
        for entry in frontier.get("entries", [])
        if entry.get("blocked_only_on_venue_text")
    ]
    disagreements = _price_disagreements(observations)
    asymmetries = _timing_asymmetries(observations)
    clarity = _clarity_section(clarity_scan)

    report = {
        "paper_only": True,
        "generated_at": now.isoformat(),
        "report_kind": "CONTRACT_DIVERGENCE_REPORT",
        "headline": {
            "pairs_watched": len(candidates),
            "approved_awaiting_settlement": len(awaiting),
            "blocked_only_on_venue_text": len(text_only_entries),
            "settlement_timing_asymmetric_pairs": len(asymmetries),
            "price_disagreement_pairs": len(disagreements),
            "trusted_labels": dict(labels),
            "rules_changed_recently": frontier.get("rules_changed_recently", 0),
            "unmonitored_pairs": frontier.get("unmonitored_pairs", 0),
        },
        "approved_awaiting_settlement": [
            {
                "event_subject": candidate.get("event_subject"),
                "kalshi_title": candidate.get("kalshi_title"),
                "polymarket_title": candidate.get("polymarket_title"),
                "pair_status": candidate.get("pair_status"),
                "settlement_ready_at": candidate.get("settlement_ready_at"),
            }
            for candidate in awaiting[:max_rows]
        ],
        "venue_text_frontier": text_only_entries[:max_rows],
        "rules_completeness": _completeness_scorecard(candidates),
        "settlement_timing_asymmetries": asymmetries[:max_rows],
        "price_disagreements": disagreements[:max_rows],
        "method": {
            "source": "persisted monitor evidence only; regenerable from the database",
            "verdicts": "deterministic verify_equivalence; this report decides nothing",
            "caveats": [
                ("every pair below is a CANDIDATE unless labeled APPROVED — "
                "resemblance is not equivalence"),
                ("price gaps against polymarket_global are research signal only: "
                "that venue publishes no order book and cannot be traded from a "
                "US account"),
                ("a gap below the tick or size floors is quantization noise or "
                "dust-depth, not a finding"),
                ("an absent asymmetry annotation means not-measured, never "
                "symmetric"),
            ],
        },
        "blocker_glossary": dict(_BLOCKER_PROSE),
    }
    if clarity is not None:
        report["settlement_clarity"] = clarity
    return report


def render_divergence_markdown(report: dict) -> str:
    """The human-facing rendering. Plain markdown, no interpretation added."""
    headline = report["headline"]
    lines = [
        "# Contract Divergence Report",
        "",
        (f"Generated {report['generated_at']} · paper-only research · "
        "deterministic rules decide every verdict"),
        "",
        "## At a glance",
        "",
        f"- **{headline['pairs_watched']}** cross-venue pairs under watch",
        (f"- **{headline['approved_awaiting_settlement']}** deterministically "
        "approved equivalents awaiting settlement on both venues"),
        (f"- **{headline['blocked_only_on_venue_text']}** pairs blocked ONLY by "
        "venue rules text — these approve automatically if published text "
        "converges"),
        (f"- **{headline['settlement_timing_asymmetric_pairs']}** pairs where "
        "one venue's rules allow settling before its twin"),
        (f"- Trusted settled labels to date: "
        f"{sum(headline['trusted_labels'].values())} "
        f"({', '.join(f'{k}: {v}' for k, v in sorted(headline['trusted_labels'].items()))})"),
        (f"- {headline['rules_changed_recently']} rules-text changes detected in "
        f"the last 14 days · {headline['unmonitored_pairs']} pairs lack a "
        "rules baseline (their text could move unseen)"),
        "",
    ]

    awaiting = report["approved_awaiting_settlement"]
    if awaiting:
        lines += [
            "## Approved equivalents awaiting settlement",
            "",
            ("These passed the deterministic equivalence gate. Each becomes a "
            "trusted label only when both venues reach terminal settlement and "
            "the outcomes reconcile."),
            "",
            "| Subject | Kalshi | Polymarket | Settles by |",
            "|---|---|---|---|",
        ]
        for row in awaiting:
            lines.append(
                f"| {row['event_subject']} | {row['kalshi_title']} | "
                f"{row['polymarket_title']} | {row['settlement_ready_at'] or '—'} |"
            )
        lines.append("")

    frontier = report["venue_text_frontier"]
    if frontier:
        lines += [
            "## Blocked only by venue text",
            "",
            ("Same economic claim on both venues; the published rules texts do "
            "not yet prove it. No amount of code fixes these — only a venue "
            "publishing more complete terms. Atlas watches for exactly that."),
            "",
        ]
        for entry in frontier:
            codes = ", ".join(entry.get("text_clearable_codes") or []) or "—"
            lines.append(
                f"- **{entry.get('event_subject')}** — blocked on: {codes}"
            )
        lines.append("")

    scorecard = report["rules_completeness"]
    if scorecard:
        lines += [
            "## Rules-completeness scorecard",
            "",
            ("Of each family's watched legs, how many carry published text "
            "complete enough for a settlement guarantee."),
            "",
            "| Family | Kalshi guaranteed | Polymarket guaranteed | Dominant gap |",
            "|---|---|---|---|",
        ]
        for row in scorecard:
            kalshi, polymarket = row["kalshi"], row["polymarket"]
            gaps = kalshi["top_blockers"] or polymarket["top_blockers"]
            lines.append(
                f"| {row['family']} | {kalshi['guaranteed']}/{kalshi['legs_seen']} "
                f"| {polymarket['guaranteed']}/{polymarket['legs_seen']} "
                f"| {gaps[0] if gaps else '—'} |"
            )
        lines.append("")

    clarity = report.get("settlement_clarity")
    if clarity:
        lines += [
            "## Settlement clarity",
            "",
            ("Every open contract graded A–F on how completely its venue "
            "publishes what decides the payout — no twin pair required. "
            f"Scan of {clarity['markets_graded']} contracts taken "
            f"{clarity['scanned_at']}."),
            "",
            "| Venue | Graded | A | B | C | D | F | Mean |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for venue, stats in clarity["per_venue"].items():
            distribution = stats["grade_distribution"]
            bands = " | ".join(str(distribution.get(band, 0)) for band in "ABCDF")
            lines.append(
                f"| {venue} | {stats['markets']} | {bands} | {stats['mean_score']} |"
            )
        lines.append("")
        for venue in clarity.get("degraded_venues") or []:
            lines.append(
                f"- {venue} could not be fetched for this scan — absent, not clean."
            )
        for venue in (clarity.get("scope") or {}).get("truncated_venues") or []:
            lines.append(
                f"- {venue} was sampled, not swept in full "
                f"({(clarity.get('scope') or {}).get('max_markets_per_venue')} contracts)."
            )
        worst = clarity.get("worst_offenders") or []
        if worst:
            lines += [
                "",
                "Lowest-scoring open contracts:",
                "",
            ]
            for row in worst:
                codes = ", ".join(row.get("findings") or []) or "—"
                shared = row.get("contracts_with_this_title") or 1
                scale = f" ({shared} contracts share this wording)" if shared > 1 else ""
                lines.append(
                    f"- **{row['grade']} ({row['score']}/100)** {row['venue']} — "
                    f"{row['title']}{scale} · {codes}"
                )
        lines.append("")
        lines += [f"- {limit}" for limit in clarity["limits"]]
        lines.append("")

    asymmetries = report["settlement_timing_asymmetries"]
    if asymmetries:
        lines += [
            "## Settlement-timing asymmetries",
            "",
            ("Pairs where the published rules let one venue settle first — a "
            "'matched' position frees its capital only when the LATER leg "
            "settles, and the early leg can settle on projections the late leg "
            "must wait out."),
            "",
        ]
        for row in asymmetries:
            low = row.get("days_to_settlement_min")
            high = row.get("days_to_settlement_max")
            span = low if low == high else f"{low}–{high}"
            lines.append(
                f"- **{row['event_subject']}** ({row['pairs']} pair"
                f"{'s' if row['pairs'] != 1 else ''}): {row['early_venue']} may "
                f"settle early ({', '.join(row['early_codes']) or 'clause on file'}); "
                f"capital locked ≈{span} days"
            )
        lines.append("")

    disagreements = [r for r in report["price_disagreements"] if Decimal(r["best_gap"]) > 0]
    if disagreements:
        lines += [
            "## Where the venues disagree on price",
            "",
            ("Latest reading per twin-shaped pair, net of published taker fees. "
            "Research signal, not opportunity: most legs are on the offshore "
            "venue, and readings below the tick/size floors are noise."),
            "",
            "| Contract (Kalshi wording) | Gap | Venue | Tradeable | Above floors | Verdict |",
            "|---|---|---|---|---|---|",
        ]
        for row in disagreements:
            lines.append(
                f"| {row['kalshi_title']} | {row['best_gap']} | "
                f"{row['polymarket_venue']} | "
                f"{'yes' if row['tradeable_venue_pair'] else 'no'} | "
                f"{'yes' if row['meets_floors'] else 'no'} | "
                f"{row['verification_status']} |"
            )
        lines.append("")

    lines += [
        "## Method and limits",
        "",
        f"- {report['method']['source']}",
        f"- {report['method']['verdicts']}",
    ]
    lines += [f"- {caveat}" for caveat in report["method"]["caveats"]]
    lines.append("")
    return "\n".join(lines)
