import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256

import httpx

from atlas.fingerprints import build_fingerprint
from atlas.learning import record_verified_pair
from atlas.models import ContractPair, Market, MarketStatus, MatchStatus
from atlas.outcomes import settled_outcome
from atlas.policy_evidence import parse_market_policy_evidence
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.storage import AtlasStore
from atlas.verification import verify_equivalence


def market_evidence_snapshot(market: Market, reason: str) -> dict[str, object]:
    fingerprint = build_fingerprint(market)
    payload = {
        "market_id": market.market_id,
        "venue": market.venue.value,
        "venue_market_id": market.venue_market_id,
        "title": market.title,
        "status": market.status.value,
        "close_time": market.close_time.isoformat() if market.close_time else None,
        "resolution_time": (
            market.resolution_time.isoformat() if market.resolution_time else None
        ),
        "resolution_source": market.resolution_source,
        "resolution_text": market.resolution_text,
        "raw_rules_text": market.raw_rules_text,
        "outcome": settled_outcome(market),
        "venue_result": (
            market.raw_market_json.get("result")
            or market.raw_market_json.get("settlement_value")
            or market.raw_market_json.get("expiration_value")
        ),
        "settlement_evidence": market.raw_market_json.get("settlement_evidence"),
        "settlement_policy_evidence": parse_market_policy_evidence(market).model_dump(
            mode="json"
        ),
        "fingerprint": fingerprint.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        "market_id": market.market_id,
        "venue": market.venue.value,
        "observed_at": datetime.now(UTC).isoformat(),
        "evidence_hash": sha256(encoded.encode()).hexdigest(),
        "rules_hash": fingerprint.rules_hash or fingerprint.digest(),
        "status": market.status.value,
        "outcome": payload["outcome"],
        "reason": reason,
        "payload": payload,
    }


async def capture_validation_universe(
    store: AtlasStore,
    kalshi_markets: list[Market],
    polymarket_markets: list[Market],
    approved_pairs: list[ContractPair],
    reviews: list[dict[str, object]],
) -> dict[str, int]:
    all_markets = kalshi_markets + polymarket_markets
    by_id = {
        identifier: market
        for market in all_markets
        for identifier in (market.market_id, market.venue_market_id)
    }
    guaranteed = [
        market
        for market in all_markets
        if assess_settlement_guarantee(market)["status"]
        == GuaranteeStatus.GUARANTEED.value
    ]
    relevant: dict[str, Market] = {market.market_id: market for market in guaranteed}
    cases: list[tuple[ContractPair, str]] = [(pair, "APPROVED") for pair in approved_pairs]
    for review in reviews:
        left = by_id.get(str(review.get("kalshi_market_id", "")))
        right = by_id.get(str(review.get("polymarket_market_id", "")))
        if left is None or right is None:
            continue
        relevant[left.market_id] = left
        relevant[right.market_id] = right
        guarantee_a = assess_settlement_guarantee(left)["status"]
        guarantee_b = assess_settlement_guarantee(right)["status"]
        if guarantee_a != GuaranteeStatus.GUARANTEED.value or guarantee_b != GuaranteeStatus.GUARANTEED.value:
            continue
        pair_id = f"validation:{left.market_id}::{right.market_id}"
        cases.append((verify_equivalence(left, right, pair_id), "RULE_REVIEW"))

    snapshots = [
        market_evidence_snapshot(market, "VALIDATION_UNIVERSE")
        for market in relevant.values()
    ]
    evidence = await store.save_market_evidence_snapshots(snapshots)
    new_cases = 0
    for pair, source_kind in cases:
        guarantee_a = str(assess_settlement_guarantee(pair.market_a)["status"])
        guarantee_b = str(assess_settlement_guarantee(pair.market_b)["status"])
        if guarantee_a != GuaranteeStatus.GUARANTEED.value or guarantee_b != GuaranteeStatus.GUARANTEED.value:
            continue
        created = await store.save_validation_case(
            {
                "pair_id": pair.pair_id,
                "source_kind": source_kind,
                "decision_status": pair.status.value,
                "guarantee_a": guarantee_a,
                "guarantee_b": guarantee_b,
                "tracking_status": "AWAITING_SETTLEMENT",
                "payload": {"pair": pair.model_dump(mode="json")},
            }
        )
        new_cases += int(created)
    return {
        "markets_observed": evidence["observed"],
        "new_evidence_versions": evidence["new_versions"],
        "new_validation_cases": new_cases,
    }


