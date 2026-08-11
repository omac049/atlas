import re
from collections import Counter
from datetime import datetime

from atlas.fingerprints import build_fingerprint, deterministic_settlement_blockers
from atlas.models import ContractFingerprint, ContractPair, Market, MarketStatus
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.verification import verify_equivalence


def is_live_market(market: Market) -> bool:
    return market.status is MarketStatus.ACTIVE and market.venue_market_id not in {"", "None"}


def filter_live_markets(markets: list[Market]) -> list[Market]:
    return [market for market in markets if is_live_market(market)]


def markets_requiring_source_enrichment(
    market_a: list[Market], market_b: list[Market]
) -> list[Market]:
    """Return left-side same-event candidates whose authoritative source is absent."""
    right_events = {
        _fingerprint_event_key(fingerprint)
        for market in filter_live_markets(market_b)
        for fingerprint in (build_fingerprint(market),)
    }
    candidates: list[Market] = []
    for market in filter_live_markets(market_a):
        fingerprint = build_fingerprint(market)
        if (
            _fingerprint_event_key(fingerprint) in right_events
            and fingerprint.resolution_source in {"", "unknown"}
        ):
            candidates.append(market)
    return candidates


def markets_requiring_family_source_enrichment(
    markets: list[Market], families: set[str] | None = None
) -> list[Market]:
    """Return source-incomplete contracts in families that need authoritative rules."""
    families = families or {"economic", "election"}
    candidates: list[Market] = []
    for market in filter_live_markets(markets):
        fingerprint = build_fingerprint(market)
        if fingerprint.market_type not in families:
            continue
        if market.resolution_source in {"", "unknown"} or fingerprint.resolution_source in {
            "",
            "unknown",
        }:
            candidates.append(market)
    return candidates


def scan_market_pairs(
    market_a: list[Market], market_b: list[Market]
) -> list[ContractPair]:
    """Compare live market candidates using the deterministic rule verifier."""
    pairs: list[ContractPair] = []
    right_by_key: dict[tuple, list[Market]] = {}
    for right in filter_live_markets(market_b):
        right_by_key.setdefault(_verification_key(right), []).append(right)
    for left in filter_live_markets(market_a):
        for right in right_by_key.get(_verification_key(left), []):
            pair_id = f"{left.market_id}::{right.market_id}"
            pairs.append(verify_equivalence(left, right, pair_id))
    return pairs


def _verification_key(market: Market) -> tuple:
    return _fingerprint_verification_key(build_fingerprint(market))


def _fingerprint_verification_key(fingerprint: ContractFingerprint) -> tuple:
    return tuple(
        getattr(fingerprint, field)
        for field in (
            "event_subject", "event_action", "contract_scope", "affirmative_outcome",
            "signed_line", "settlement_policy",
            "threshold", "threshold_upper", "threshold_operator",
            "threshold_unit", "measurement_period", "resolution_source", "revision_policy",
        )
    ) + (tuple(fingerprint.participants), fingerprint.market_type)


_STOPWORDS = {
    "will", "be", "the", "over", "under", "more", "than", "score", "scored",
    "wins", "win", "by", "yes", "no", "to", "a", "an", "or", "and", "in", "on",
}


def propose_market_pairs(
    market_a: list[Market], market_b: list[Market], limit: int = 25
) -> list[dict[str, object]]:
    """Surface lexical candidates for human/AI review; never approves them."""
    left = filter_live_markets(market_a)
    right = filter_live_markets(market_b)
    index: dict[str, list[Market]] = {}
    for market in right:
        for token in _tokens(market.title):
            index.setdefault(token, []).append(market)
    proposals: list[dict[str, object]] = []
    # Candidate generation is a bounded proposal pass; deterministic matching still scans the full catalogs.
    for candidate in left[:5000]:
        candidate_tokens = _tokens(candidate.title)
        indexed_tokens = [token for token in candidate_tokens if index.get(token)]
        if not indexed_tokens:
            continue
        seed = min(indexed_tokens, key=lambda token: len(index[token]))
        possible = {market.market_id: market for market in index[seed][:500]}
        for market in possible.values():
            shared = candidate_tokens & _tokens(market.title)
            union = candidate_tokens | _tokens(market.title)
            score = len(shared) / len(union) if union else 0
            if len(shared) >= 2 and score >= 0.35:
                proposals.append({
                    "kalshi_market_id": candidate.market_id,
                    "polymarket_market_id": market.market_id,
                    "kalshi_title": candidate.title,
                    "polymarket_title": market.title,
                    "score": round(score, 4),
                    "shared_terms": sorted(shared),
                    "status": "REVIEW_REQUIRED",
                    "deterministic_settlement": not (
                        deterministic_settlement_blockers(candidate)
                        or deterministic_settlement_blockers(market)
                    ),
                    "settlement_blockers": _pair_settlement_blockers(candidate, market),
                })
    proposals.sort(
        key=lambda item: (
            bool(item["deterministic_settlement"]),
            item["score"],
            len(item["shared_terms"]),
        ),
        reverse=True,
    )
    return proposals[:limit]


