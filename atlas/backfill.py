import asyncio
import re
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from atlas.learning import export_training_bundle, record_verified_pair
from atlas.models import ContractPair, Market, MarketStatus, MatchStatus
from atlas.outcomes import settled_outcome
from atlas.settlement import assess_settlement_guarantee
from atlas.storage import AtlasStore
from atlas.validation import market_evidence_snapshot
from atlas.verification import verify_equivalence

_STOPWORDS = {
    "will",
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "over",
    "under",
    "more",
    "than",
    "market",
    "event",
    "match",
    "game",
    "games",
    "versus",
    "vs",
    "win",
    "wins",
    "first",
    "team",
    "teams",
    "player",
    "players",
    "score",
    "scores",
    "scored",
    "scoring",
    "goal",
    "goals",
    "record",
    "records",
    "close",
    "price",
    "high",
    "low",
    "hit",
    "week",
    "rotten",
    "tomatoes",
    "spread",
    "total",
    "round",
    "points",
    # Election catalog labels describe the product family, not the specific event.
    "governor",
    "gubernatorial",
    "primary",
    "election",
    "republican",
    "democratic",
    "party",
    "nominee",
    "runoff",
    "popular",
    "vote",
    "votes",
    "finish",
    "receive",
}


async def backfill_historical_validation(
    store: AtlasStore,
    kalshi_venue: object,
    polymarket_venue: object,
    *,
    target_labels: int = 50,
    kalshi_event_pages: int = 100,
    polymarket_pages: int = 20,
    additional_polymarket_venues: dict[str, object] | None = None,
    additional_polymarket_pages: int = 20,
    max_candidate_events: int = 100,
    max_market_pairs: int = 2_000,
    max_resolved_pairs: int = 250,
    training_output_dir: str | None = None,
    concurrency: int = 10,
) -> dict[str, object]:
    """Create labels only from explicit final outcomes on both public venues."""
    started_at = datetime.now(UTC).isoformat()
    counts_before = await store.trusted_learning_counts()
    labels_before = sum(counts_before.values())
    blockers: Counter[str] = Counter()

    source_specs = [("polymarket_us", polymarket_venue, polymarket_pages)]
    source_specs.extend(
        (name, venue, additional_polymarket_pages)
        for name, venue in (additional_polymarket_venues or {}).items()
    )
    venue_coverage: dict[str, dict[str, int]] = {}
    closed: list[Market] = []
    final_polymarket: list[Market] = []
    for source_name, source_venue, page_limit in source_specs:
        source_closed = await source_venue.list_closed_markets(max_pages=page_limit)
        source_final = await _finalize_polymarket_markets(source_closed, source_venue, concurrency)
        venue_coverage[source_name] = {
            "closed_markets": len(source_closed),
            "final_binary_markets": len(source_final),
            "catalog_scope": str(getattr(source_venue, "catalog_scope", "all")),
        }
        closed.extend(source_closed)
        final_polymarket.extend(source_final)
        if not source_closed:
            blockers[f"{source_name.upper()}_CLOSED_CATALOG_EMPTY"] += 1
        if not source_final:
            blockers[f"{source_name.upper()}_FINAL_BINARY_EVIDENCE_UNAVAILABLE"] += 1

    events = await kalshi_venue.list_settled_events(max_pages=kalshi_event_pages)
    if not events:
        blockers["KALSHI_SETTLED_EVENT_CATALOG_EMPTY"] += 1
    event_candidates = historical_event_candidates(events, final_polymarket)
    candidate_events_found = len(event_candidates)
    if candidate_events_found > max_candidate_events:
        blockers["HISTORICAL_CANDIDATE_EVENT_CAP_APPLIED"] += 1
        event_candidates = event_candidates[:max_candidate_events]
    if final_polymarket and not event_candidates:
        blockers["NO_CROSS_VENUE_SETTLED_EVENT_OVERLAP"] += 1

    event_semaphore = asyncio.Semaphore(concurrency)

    async def compare_event(
        event: dict, polymarket_markets: list[Market]
    ) -> tuple[list[ContractPair], int, bool]:
        event_ticker = str(event.get("event_ticker") or "")
        try:
            async with event_semaphore:
                kalshi_markets = await kalshi_venue.list_settled_event_markets(event_ticker)
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return [], 0, True
        compared: list[ContractPair] = []
        missing_final = 0
        for left in kalshi_markets:
            if settled_outcome(left) is None:
                missing_final += 1
                continue
            for right in polymarket_markets:
                pair_id = f"historical:{left.market_id}::{right.market_id}"
                compared.append(verify_equivalence(left, right, pair_id))
        return compared, missing_final, False

    event_results = await asyncio.gather(
        *(
            compare_event(event, polymarket_markets)
            for event, polymarket_markets in event_candidates
        )
    )
    market_pairs: list[ContractPair] = []
    for compared, missing_final, fetch_failed in event_results:
        market_pairs.extend(compared)
        if missing_final:
            blockers["KALSHI_FINAL_BINARY_EVIDENCE_UNAVAILABLE"] += missing_final
        if fetch_failed:
            blockers["KALSHI_EVENT_MARKET_FETCH_FAILED"] += 1
    if len(market_pairs) > max_market_pairs:
        blockers["HISTORICAL_MARKET_PAIR_CAP_APPLIED"] += 1
        market_pairs = market_pairs[:max_market_pairs]

    new_labels = 0
    resolved_pairs = 0
    inconclusive_pairs = 0
    for pair in market_pairs:
        if resolved_pairs >= max_resolved_pairs:
            blockers["HISTORICAL_RESOLVED_PAIR_CAP_APPLIED"] += 1
            break
        outcome_a = settled_outcome(pair.market_a)
        outcome_b = settled_outcome(pair.market_b)
        if outcome_a is None or outcome_b is None:
            continue
        label, relationship = _historical_label(pair, outcome_a, outcome_b)
        if label is None:
            inconclusive_pairs += 1
            continue
        resolved_pairs += 1
        guarantee_a = str(assess_settlement_guarantee(pair.market_a)["status"])
        guarantee_b = str(assess_settlement_guarantee(pair.market_b)["status"])
        evidence = {
            "settlement_verified": True,
            "settlement_status": "SETTLED",
            "source_kind": "HISTORICAL_BACKFILL",
            "relationship_status": relationship,
            "outcome_a": outcome_a,
            "outcome_b": outcome_b,
            "kalshi_evidence": pair.market_a.raw_market_json.get("settlement_evidence")
            or {
                "source": "kalshi_finalized_market",
                "result": pair.market_a.raw_market_json.get("result"),
                "settlement_ts": pair.market_a.raw_market_json.get("settlement_ts"),
            },
            "polymarket_evidence": pair.market_b.raw_market_json.get("settlement_evidence"),
            "resolved_at": datetime.now(UTC).isoformat(),
        }
        created = await store.save_validation_case(
            {
                "pair_id": pair.pair_id,
                "source_kind": "HISTORICAL_BACKFILL",
                "decision_status": pair.status.value,
                "guarantee_a": guarantee_a,
                "guarantee_b": guarantee_b,
                "tracking_status": "RESOLVED",
                "payload": {"pair": pair.model_dump(mode="json")},
            }
        )
        await store.save_market_evidence_snapshots(
            [
                market_evidence_snapshot(pair.market_a, "HISTORICAL_BACKFILL"),
                market_evidence_snapshot(pair.market_b, "HISTORICAL_BACKFILL"),
            ]
        )
        await store.save_validation_outcome(
            {
                "pair_id": pair.pair_id,
                "resolved_at": evidence["resolved_at"],
                "relationship_status": relationship,
                "outcome_a": outcome_a,
                "outcome_b": outcome_b,
                "trusted_label": label,
                "evidence": evidence,
            }
        )
        await record_verified_pair(store, pair, label, settlement_evidence=evidence)
        new_labels += int(created)

    counts_after = await store.trusted_learning_counts()
    labels_after = sum(counts_after.values())
    remaining = max(0, target_labels - labels_after)
    approved_count = counts_after.get("APPROVED_EQUIVALENT", 0)
    rejected_count = counts_after.get("REJECTED", 0)
    if labels_after >= target_labels and approved_count == 0:
        blockers["NO_APPROVED_EQUIVALENT_LABELS"] += 1
    if labels_after >= target_labels and approved_count > 0 and rejected_count > 0:
        status = "MILESTONE_COMPLETE"
    elif labels_after >= target_labels and approved_count == 0:
        status = "LABEL_MIX_BLOCKED"
    elif new_labels:
        status = "MILESTONE_IN_PROGRESS"
    elif blockers:
        status = "EXTERNAL_EVIDENCE_BLOCKED"
    else:
        status = "NO_NEW_TRUSTED_LABELS"
    report: dict[str, object] = {
        "status": status,
        "paper_only": True,
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "target_labels": target_labels,
        "labels_before": labels_before,
        "labels_after": labels_after,
        "labels_remaining": remaining,
        "approved_labels": approved_count,
        "rejected_labels": rejected_count,
        "polymarket_closed_markets": len(closed),
        "polymarket_final_binary_markets": len(final_polymarket),
        "venue_coverage": venue_coverage,
        "kalshi_events_scanned": len(events),
        "cross_venue_event_candidates": len(event_candidates),
        "historical_candidate_events": len(event_candidates),
        "historical_candidate_events_found": candidate_events_found,
        "market_pairs_reviewed": len(market_pairs),
        "max_market_pairs": max_market_pairs,
        "max_resolved_pairs": max_resolved_pairs,
        "resolved_pairs": resolved_pairs,
        "inconclusive_pairs": inconclusive_pairs,
        "new_labels": new_labels,
        "blockers": dict(sorted(blockers.items())),
    }
    report["training_artifacts"] = await export_training_bundle(
        store,
        training_output_dir or str(store.path.parent / "training"),
        backfill_report=report,
    )
    await store.save_historical_backfill(report)
    return report


