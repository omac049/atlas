import asyncio
from collections import Counter

import httpx

from atlas.fingerprints import build_fingerprint
from atlas.models import ContractFingerprint, ContractPair, Market
from atlas.policy_evidence import (
    assess_policy_compatibility,
    parse_market_policy_evidence,
)
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.storage import AtlasStore
from atlas.validation import market_evidence_snapshot
from atlas.verification import verify_equivalence


def weather_rule_targets(
    kalshi_markets: list[Market],
    polymarket_markets: list[Market],
    limit: int = 64,
) -> list[tuple[Market, Market]]:
    """Select one closest contract pair per shared weather measurement event."""
    left = _weather_groups(kalshi_markets)
    right = _weather_groups(polymarket_markets)
    targets: list[tuple[Market, Market]] = []
    for event_key in sorted(left.keys() & right.keys()):
        left_by_shape = _by_shape(left[event_key])
        right_by_shape = _by_shape(right[event_key])
        shared_shapes = sorted(left_by_shape.keys() & right_by_shape.keys(), key=repr)
        if shared_shapes:
            shape = shared_shapes[0]
            targets.append((left_by_shape[shape][0][0], right_by_shape[shape][0][0]))
        else:
            targets.append((left[event_key][0][0], right[event_key][0][0]))
        if len(targets) >= limit:
            break
    return targets


def shared_rule_targets(
    kalshi_markets: list[Market],
    polymarket_markets: list[Market],
    limit: int = 24,
    exclude_market_types: set[str] | None = None,
) -> list[tuple[Market, Market]]:
    """Select bounded detail-refresh targets across shared event families."""
    return shared_rule_target_report(
        kalshi_markets,
        polymarket_markets,
        limit=limit,
        exclude_market_types=exclude_market_types,
    )["targets"]


def shared_rule_target_report(
    kalshi_markets: list[Market],
    polymarket_markets: list[Market],
    limit: int = 24,
    exclude_market_types: set[str] | None = None,
) -> dict[str, object]:
    """Return refresh targets plus deterministic exclusions for known bad settlement."""
    excluded = exclude_market_types or set()
    left_groups: dict[tuple[str, str, str], list[Market]] = {}
    right_groups: dict[tuple[str, str, str], list[Market]] = {}
    for market in kalshi_markets:
        fingerprint = build_fingerprint(market)
        if fingerprint.market_type in excluded:
            continue
        key = (
            fingerprint.event_subject,
            fingerprint.event_action,
            fingerprint.market_type or "unknown",
        )
        left_groups.setdefault(key, []).append(market)
    for market in polymarket_markets:
        fingerprint = build_fingerprint(market)
        if fingerprint.market_type in excluded:
            continue
        key = (
            fingerprint.event_subject,
            fingerprint.event_action,
            fingerprint.market_type or "unknown",
        )
        right_groups.setdefault(key, []).append(market)

    shared_keys = sorted(left_groups.keys() & right_groups.keys())
    shared_keys.sort(key=lambda key: (key[2], key[0], key[1]))
    targets: list[tuple[Market, Market]] = []
    skipped_non_guaranteed = 0
    for key in shared_keys:
        left = min(left_groups[key], key=lambda market: market.market_id)
        right = min(right_groups[key], key=lambda market: market.market_id)
        if (
            assess_settlement_guarantee(left)["status"]
            == GuaranteeStatus.NON_GUARANTEED.value
            or assess_settlement_guarantee(right)["status"]
            == GuaranteeStatus.NON_GUARANTEED.value
        ):
            skipped_non_guaranteed += 1
            continue
        targets.append((left, right))
        if len(targets) >= max(limit, 0):
            break
    return {
        "targets": targets,
        "shared_events_available": len(shared_keys),
        "skipped_non_guaranteed": skipped_non_guaranteed,
    }