def review_market_pairs(
    market_a: list[Market], market_b: list[Market], limit: int = 25
) -> list[dict[str, object]]:
    """Explain why contracts on the same event are not deterministic equivalents."""
    right_by_event: dict[tuple[str, str, str], list[Market]] = {}
    for market in filter_live_markets(market_b):
        fingerprint = build_fingerprint(market)
        right_by_event.setdefault(
            _fingerprint_event_key(fingerprint), []
        ).append(market)

    reviews: list[dict[str, object]] = []
    for left in filter_live_markets(market_a):
        fingerprint = build_fingerprint(left)
        event_key = _fingerprint_event_key(fingerprint)
        for right in right_by_event.get(event_key, []):
            pair = verify_equivalence(
                left, right, f"{left.market_id}::{right.market_id}"
            )
            if not pair.decision or not pair.decision.mismatch_codes:
                continue
            reviews.append(
                {
                    "kalshi_market_id": left.market_id,
                    "polymarket_market_id": right.market_id,
                    "kalshi_title": left.title,
                    "polymarket_title": right.title,
                    "score": round(
                        max(0, 1 - len(pair.decision.mismatch_codes) / 10), 4
                    ),
                    "shared_terms": sorted(_tokens(left.title) & _tokens(right.title)),
                    "status": "REVIEW_REQUIRED",
                    "review_kind": "SAME_EVENT_DIFFERENT_CONTRACT",
                    "mismatch_codes": pair.decision.mismatch_codes,
                    "kalshi_terms": _review_terms(pair.decision.fingerprint_a),
                    "polymarket_terms": _review_terms(pair.decision.fingerprint_b),
                    "deterministic_settlement": not (
                        deterministic_settlement_blockers(left)
                        or deterministic_settlement_blockers(right)
                    ),
                    "settlement_blockers": _pair_settlement_blockers(left, right),
                }
            )
    reviews.sort(
        key=lambda item: (
            not bool(item["deterministic_settlement"]),
            len(item["mismatch_codes"]),
            -float(item["score"]),
            item["kalshi_title"],
            item["polymarket_title"],
        )
    )
    return reviews[:limit]


_STRUCTURED_IDENTITY_FAMILIES = {"economic", "weather", "election", "crypto"}


def structured_identity_candidates(
    market_a: list[Market], market_b: list[Market], limit: int = 25
) -> list[dict[str, object]]:
    """Surface complete structural event aliases for review; never approve them."""
    right_by_identity: dict[
        tuple[str, str, str, str, str],
        list[tuple[Market, ContractFingerprint]],
    ] = {}
    for market in filter_live_markets(market_b):
        fingerprint = build_fingerprint(market)
        identity_key = _structured_identity_key(fingerprint)
        if identity_key is not None:
            right_by_identity.setdefault(identity_key, []).append((market, fingerprint))

    candidates: list[dict[str, object]] = []
    for left in filter_live_markets(market_a):
        left_fingerprint = build_fingerprint(left)
        identity_key = _structured_identity_key(left_fingerprint)
        if identity_key is None:
            continue
        for right, right_fingerprint in right_by_identity.get(identity_key, []):
            if left_fingerprint.event_subject == right_fingerprint.event_subject:
                continue
            pair = verify_equivalence(left, right, f"{left.market_id}::{right.market_id}")
            mismatches = pair.decision.mismatch_codes if pair.decision else pair.differences
            candidates.append(
                {
                    "kalshi_market_id": left.market_id,
                    "polymarket_market_id": right.market_id,
                    "kalshi_title": left.title,
                    "polymarket_title": right.title,
                    "score": round(max(0, 1 - len(mismatches) / 10), 4),
                    "shared_terms": sorted(_tokens(left.title) & _tokens(right.title)),
                    "status": "REVIEW_REQUIRED",
                    "review_kind": "STRUCTURED_IDENTITY_REVIEW",
                    "identity_tier": "STRUCTURED_EVENT_IDENTITY",
                    "identity_reasons": [
                        "FAMILY_MATCH",
                        "ACTION_MATCH",
                        "SCOPE_MATCH",
                        "ANCHOR_MATCH",
                        "DATE_MATCH",
                    ],
                    "identity_key": {
                        "market_type": identity_key[0],
                        "event_action": identity_key[1],
                        "contract_scope": identity_key[2],
                        "geography": identity_key[3],
                        "event_date": identity_key[4],
                    },
                    "mismatch_codes": mismatches,
                    "kalshi_terms": _review_terms(left_fingerprint),
                    "polymarket_terms": _review_terms(right_fingerprint),
                    "deterministic_settlement": not (
                        deterministic_settlement_blockers(left)
                        or deterministic_settlement_blockers(right)
                    ),
                    "settlement_blockers": _pair_settlement_blockers(left, right),
                }
            )
    candidates.sort(
        key=lambda item: (
            not bool(item["deterministic_settlement"]),
            len(item["mismatch_codes"]),
            -float(item["score"]),
            str(item["kalshi_title"]),
            str(item["polymarket_title"]),
        )
    )
    return candidates[:limit]