async def _finalize_polymarket_markets(
    markets: list[Market], venue: object, concurrency: int
) -> list[Market]:
    semaphore = asyncio.Semaphore(concurrency)

    async def finalize(market: Market) -> Market | None:
        async with semaphore:
            try:
                evidence = await venue.get_terminal_settlement_evidence(market.venue_market_id)
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                return None
        if not isinstance(evidence, dict):
            return None
        try:
            settlement = Decimal(str(evidence.get("settlement")))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if settlement not in {Decimal(0), Decimal(1)}:
            return None
        market.status = MarketStatus.SETTLED
        market.raw_market_json["settlement_value"] = "yes" if settlement == Decimal(1) else "no"
        market.raw_market_json["settlement_evidence"] = evidence
        return market

    finalized = await asyncio.gather(*(finalize(market) for market in markets))
    return [market for market in finalized if market is not None]


def historical_event_candidates(
    events: list[dict], polymarket_markets: list[Market]
) -> list[tuple[dict, list[Market]]]:
    """Require strong lexical event identity before any deterministic comparison."""
    matches: dict[str, tuple[dict, list[Market]]] = {}
    event_identities: list[tuple[dict, set[str], set[str]]] = []
    for event in events:
        event_text = " ".join(
            str(event.get(key) or "") for key in ("title", "sub_title", "event_ticker")
        )
        event_identities.append((event, _identity_tokens(event_text), _identity_dates(event_text)))
    event_token_index: dict[str, set[int]] = {}
    for index, (_, tokens, _) in enumerate(event_identities):
        for token in tokens:
            event_token_index.setdefault(token, set()).add(index)
    match_scores: dict[str, tuple[int, int]] = {}
    for market in polymarket_markets:
        market_text = " ".join(
            str(value)
            for value in (
                market.title,
                market.raw_market_json.get("question"),
                market.venue_market_id,
                *market.participants,
            )
            if value
        )
        market_tokens = _identity_tokens(market_text)
        market_dates = _identity_dates(market_text)
        candidate_indexes = sorted(
            {index for token in market_tokens for index in event_token_index.get(token, set())}
        )
        for index in candidate_indexes:
            event, event_tokens, event_dates = event_identities[index]
            if market_dates and event_dates and market_dates.isdisjoint(event_dates):
                continue
            shared = market_tokens & event_tokens
            required = min(3, len(market_tokens))
            if required < 2 or len(shared) < required:
                continue
            ticker = str(event.get("event_ticker") or "")
            if not ticker:
                continue
            if ticker not in matches:
                matches[ticker] = (event, [])
            matches[ticker][1].append(market)
            score = len(shared)
            date_score = int(bool(market_dates and event_dates and market_dates & event_dates))
            previous = match_scores.get(ticker, (0, 0))
            match_scores[ticker] = (
                max(previous[0], score),
                previous[1] + date_score,
            )
    ordered_tickers = sorted(
        matches,
        key=lambda ticker: (
            -match_scores[ticker][0],
            -match_scores[ticker][1],
            ticker,
        ),
    )
    return [matches[ticker] for ticker in ordered_tickers]