async def enrich_shared_rules(
    store: AtlasStore,
    kalshi_venue: object,
    polymarket_venue: object,
    kalshi_markets: list[Market],
    polymarket_markets: list[Market],
    limit: int = 24,
    concurrency: int = 8,
    exclude_market_types: set[str] | None = None,
) -> dict[str, object]:
    """Refresh bounded non-specialist shared events without inferring policy terms."""
    target_report = shared_rule_target_report(
        kalshi_markets,
        polymarket_markets,
        limit=limit,
        exclude_market_types=exclude_market_types,
    )
    targets = target_report["targets"]
    semaphore = asyncio.Semaphore(concurrency)

    async def refresh_pair(
        left: Market, right: Market
    ) -> tuple[Market, Market] | None:
        async with semaphore:
            try:
                detailed_left, detailed_right = await asyncio.gather(
                    kalshi_venue.get_market(left.venue_market_id),
                    polymarket_venue.get_market(right.venue_market_id),
                )
                enrich_source = getattr(kalshi_venue, "enrich_market_source", None)
                if callable(enrich_source):
                    detailed_left = await enrich_source(detailed_left)
            except (httpx.HTTPError, KeyError, StopIteration, ValueError):
                return None
        _replace_market(left, detailed_left, "market_detail+event_source")
        _replace_market(right, detailed_right, "market_detail")
        return left, right

    refreshed = await asyncio.gather(
        *(refresh_pair(left, right) for left, right in targets)
    )
    pairs = [pair for pair in refreshed if pair is not None]
    evidence = await store.save_market_evidence_snapshots(
        [
            market_evidence_snapshot(market, "SHARED_RULE_ENRICHMENT")
            for pair in pairs
            for market in pair
        ]
    )
    complete_pairs = 0
    policy_blockers: Counter[str] = Counter()
    for left, right in pairs:
        left_policy = parse_market_policy_evidence(left)
        right_policy = parse_market_policy_evidence(right)
        complete_pairs += int(left_policy.complete and right_policy.complete)
        assessment = assess_policy_compatibility(left_policy, right_policy)
        policy_blockers.update(assessment.blockers)
        policy_blockers.update(assessment.mismatch_codes)
    return {
        "shared_events_considered": len(targets),
        "shared_events_available": target_report["shared_events_available"],
        "shared_events_skipped_non_guaranteed": target_report["skipped_non_guaranteed"],
        "pairs_refreshed": len(pairs),
        "markets_refreshed": len(pairs) * 2,
        "detail_failures": len(targets) - len(pairs),
        "evidence_observed": evidence["observed"],
        "new_evidence_versions": evidence["new_versions"],
        "complete_policy_pairs": complete_pairs,
        "policy_blocker_counts": dict(
            sorted(policy_blockers.items(), key=lambda item: (-item[1], item[0]))
        ),
    }


async def enrich_weather_rules(
    store: AtlasStore,
    kalshi_venue: object,
    polymarket_venue: object,
    kalshi_markets: list[Market],
    polymarket_markets: list[Market],
    limit: int = 64,
    concurrency: int = 8,
) -> dict[str, object]:
    """Refresh shared weather rules and persist evidence without inferring missing terms."""
    targets = weather_rule_targets(kalshi_markets, polymarket_markets, limit)
    semaphore = asyncio.Semaphore(concurrency)

    async def refresh_pair(
        left: Market, right: Market
    ) -> tuple[Market, Market] | None:
        async with semaphore:
            try:
                detailed_left, detailed_right = await asyncio.gather(
                    kalshi_venue.get_market(left.venue_market_id),
                    polymarket_venue.get_market(right.venue_market_id),
                )
                detailed_left = await kalshi_venue.enrich_market_source(detailed_left)
            except (httpx.HTTPError, KeyError, StopIteration, ValueError):
                return None
        _replace_market(left, detailed_left, "market_detail+event_source")
        _replace_market(right, detailed_right, "market_detail")
        return left, right

    refreshed = await asyncio.gather(
        *(refresh_pair(left, right) for left, right in targets)
    )
    pairs = [pair for pair in refreshed if pair is not None]
    evidence = await store.save_market_evidence_snapshots(
        [
            market_evidence_snapshot(market, "WEATHER_RULE_ENRICHMENT")
            for pair in pairs
            for market in pair
        ]
    )
    blockers: Counter[str] = Counter()
    guaranteed_pairs = 0
    exact_pairs = 0
    cohort_items: list[dict[str, object]] = []
    policy_blockers: Counter[str] = Counter()
    for index, (left, right) in enumerate(pairs):
        pair = verify_equivalence(left, right, f"weather-enrichment:{index}")
        blockers.update(pair.differences)
        left_guarantee = assess_settlement_guarantee(left)
        right_guarantee = assess_settlement_guarantee(right)
        guarantees = {left_guarantee["status"], right_guarantee["status"]}
        guaranteed_pairs += int(guarantees == {GuaranteeStatus.GUARANTEED.value})
        exact_pairs += int(pair.status.value == "APPROVED_EQUIVALENT")
        cohort_items.append(
            _validation_cohort_item(
                left,
                right,
                pair,
                left_guarantee,
                right_guarantee,
            )
        )
        policy_assessment = assess_policy_compatibility(
            parse_market_policy_evidence(left),
            parse_market_policy_evidence(right),
        )
        policy_blockers.update(policy_assessment.blockers)
        policy_blockers.update(policy_assessment.mismatch_codes)
    return {
        "shared_events_considered": len(targets),
        "pairs_refreshed": len(pairs),
        "markets_refreshed": len(pairs) * 2,
        "detail_failures": len(targets) - len(pairs),
        "evidence_observed": evidence["observed"],
        "new_evidence_versions": evidence["new_versions"],
        "guaranteed_pairs": guaranteed_pairs,
        "exact_rule_matches": exact_pairs,
        "unresolved_pairs": len(pairs) - exact_pairs,
        "blocker_counts": dict(
            sorted(blockers.items(), key=lambda item: (-item[1], item[0]))
        ),
        "policy_blocker_counts": dict(
            sorted(policy_blockers.items(), key=lambda item: (-item[1], item[0]))
        ),
        "validation_cohort": {
            "status": (
                "READY_FOR_SETTLEMENT_TRACKING"
                if any(item["eligible"] for item in cohort_items)
                else "RULE_EVIDENCE_BLOCKED"
                if cohort_items
                else "NO_SHARED_WEATHER_EVENT"
            ),
            "events": len(cohort_items),
            "eligible_events": sum(bool(item["eligible"]) for item in cohort_items),
            "blocked_events": sum(not bool(item["eligible"]) for item in cohort_items),
            "items": cohort_items,
        },
    }