def _review_terms(fingerprint: object) -> dict[str, str]:
    return {
        field: str(value)
        for field in (
            "event_subject",
            "event_action",
            "event_date",
            "threshold",
            "threshold_upper",
            "threshold_operator",
            "threshold_unit",
            "contract_scope",
            "affirmative_outcome",
            "signed_line",
            "settlement_policy",
            "measurement_period",
            "geography",
            "resolution_source",
            "revision_policy",
            "market_type",
        )
        if (value := getattr(fingerprint, field)) is not None
    }


def compatibility_report(market_a: list[Market], market_b: list[Market]) -> dict[str, object]:
    left = filter_live_markets(market_a)
    right = filter_live_markets(market_b)
    left_fingerprints = [(market, build_fingerprint(market)) for market in left]
    right_fingerprints = [(market, build_fingerprint(market)) for market in right]
    left_families = {fingerprint.market_type for _, fingerprint in left_fingerprints}
    right_families = {fingerprint.market_type for _, fingerprint in right_fingerprints}
    left_events = {
        _fingerprint_event_key(fingerprint)
        for _, fingerprint in left_fingerprints
    }
    right_events = {
        _fingerprint_event_key(fingerprint)
        for _, fingerprint in right_fingerprints
    }
    left_verification_keys = {
        _fingerprint_verification_key(fingerprint)
        for _, fingerprint in left_fingerprints
    }
    right_verification_keys = {
        _fingerprint_verification_key(fingerprint)
        for _, fingerprint in right_fingerprints
    }
    left_assessments = {
        market.market_id: assess_settlement_guarantee(market, fingerprint)
        for market, fingerprint in left_fingerprints
    }
    right_assessments = {
        market.market_id: assess_settlement_guarantee(market, fingerprint)
        for market, fingerprint in right_fingerprints
    }
    return {
        "kalshi_families": sorted(left_families),
        "polymarket_families": sorted(right_families),
        "compatible_families": sorted(left_families & right_families),
        "compatible": bool(left_families & right_families),
        "event_compatible": bool(left_verification_keys & right_verification_keys),
        "shared_event_count": len(left_events & right_events),
        "election_discovery": _election_discovery(
            left_fingerprints, right_fingerprints
        ),
        "identity_discovery": _identity_discovery(
            left_fingerprints, right_fingerprints
        ),
        "normalization_coverage": _normalization_coverage(
            left_fingerprints,
            right_fingerprints,
            left_assessments,
            right_assessments,
        ),
        "settlement_discovery": _settlement_discovery(
            left_fingerprints,
            right_fingerprints,
            left_assessments,
            right_assessments,
        ),
    }


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2 and token not in _STOPWORDS}


def _pair_settlement_blockers(left: Market, right: Market) -> list[str]:
    return [
        *(f"KALSHI_{code}" for code in deterministic_settlement_blockers(left)),
        *(f"POLYMARKET_{code}" for code in deterministic_settlement_blockers(right)),
    ]


def _structured_identity_key(
    fingerprint: ContractFingerprint,
) -> tuple[str, str, str, str, str] | None:
    if fingerprint.market_type not in _STRUCTURED_IDENTITY_FAMILIES:
        return None
    values = (
        fingerprint.market_type,
        fingerprint.event_action,
        fingerprint.contract_scope,
        fingerprint.geography,
        fingerprint.event_date,
    )
    if any(not _complete_identity_value(value) for value in values):
        return None
    return tuple(str(value) for value in values)  # type: ignore[return-value]


def _complete_identity_value(value: object) -> bool:
    normalized = str(value or "").strip().lower()
    return bool(normalized) and normalized != "none" and "unknown" not in normalized


def _identity_discovery(
    left: list[tuple[Market, ContractFingerprint]],
    right: list[tuple[Market, ContractFingerprint]],
    limit: int = 12,
) -> dict[str, object]:
    left_by_key: dict[tuple[str, str, str, str, str], set[str]] = {}
    right_by_key: dict[tuple[str, str, str, str, str], set[str]] = {}
    for _, fingerprint in left:
        if identity_key := _structured_identity_key(fingerprint):
            left_by_key.setdefault(identity_key, set()).add(fingerprint.event_subject)
    for _, fingerprint in right:
        if identity_key := _structured_identity_key(fingerprint):
            right_by_key.setdefault(identity_key, set()).add(fingerprint.event_subject)

    shared_keys = sorted(left_by_key.keys() & right_by_key.keys())
    same_subject_keys = [
        key for key in shared_keys if left_by_key[key] & right_by_key[key]
    ]
    novel_alias_keys = [
        key
        for key in shared_keys
        if any(
            left_subject != right_subject
            for left_subject in left_by_key[key]
            for right_subject in right_by_key[key]
        )
    ]
    return {
        "status": (
            "NOVEL_ALIASES_REQUIRE_RULE_REVIEW"
            if novel_alias_keys
            else "STRUCTURED_EVENTS_SHARE_CANONICAL_IDENTITY"
            if shared_keys
            else "NO_COMPLETE_SHARED_IDENTITY"
        ),
        "structured_shared_keys": len(shared_keys),
        "same_subject_keys": len(same_subject_keys),
        "novel_alias_candidates": len(novel_alias_keys),
        "complete_kalshi_keys": len(left_by_key),
        "complete_polymarket_keys": len(right_by_key),
        "novel_aliases": [
            {
                "market_type": key[0],
                "event_action": key[1],
                "contract_scope": key[2],
                "geography": key[3],
                "event_date": key[4],
                "kalshi_subjects": sorted(left_by_key[key]),
                "polymarket_subjects": sorted(right_by_key[key]),
            }
            for key in novel_alias_keys[:limit]
        ],
    }


