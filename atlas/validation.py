import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256

import httpx

from atlas.fingerprints import build_fingerprint
from atlas.learning import record_verified_pair
from atlas.models import ContractPair, Market, MarketStatus, MatchStatus
from atlas.outcomes import settled_outcome
from atlas.policy_evidence import parse_market_policy_evidence
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.settlement_polling import (
    PendingReasonCode,
    plan_settlement_polls,
    retry_delay,
)
from atlas.storage import AtlasStore
from atlas.venues.base import normalize_settled_at
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
    cases = await store.pending_validation_cases(
        limit=max(limit * 4, 20), due_only=True, now=now
    )
    pairs_by_id = {
        str(case["pair_id"]): ContractPair.model_validate(case["payload"]["pair"])
        for case in cases
    }
    poll_plan = plan_settlement_polls(
        pairs_by_id.values(),
        now=now,
        last_checked_at={
            str(case["pair_id"]): case.get("last_checked_at") for case in cases
        },
    )
    case_by_id = {str(case["pair_id"]): case for case in cases}
    for decision in poll_plan.pending:
        await store.update_validation_pending(
            decision.pair_id,
            pending_reason="|".join(str(reason) for reason in decision.pending_reason_codes),
            next_poll_at=decision.next_poll_at.isoformat(),
        )

    # Cases created by older catalog snapshots may lack both timing fields. They
    # are still worth one immediate evidence attempt; if that attempt is not
    # terminal, the venue-specific pending reason becomes the durable schedule
    # signal. The planner keeps MISSING_SETTLEMENT_TIMING visible for the API.
    ready_decisions = list(poll_plan.ready)
    ready_decisions.extend(
        decision
        for decision in poll_plan.pending
        if decision.pending_reason_codes == (PendingReasonCode.MISSING_SETTLEMENT_TIMING,)
    )
    for decision in ready_decisions[:limit]:
        case = case_by_id.get(decision.pair_id)
        if case is None:
            summary["errors"] += 1
            continue
        pair = ContractPair.model_validate(case["payload"]["pair"])
        try:
            market_a = await kalshi_venue.get_market(pair.market_a.venue_market_id)
            market_b = await polymarket_venue.get_market(pair.market_b.venue_market_id)
        except (httpx.HTTPError, KeyError, ValueError):
            await store.mark_validation_checked(
                pair.pair_id,
                pending_reason=PendingReasonCode.VENUE_EVIDENCE_UNAVAILABLE.value,
                next_poll_at=(now + retry_delay(int(case.get("retry_count", 0)))).isoformat(),
                increment_retry=True,
            )
            summary["errors"] += 1
            continue
        market_a = await _apply_terminal_settlement(market_a, kalshi_venue)
        market_b = await _apply_terminal_settlement(market_b, polymarket_venue)
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
            pending_reason = "|".join(
                _pending_evidence_reasons(market_a, market_b)
            ) or PendingReasonCode.TERMINAL_EVIDENCE_MISSING.value
            failures = _pending_evidence_failures(market_a, market_b)
            retry_count = int(case.get("retry_count", 0))
            if failures:
                # A failed evidence request counts against the retry budget;
                # only a non-retryable failure can exhaust it.
                exhausted = any(
                    failure.get("retryable") is False for failure in failures
                ) and retry_count + 1 >= int(case.get("max_retries", 5))
                await store.mark_validation_checked(
                    pair.pair_id,
                    "EVIDENCE_EXHAUSTED" if exhausted else "AWAITING_SETTLEMENT",
                    pending_reason=pending_reason,
                    next_poll_at=(now + retry_delay(retry_count)).isoformat(),
                    increment_retry=True,
                )
            else:
                # A clean "not settled yet" answer is not a failure: reset the
                # consecutive-failure counter and poll again at the base delay.
                await store.mark_validation_checked(
                    pair.pair_id,
                    pending_reason=pending_reason,
                    next_poll_at=(now + retry_delay(0)).isoformat(),
                    retry_count=0,
                )
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
            "kalshi_evidence": market_a.raw_market_json.get("settlement_evidence"),
            "polymarket_evidence": market_b.raw_market_json.get("settlement_evidence"),
            "resolved_at": resolved_at,
        }
        lag = settlement_lag_observation(market_a, market_b)
        evidence.update(lag)
        await store.mark_validation_checked(pair.pair_id, retry_count=0)
        await store.save_validation_outcome(
            {
                "pair_id": pair.pair_id,
                "resolved_at": resolved_at,
                "relationship_status": relationship_status,
                "outcome_a": outcome_a,
                "outcome_b": outcome_b,
                "trusted_label": trusted_label,
                "evidence": evidence,
                **lag,
            }
        )
        if trusted_label:
            await record_verified_pair(
                store, pair, trusted_label, settlement_evidence=evidence
            )
            summary["labeled"] += 1
        summary["resolved"] += 1
    return summary