def _identity_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2 and token not in _STOPWORDS and not token.isdigit()
    }


def _identity_dates(value: str) -> set[str]:
    dates = set(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", value))
    month_numbers = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    for year, month, day in re.findall(r"\b(\d{2})([A-Z]{3})(\d{2})\b", value.upper()):
        if month.lower() in month_numbers:
            dates.add(f"20{year}-{month_numbers[month.lower()]:02d}-{int(day):02d}")
    month_pattern = (
        "January|February|March|April|May|June|July|August|September|October|November|December"
    )
    for month, day, year in re.findall(
        rf"\b({month_pattern})\s+(\d{{1,2}})(?:,)?\s+(20\d{{2}})\b",
        value,
        flags=re.IGNORECASE,
    ):
        month_number = month_numbers[month[:3].lower()]
        dates.add(f"{year}-{month_number:02d}-{int(day):02d}")
    return dates


def _historical_label(pair: ContractPair, outcome_a: str, outcome_b: str) -> tuple[str | None, str]:
    inverse = pair.status is MatchStatus.APPROVED_INVERSE
    if pair.status not in {
        MatchStatus.APPROVED_EQUIVALENT,
        MatchStatus.APPROVED_INVERSE,
    }:
        return None, "INCONCLUSIVE"
    agrees = outcome_a != outcome_b if inverse else outcome_a == outcome_b
    if pair.status in {
        MatchStatus.APPROVED_EQUIVALENT,
        MatchStatus.APPROVED_INVERSE,
    }:
        return ("APPROVED_EQUIVALENT", "CONFIRMED") if agrees else ("REJECTED", "DIVERGED")
    if not agrees:
        return "REJECTED", "DIVERGED"
    return None, "INCONCLUSIVE"