def _election_discovery(
    left: list[tuple[Market, object]], right: list[tuple[Market, object]]
) -> dict[str, object]:
    left_elections = [
        (market, fp)
        for market, fp in left
        if fp.market_type == "election" and fp.event_action == "party_control"
    ]
    right_elections = [
        (market, fp)
        for market, fp in right
        if fp.market_type == "election" and fp.event_action == "party_control"
    ]
    left_subjects = {fp.event_subject for _, fp in left_elections}
    right_subjects = {fp.event_subject for _, fp in right_elections}
    shared_subjects = sorted(left_subjects & right_subjects)

    if shared_subjects:
        status = "CROSS_VENUE_CANDIDATES_READY_FOR_RULE_CHECK"
    elif right_elections and not left_elections:
        status = "WAITING_FOR_KALSHI_COUNTERPART"
    elif left_elections and not right_elections:
        status = "WAITING_FOR_POLYMARKET_COUNTERPART"
    else:
        status = "NO_SHARED_ELECTION_EVENT"

    return {
        "status": status,
        "kalshi_contracts": len(left_elections),
        "polymarket_contracts": len(right_elections),
        "shared_events": len(shared_subjects),
        "deterministic_kalshi": sum(
            not deterministic_settlement_blockers(market)
            for market, _ in left_elections
        ),
        "deterministic_polymarket": sum(
            not deterministic_settlement_blockers(market)
            for market, _ in right_elections
        ),
        "subjects": sorted(left_subjects | right_subjects),
        "polymarket_candidates": [
            {
                "market_id": market.venue_market_id,
                "title": market.title,
                "event_subject": fp.event_subject,
                "affirmative_outcome": fp.affirmative_outcome,
                "deterministic_settlement": not deterministic_settlement_blockers(market),
                "blockers": deterministic_settlement_blockers(market),
            }
            for market, fp in right_elections[:10]
        ],
    }