async def reconcile_validation_cases(
    store: AtlasStore,
    kalshi_venue: object,
    polymarket_venue: object,
    limit: int = 20,
    now: datetime | None = None,
) -> dict[str, int]:
    now = now or datetime.now(UTC)
    summary = {"checked": 0, "pending": 0, "resolved": 0, "labeled": 0, "errors": 0}
    for case in await store.pending_validation_cases(limit):
        pair = ContractPair.model_validate(case["payload"]["pair"])
        if not _case_due(pair, case.get("last_checked_at"), now):
            summary["pending"] += 1
            continue
        try:
            market_a = await kalshi_venue.get_market(pair.market_a.venue_market_id)
            market_b = await polymarket_venue.get_market(pair.market_b.venue_market_id)
        except (httpx.HTTPError, KeyError, StopIteration, ValueError):
            await store.mark_validation_checked(pair.pair_id)
            summary["errors"] += 1
            continue
        market_b = await _apply_final_settlement(market_b, polymarket_venue)
        summary["checked"] += 1
        await store.save_market_evidence_snapshots(
            [
                market_evidence_snapshot(market_a, "SETTLEMENT_CHECK"),
                market_evidence_snapshot(market_b, "SETTLEMENT_CHECK"),
            ]
        )
        outcome_a, outcome_b = settled_outcome(market_a), settled_outcome(market_b)
        if (
            market_a.status is not MarketStatus.SETTLED
            or market_b.status is not MarketStatus.SETTLED
            or outcome_a is None
            or outcome_b is None
        ):
            await store.mark_validation_checked(pair.pair_id)
            summary["pending"] += 1
            continue

        pair.market_a, pair.market_b = market_a, market_b
        relationship_status, trusted_label = _resolution_label(pair, outcome_a, outcome_b)
        resolved_at = datetime.now(UTC).isoformat()
        evidence = {
            "settlement_verified": trusted_label is not None,
            "settlement_status": "SETTLED",
            "relationship_status": relationship_status,
            "outcome_a": outcome_a,
            "outcome_b": outcome_b,
            "resolved_at": resolved_at,
        }
        await store.save_validation_outcome(
            {
                "pair_id": pair.pair_id,
                "resolved_at": resolved_at,
                "relationship_status": relationship_status,
                "outcome_a": outcome_a,
                "outcome_b": outcome_b,
                "trusted_label": trusted_label,
                "evidence": evidence,
            }
        )
        if trusted_label:
            await record_verified_pair(
                store, pair, trusted_label, settlement_evidence=evidence
            )
            summary["labeled"] += 1
        summary["resolved"] += 1
    return summary


async def _apply_final_settlement(market: Market, venue: object) -> Market:
    """Apply an explicit final binary settlement response; leave absent data pending."""
    if market.status is MarketStatus.SETTLED and settled_outcome(market) is not None:
        return market
    get_settlement = getattr(venue, "get_settlement", None)
    if not callable(get_settlement):
        return market
    try:
        payload = await get_settlement(market.venue_market_id)
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return market
    value = payload.get("settlement") if isinstance(payload, dict) else None
    try:
        settlement = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return market
    if settlement not in {Decimal(0), Decimal(1)}:
        return market
    market.raw_market_json["settlement_value"] = (
        "yes" if settlement == Decimal(1) else "no"
    )
    market.raw_market_json["settlement_evidence"] = payload
    market.status = MarketStatus.SETTLED
    return market


def _case_due(
    pair: ContractPair, last_checked_at: object, now: datetime
) -> bool:
    dates = [
        value
        for value in (
            pair.market_a.close_time,
            pair.market_a.resolution_time,
            pair.market_b.close_time,
            pair.market_b.resolution_time,
        )
        if value is not None
    ]
    if dates and max(dates) > now:
        return False
    if last_checked_at:
        checked = datetime.fromisoformat(str(last_checked_at))
        return now - checked >= timedelta(hours=1)
    return True


def _resolution_label(
    pair: ContractPair, outcome_a: str, outcome_b: str
) -> tuple[str, str | None]:
    inverse = pair.status is MatchStatus.APPROVED_INVERSE
    agrees = outcome_a != outcome_b if inverse else outcome_a == outcome_b
    if pair.status in {MatchStatus.APPROVED_EQUIVALENT, MatchStatus.APPROVED_INVERSE}:
        return (
            ("CONFIRMED", "APPROVED_EQUIVALENT")
            if agrees
            else ("DIVERGED", "REJECTED")
        )
    if not agrees:
        return "DIVERGED", "REJECTED"
    return "INCONCLUSIVE", None