def _leg_settled_at(market: Market) -> str | None:
    """Best available settlement timestamp for one settled leg, or ``None``.

    Prefers the cross-venue ``settled_at`` key the adapters now normalize, and
    falls back to the venue-specific raw fields so evidence recorded before
    that key existed still yields a timestamp.
    """
    evidence = market.raw_market_json.get("settlement_evidence")
    candidates: list[object] = []
    if isinstance(evidence, dict):
        candidates.extend(
            evidence.get(key) for key in ("settled_at", "settlement_ts", "closed_time")
        )
    candidates.append(market.raw_market_json.get("settlement_ts"))
    for candidate in candidates:
        normalized = normalize_settled_at(candidate)
        if normalized:
            return normalized
    return None


def settlement_lag_observation(market_a: Market, market_b: Market) -> dict[str, object]:
    """Measure the settlement-timing asymmetry between two settled legs.

    Sign convention: ``settlement_lag_seconds`` is
    ``market_b.settled_at - market_a.settled_at``. It is POSITIVE when the
    FIRST leg settled EARLIER — the reconciler always passes the Kalshi leg
    first, so a positive lag means Kalshi settled ahead of Polymarket (the
    early media-consensus direction). It is negative when Polymarket settled
    first, and ``None`` when either timestamp is missing or unparseable.

    Observability only: nothing here participates in the relationship status
    or the trusted-label decision, and a missing timestamp never raises.
    """
    left, right = _leg_settled_at(market_a), _leg_settled_at(market_b)
    observation: dict[str, object] = {
        "kalshi_settled_at": left,
        "polymarket_settled_at": right,
        "settlement_lag_seconds": None,
        "first_settled_venue": None,
        "settled_same_day": None,
    }
    if left is None or right is None:
        return observation
    try:
        parsed_a, parsed_b = datetime.fromisoformat(left), datetime.fromisoformat(right)
    except ValueError:
        return observation
    lag = (parsed_b - parsed_a).total_seconds()
    observation["settlement_lag_seconds"] = lag
    observation["settled_same_day"] = parsed_a.date() == parsed_b.date()
    if lag > 0:
        observation["first_settled_venue"] = market_a.venue.value
    elif lag < 0:
        observation["first_settled_venue"] = market_b.venue.value
    return observation


async def _apply_terminal_settlement(market: Market, venue: object) -> Market:
    """Refresh one leg from authoritative terminal evidence when available.

    Venue adapters may expose a stronger ``get_terminal_settlement_evidence``
    method (for example, a resolved market endpoint or a final order-book
    marker). The older ``get_settlement`` method remains a compatibility
    fallback. A binary value alone is never enough: the adapter must return a
    structured evidence object that can be persisted with the validation
    outcome.
    """
    existing = settled_outcome(market)
    if existing is not None and market.raw_market_json.get("settlement_evidence"):
        return market
    get_terminal = getattr(venue, "get_terminal_settlement_evidence", None)
    get_settlement = getattr(venue, "get_settlement", None)
    fetch = get_terminal if callable(get_terminal) else get_settlement
    if not callable(fetch):
        return market
    try:
        payload = await fetch(market.venue_market_id)
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return market
    if not isinstance(payload, dict):
        return market
    if payload.get("status") == "pending":
        market.raw_market_json["settlement_evidence"] = payload
        return market
    value = payload.get("settlement")
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


def _pending_evidence_reasons(*markets: Market) -> list[str]:
    reasons: list[str] = []
    for market in markets:
        evidence = market.raw_market_json.get("settlement_evidence")
        if isinstance(evidence, dict) and evidence.get("status") == "pending":
            reason = evidence.get("reason")
            if reason:
                reasons.append(f"{market.venue.value}:{reason}")
    return reasons


# Pending-evidence reasons that mean "the request failed", as opposed to "the
# venue answered and the market is simply not settled yet". Only failures
# consume the bounded retry budget; see ``classify_terminal_error``.
_EVIDENCE_FAILURE_REASONS = {
    "rate_limited",
    "venue_server_error",
    "venue_client_error",
    "request_timeout",
    "network_error",
    "venue_request_error",
}


def _pending_evidence_failures(*markets: Market) -> list[dict[str, object]]:
    """Return pending settlement evidence whose reason is a request failure."""
    failures: list[dict[str, object]] = []
    for market in markets:
        evidence = market.raw_market_json.get("settlement_evidence")
        if (
            isinstance(evidence, dict)
            and evidence.get("status") == "pending"
            and str(evidence.get("reason")) in _EVIDENCE_FAILURE_REASONS
        ):
            failures.append(evidence)
    return failures


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