def _settlement_discovery(
    left: list[tuple[Market, object]],
    right: list[tuple[Market, object]],
    left_assessments: dict[str, dict[str, object]],
    right_assessments: dict[str, dict[str, object]],
    limit: int = 12,
) -> dict[str, object]:
    left_groups: dict[
        tuple[str, str, str], list[tuple[Market, ContractFingerprint]]
    ] = {}
    right_groups: dict[
        tuple[str, str, str], list[tuple[Market, ContractFingerprint]]
    ] = {}
    for market, fingerprint in left:
        left_groups.setdefault(_fingerprint_event_key(fingerprint), []).append(
            (market, fingerprint)
        )
    for market, fingerprint in right:
        right_groups.setdefault(_fingerprint_event_key(fingerprint), []).append(
            (market, fingerprint)
        )

    rankings: list[dict[str, object]] = []
    for event_key in left_groups.keys() & right_groups.keys():
        best: dict[str, object] | None = None
        best_key: tuple[object, ...] | None = None
        for left_market, right_market in _bounded_event_pairs(
            left_groups[event_key], right_groups[event_key]
        ):
                left_status = str(left_assessments[left_market.market_id]["status"])
                right_status = str(right_assessments[right_market.market_id]["status"])
                pair_guarantee = _combined_guarantee(left_status, right_status)
                pair = verify_equivalence(
                    left_market,
                    right_market,
                    f"{left_market.market_id}::{right_market.market_id}",
                )
                mismatches = pair.decision.mismatch_codes if pair.decision else pair.differences
                rule_distance = sum(
                    code
                    not in {
                        "NON_GUARANTEED_SETTLEMENT",
                        "SETTLEMENT_GUARANTEE_UNKNOWN",
                    }
                    for code in mismatches
                )
                lifecycle_status = _lifecycle_status(left_market, right_market)
                queue_status, next_gate = _candidate_queue_state(
                    pair.status.value,
                    pair_guarantee,
                    lifecycle_status,
                )
                settlement_ready_at = _settlement_ready_at(left_market, right_market)
                left_evidence = _evidence_summary(
                    left_market,
                    left_assessments[left_market.market_id],
                )
                right_evidence = _evidence_summary(
                    right_market,
                    right_assessments[right_market.market_id],
                )
                evidence_readiness = _pair_evidence_readiness(
                    left_evidence,
                    right_evidence,
                )
                candidate_key = (
                    _queue_rank(queue_status),
                    _reachability_rank(pair_guarantee),
                    _evidence_priority(queue_status, evidence_readiness),
                    _settlement_sort_key(settlement_ready_at),
                    _guarantee_rank(pair_guarantee),
                    rule_distance,
                    int(evidence_readiness["gap"]),
                    left_market.title,
                    right_market.title,
                )
                if best_key is None or candidate_key < best_key:
                    best_key = candidate_key
                    best = {
                        "event_subject": event_key[0],
                        "market_type": event_key[1],
                        "event_action": event_key[2],
                        "guarantee_status": pair_guarantee,
                        "guarantee_reachable": _guarantee_reachable(pair_guarantee),
                        "rule_distance": rule_distance,
                        "mismatch_codes": mismatches,
                        "pair_status": pair.status.value,
                        "queue_status": queue_status,
                        "next_gate": next_gate,
                        "settlement_ready_at": settlement_ready_at,
                        "evidence_readiness": evidence_readiness,
                        "kalshi_guarantee": {
                            "status": left_status,
                            "reason_codes": left_assessments[left_market.market_id].get(
                                "reason_codes", []
                            ),
                        },
                        "kalshi_evidence": left_evidence,
                        "polymarket_guarantee": {
                            "status": right_status,
                            "reason_codes": right_assessments[right_market.market_id].get(
                                "reason_codes", []
                            ),
                        },
                        "polymarket_evidence": right_evidence,
                        "lifecycle_status": lifecycle_status,
                        "kalshi_status": left_market.status.value,
                        "polymarket_status": right_market.status.value,
                        "kalshi_resolution_time": (
                            left_market.resolution_time.isoformat()
                            if left_market.resolution_time
                            else None
                        ),
                        "polymarket_resolution_time": (
                            right_market.resolution_time.isoformat()
                            if right_market.resolution_time
                            else None
                        ),
                        "kalshi_market_id": left_market.market_id,
                        "polymarket_market_id": right_market.market_id,
                        "kalshi_title": left_market.title,
                        "polymarket_title": right_market.title,
                        "kalshi_contracts": len(left_groups[event_key]),
                        "polymarket_contracts": len(right_groups[event_key]),
                    }
        if best:
            rankings.append(best)

    rankings.sort(
        key=lambda item: (
            _queue_rank(str(item["queue_status"])),
            _reachability_rank(str(item["guarantee_status"])),
            _evidence_priority(
                str(item["queue_status"]),
                item.get("evidence_readiness", {}),
            ),
            _settlement_sort_key(item.get("settlement_ready_at")),
            _guarantee_rank(str(item["guarantee_status"])),
            int(item["rule_distance"]),
            int(item.get("evidence_readiness", {}).get("gap", 999)),
            str(item["event_subject"]),
        )
    )
    evidence_states = [
        str(item.get("evidence_readiness", {}).get("status", "UNSPECIFIED"))
        for item in rankings
    ]
    evidence_blockers = Counter(
        blocker
        for item in rankings
        for side in ("kalshi_evidence", "polymarket_evidence")
        for blocker in item.get(side, {}).get("missing_fields", [])
    )
    return {
        "kalshi_counts": _guarantee_counts(left_assessments.values()),
        "polymarket_counts": _guarantee_counts(right_assessments.values()),
        "shared_events_ranked": len(rankings),
        "guaranteed_shared_events": sum(
            item["guarantee_status"] == GuaranteeStatus.GUARANTEED.value
            for item in rankings
        ),
        "execution_ready_events": sum(
            item["queue_status"] in {"AWAITING_SETTLEMENT", "SETTLED"}
            for item in rankings
        ),
        "reachable_blocked_events": sum(
            item["queue_status"] == "BLOCKED"
            and _guarantee_reachable(str(item["guarantee_status"]))
            for item in rankings
        ),
        "structurally_unreachable_events": sum(
            not _guarantee_reachable(str(item["guarantee_status"])) for item in rankings
        ),
        "evidence_complete_shared_events": evidence_states.count("COMPLETE"),
        "evidence_partial_shared_events": evidence_states.count("PARTIAL"),
        "evidence_unspecified_shared_events": evidence_states.count("UNSPECIFIED"),
        "evidence_blocker_counts": dict(
            sorted(evidence_blockers.items(), key=lambda item: (-item[1], item[0]))
        ),
        "rankings": rankings[:limit],
    }


def _lifecycle_status(left: Market, right: Market) -> str:
    """Describe whether a ranked pair can be tracked before settlement."""
    if left.status.value == "settled" and right.status.value == "settled":
        return "SETTLED"
    if left.status.value in {"closed", "settled"} and right.status.value in {
        "closed",
        "settled",
    }:
        return "CLOSED_AWAITING_FINAL_EVIDENCE"
    return "OPEN_AWAITING_SETTLEMENT"