def _validation_cohort_item(
    left: Market,
    right: Market,
    pair: ContractPair,
    left_guarantee: dict[str, object],
    right_guarantee: dict[str, object],
) -> dict[str, object]:
    fingerprint = build_fingerprint(left)
    left_policy = parse_market_policy_evidence(left)
    right_policy = parse_market_policy_evidence(right)
    policy_compatibility = assess_policy_compatibility(left_policy, right_policy)
    both_guaranteed = {
        left_guarantee["status"],
        right_guarantee["status"],
    } == {GuaranteeStatus.GUARANTEED.value}
    exact = pair.status.value in {"APPROVED_EQUIVALENT", "APPROVED_INVERSE"}
    eligible = both_guaranteed and exact
    if not both_guaranteed:
        stage = "SETTLEMENT_EVIDENCE_BLOCKED"
        next_gate = "PROVE_EXPLICIT_SETTLEMENT_AND_EXCEPTION_POLICIES"
    elif not exact:
        stage = "CONTRACT_RULES_BLOCKED"
        next_gate = "CLEAR_ALL_DETERMINISTIC_RULE_MISMATCHES"
    else:
        stage = "ELIGIBLE_FOR_VALIDATION"
        next_gate = "OPEN_PAPER_ONLY_CASE_AND_AWAIT_BOTH_SETTLEMENTS"
    return {
        "event_subject": fingerprint.event_subject,
        "event_date": fingerprint.event_date,
        "market_type": fingerprint.market_type,
        "kalshi_market_id": left.market_id,
        "polymarket_market_id": right.market_id,
        "kalshi_title": left.title,
        "polymarket_title": right.title,
        "decision_status": pair.status.value,
        "eligible": eligible,
        "stage": stage,
        "next_gate": next_gate,
        "mismatch_codes": list(pair.differences),
        "kalshi_guarantee": left_guarantee,
        "polymarket_guarantee": right_guarantee,
        "policy_compatibility": policy_compatibility.model_dump(mode="json"),
        "kalshi_policy_evidence": left_policy.model_dump(mode="json"),
        "polymarket_policy_evidence": right_policy.model_dump(mode="json"),
    }


def _weather_groups(
    markets: list[Market],
) -> dict[tuple[str, str], list[tuple[Market, ContractFingerprint]]]:
    groups: dict[tuple[str, str], list[tuple[Market, ContractFingerprint]]] = {}
    for market in markets:
        fingerprint = build_fingerprint(market)
        if fingerprint.market_type != "weather":
            continue
        key = (fingerprint.event_subject, fingerprint.event_action)
        groups.setdefault(key, []).append((market, fingerprint))
    for values in groups.values():
        values.sort(key=lambda item: item[0].market_id)
    return groups


def _by_shape(
    markets: list[tuple[Market, ContractFingerprint]],
) -> dict[tuple, list[tuple[Market, ContractFingerprint]]]:
    groups: dict[tuple, list[tuple[Market, ContractFingerprint]]] = {}
    for market, fingerprint in markets:
        groups.setdefault(_weather_shape(fingerprint), []).append((market, fingerprint))
    return groups


def _weather_shape(fingerprint: ContractFingerprint) -> tuple:
    return (
        fingerprint.event_action,
        fingerprint.contract_scope,
        fingerprint.threshold,
        fingerprint.threshold_upper,
        fingerprint.threshold_operator,
        fingerprint.threshold_unit,
        fingerprint.measurement_period,
        fingerprint.resolution_source,
    )


def _replace_market(target: Market, detailed: Market, evidence_source: str) -> None:
    for field in Market.model_fields:
        setattr(target, field, getattr(detailed, field))
    target.raw_market_json["_atlas_rule_evidence_source"] = evidence_source