def _settlement_ready_at(left: Market, right: Market) -> str | None:
    """Return the later venue time because both legs must be terminal."""
    times = [time for time in (left.resolution_time, right.resolution_time) if time]
    return max(times).isoformat() if times else None


def _queue_rank(queue_status: str) -> int:
    return {"SETTLED": 0, "AWAITING_SETTLEMENT": 1, "BLOCKED": 2}.get(queue_status, 3)


def _settlement_sort_key(value: object) -> float:
    if not value:
        return float("inf")
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return float("inf")


def _evidence_summary(market: Market, assessment: dict[str, object]) -> dict[str, object]:
    """Expose the evidence gate without copying raw venue payloads into rankings."""
    policy_evidence = assessment.get("policy_evidence")
    policy_blockers = (
        list(policy_evidence.get("blockers", []))
        if isinstance(policy_evidence, dict)
        else []
    )
    reason_codes = [str(code) for code in assessment.get("reason_codes", [])]
    blockers = policy_blockers or reason_codes
    source = str(market.resolution_source or "").strip()
    rules = " ".join(
        value.strip()
        for value in (market.raw_rules_text, market.resolution_text, market.description or "")
        if value and value.strip()
    )
    field_presence = (
        dict(policy_evidence.get("field_presence", {}))
        if isinstance(policy_evidence, dict)
        else {}
    )
    required_fields = (
        "affirmative_branch",
        "negative_branch",
        "cancellation_policy",
        "revision_policy",
        "authoritative_source",
    )
    missing_fields = (
        [field for field in required_fields if field_presence.get(field) is not True]
        if isinstance(policy_evidence, dict)
        else []
    )
    source_fields = [
        field
        for field in (
            "raw_rules_text",
            "resolution_text",
            "description",
            "resolution_source",
            "explicit_binary_sides",
        )
        if field_presence.get(field) is True
    ]
    return {
        "complete": bool(
            policy_evidence.get("complete")
            if isinstance(policy_evidence, dict)
            else assessment.get("status") == GuaranteeStatus.GUARANTEED.value
        ),
        "status": str(assessment.get("status", GuaranteeStatus.UNKNOWN.value)),
        "blockers": blockers,
        "resolution_source": source or None,
        "source_present": bool(source and source.lower() != "unknown"),
        "rules_present": bool(rules),
        "rules_hash": build_fingerprint(market).rules_hash,
        "field_presence": field_presence,
        "source_fields": source_fields,
        "missing_fields": missing_fields,
    }


def _pair_evidence_readiness(
    left: dict[str, object], right: dict[str, object]
) -> dict[str, object]:
    """Classify whether both venues publish the fields needed by the evidence gate."""
    summaries = (left, right)
    explicit_policy = any(bool(summary.get("field_presence")) for summary in summaries)
    complete = all(bool(summary.get("complete")) for summary in summaries)
    missing = sum(len(summary.get("missing_fields", [])) for summary in summaries)
    if complete:
        status = "COMPLETE"
    elif explicit_policy:
        status = "PARTIAL"
    else:
        status = "UNSPECIFIED"
    return {
        "status": status,
        "complete": complete,
        "gap": missing if explicit_policy else 999,
        "missing_fields": {
            "kalshi": list(left.get("missing_fields", [])),
            "polymarket": list(right.get("missing_fields", [])),
        },
    }


def _evidence_priority(queue_status: str, readiness: dict[str, object]) -> int:
    """Preserve settlement-date priority for ready pairs; focus blocked queue on evidence."""
    if queue_status in {"AWAITING_SETTLEMENT", "SETTLED"}:
        return 0
    return {"COMPLETE": 0, "PARTIAL": 1, "UNSPECIFIED": 2}.get(
        str(readiness.get("status", "UNSPECIFIED")),
        3,
    )


def _candidate_queue_state(
    pair_status: str, guarantee_status: str, lifecycle_status: str
) -> tuple[str, str]:
    if not _guarantee_reachable(guarantee_status):
        # A NON_GUARANTEED (discretionary fair-price) pair can never become a trusted
        # label, so it is a permanent dead end rather than rule-mismatch cleanup work.
        return "BLOCKED", "STRUCTURALLY_UNREACHABLE_DISCRETIONARY_SETTLEMENT"
    if pair_status not in {"APPROVED_EQUIVALENT", "APPROVED_INVERSE"}:
        return "BLOCKED", "CLEAR_DETERMINISTIC_RULE_MISMATCHES"
    if guarantee_status != GuaranteeStatus.GUARANTEED.value:
        return "BLOCKED", "COMPLETE_EXPLICIT_SETTLEMENT_GUARANTEE"
    if lifecycle_status == "SETTLED":
        return "SETTLED", "RECONCILE_TERMINAL_OUTCOMES"
    return "AWAITING_SETTLEMENT", "WAIT_FOR_BOTH_TERMINAL_OUTCOMES"


def _bounded_event_pairs(
    left: list[tuple[Market, ContractFingerprint]],
    right: list[tuple[Market, ContractFingerprint]],
) -> list[tuple[Market, Market]]:
    """Generate likely rule-shape pairs without an unbounded Cartesian product."""
    maximum_pairs = 128
    left_shapes: dict[tuple, list[Market]] = {}
    right_shapes: dict[tuple, list[Market]] = {}
    for market, fingerprint in left:
        left_shapes.setdefault(_rule_shape(fingerprint), []).append(market)
    for market, fingerprint in right:
        right_shapes.setdefault(_rule_shape(fingerprint), []).append(market)

    pairs: list[tuple[Market, Market]] = []
    for shape in sorted(left_shapes.keys() & right_shapes.keys(), key=repr):
        left_markets = sorted(left_shapes[shape], key=lambda item: item.market_id)[:8]
        right_markets = sorted(right_shapes[shape], key=lambda item: item.market_id)[:8]
        for left_market in left_markets:
            for right_market in right_markets:
                pairs.append((left_market, right_market))
                if len(pairs) >= maximum_pairs:
                    return pairs
    if pairs:
        return pairs
    return [
        (left_market, right_market)
        for left_market, _ in sorted(left, key=lambda item: item[0].market_id)[:4]
        for right_market, _ in sorted(right, key=lambda item: item[0].market_id)[:4]
    ]


def _rule_shape(fingerprint: ContractFingerprint) -> tuple:
    signed_magnitude = (
        abs(fingerprint.signed_line) if fingerprint.signed_line is not None else None
    )
    return (
        fingerprint.contract_scope,
        signed_magnitude,
        fingerprint.threshold,
        fingerprint.threshold_upper,
        fingerprint.threshold_operator,
        fingerprint.threshold_unit,
        tuple(fingerprint.participants),
    )


def _combined_guarantee(left: str, right: str) -> str:
    statuses = {left, right}
    if statuses == {GuaranteeStatus.GUARANTEED.value}:
        return GuaranteeStatus.GUARANTEED.value
    if GuaranteeStatus.NON_GUARANTEED.value in statuses:
        return GuaranteeStatus.NON_GUARANTEED.value
    return GuaranteeStatus.UNKNOWN.value


def _guarantee_rank(status: str) -> int:
    return {
        GuaranteeStatus.GUARANTEED.value: 0,
        GuaranteeStatus.UNKNOWN.value: 1,
        GuaranteeStatus.NON_GUARANTEED.value: 2,
    }.get(status, 3)


def _guarantee_reachable(guarantee_status: str) -> bool:
    """Whether a pair's family can ever reach a guaranteed (trusted-label) settlement.

    NON_GUARANTEED means at least one leg settles by exchange discretion (fair market
    price), which no amount of additional published evidence can promote to GUARANTEED —
    a permanent dead end. UNKNOWN pairs are still reachable: they settle on objective
    published data and lack only complete policy text, so more venue evidence can promote
    them to GUARANTEED.
    """
    return guarantee_status != GuaranteeStatus.NON_GUARANTEED.value


def _reachability_rank(guarantee_status: str) -> int:
    """Sort reachable candidates ahead of structurally-unreachable dead ends."""
    return 0 if _guarantee_reachable(guarantee_status) else 1


def _guarantee_counts(assessments: object) -> dict[str, int]:
    counts = {status.value: 0 for status in GuaranteeStatus}
    for assessment in assessments:
        status = str(assessment["status"])
        counts[status] = counts.get(status, 0) + 1
    return counts


_NORMALIZATION_FAMILIES = ("economic", "weather", "election", "crypto")


def _normalization_coverage(
    left: list[tuple[Market, ContractFingerprint]],
    right: list[tuple[Market, ContractFingerprint]],
    left_assessments: dict[str, dict[str, object]],
    right_assessments: dict[str, dict[str, object]],
) -> dict[str, object]:
    families: dict[str, dict[str, object]] = {}
    for family in _NORMALIZATION_FAMILIES:
        left_family = [(market, fp) for market, fp in left if fp.market_type == family]
        right_family = [(market, fp) for market, fp in right if fp.market_type == family]
        left_complete = [
            (market, fp)
            for market, fp in left_family
            if not _normalization_blockers(fp)
        ]
        right_complete = [
            (market, fp)
            for market, fp in right_family
            if not _normalization_blockers(fp)
        ]
        left_events = {_fingerprint_event_key(fp) for _, fp in left_complete}
        right_events = {_fingerprint_event_key(fp) for _, fp in right_complete}
        left_guaranteed = {
            _fingerprint_event_key(fp)
            for market, fp in left_complete
            if left_assessments[market.market_id]["status"]
            == GuaranteeStatus.GUARANTEED.value
        }
        right_guaranteed = {
            _fingerprint_event_key(fp)
            for market, fp in right_complete
            if right_assessments[market.market_id]["status"]
            == GuaranteeStatus.GUARANTEED.value
        }
        left_keys = {_fingerprint_verification_key(fp) for _, fp in left_complete}
        right_keys = {_fingerprint_verification_key(fp) for _, fp in right_complete}
        exact_matches = len(left_keys & right_keys)
        shared_events = len(left_events & right_events)
        guaranteed_shared = len(left_guaranteed & right_guaranteed)
        blockers: dict[str, int] = {}
        for _, fingerprint in left_family + right_family:
            for blocker in _normalization_blockers(fingerprint):
                blockers[blocker] = blockers.get(blocker, 0) + 1
        families[family] = {
            "status": _normalization_status(
                bool(left_family),
                bool(right_family),
                bool(left_complete),
                bool(right_complete),
                shared_events,
                guaranteed_shared,
                exact_matches,
            ),
            "kalshi_contracts": len(left_family),
            "polymarket_contracts": len(right_family),
            "kalshi_normalized": len(left_complete),
            "polymarket_normalized": len(right_complete),
            "shared_events": shared_events,
            "guaranteed_shared_events": guaranteed_shared,
            "exact_rule_matches": exact_matches,
            "blocker_counts": dict(
                sorted(blockers.items(), key=lambda item: (-item[1], item[0]))
            ),
        }
    return {
        "families": families,
        "normalized_contracts": sum(
            int(item["kalshi_normalized"]) + int(item["polymarket_normalized"])
            for item in families.values()
        ),
        "shared_events": sum(int(item["shared_events"]) for item in families.values()),
        "guaranteed_shared_events": sum(
            int(item["guaranteed_shared_events"]) for item in families.values()
        ),
        "exact_rule_matches": sum(
            int(item["exact_rule_matches"]) for item in families.values()
        ),
    }


def _normalization_blockers(fingerprint: ContractFingerprint) -> list[str]:
    blockers: list[str] = []
    for field in (
        "event_subject",
        "event_date",
        "event_action",
        "contract_scope",
        "affirmative_outcome",
        "measurement_period",
        "resolution_source",
    ):
        value = getattr(fingerprint, field)
        if value is None or value in {"", "unknown"} or str(value).endswith("unknown"):
            blockers.append(f"MISSING_{field.upper()}")
    threshold_required = fingerprint.market_type == "weather" or (
        fingerprint.market_type == "economic"
        and fingerprint.event_action == "published_value"
    ) or (
        fingerprint.market_type == "crypto"
        and fingerprint.event_action != "price_direction"
    )
    if threshold_required:
        for field in ("threshold", "threshold_operator", "threshold_unit"):
            if getattr(fingerprint, field) is None:
                blockers.append(f"MISSING_{field.upper()}")
    if (
        fingerprint.threshold_operator == "between_inclusive"
        and fingerprint.threshold_upper is None
    ):
        blockers.append("MISSING_THRESHOLD_UPPER")
    if fingerprint.market_type == "crypto":
        if "unknown_method" in str(fingerprint.contract_scope):
            blockers.append("UNKNOWN_CRYPTO_METHOD")
        if fingerprint.resolution_source == "cf_benchmarks_unknown_index":
            blockers.append("UNKNOWN_CRYPTO_INDEX")
    if (
        fingerprint.market_type == "economic"
        and fingerprint.contract_scope == "cpi_yoy_not_seasonally_adjusted"
        and fingerprint.revision_policy != "first_official_release"
    ):
        blockers.append("MISSING_RELEASE_REVISION_POLICY")
    if (
        fingerprint.market_type == "weather"
        and fingerprint.resolution_source == "nws_climatological_report_daily"
        and fingerprint.revision_policy != "latest_final_report"
    ):
        blockers.append("MISSING_WEATHER_REVISION_POLICY")
    return blockers


def _normalization_status(
    has_left: bool,
    has_right: bool,
    normalized_left: bool,
    normalized_right: bool,
    shared_events: int,
    guaranteed_shared: int,
    exact_matches: int,
) -> str:
    if exact_matches:
        return "EXACT_RULE_MATCH"
    if guaranteed_shared:
        return "GUARANTEED_EVENT_NEEDS_TERM_ALIGNMENT"
    if shared_events:
        return "SHARED_EVENT_NEEDS_SETTLEMENT_EVIDENCE"
    if has_left and has_right and not (normalized_left and normalized_right):
        return "NORMALIZATION_BLOCKED"
    if has_left and has_right:
        return "FAMILY_PRESENT_NO_SHARED_EVENT"
    if has_left or has_right:
        return "WAITING_FOR_COUNTERPART"
    return "NOT_PRESENT"


def _fingerprint_event_key(fingerprint: ContractFingerprint) -> tuple[str, str, str]:
    """Identify one measurement event without collapsing distinct actions."""
    return (
        fingerprint.event_subject,
        fingerprint.market_type,
        fingerprint.event_action,
    )
