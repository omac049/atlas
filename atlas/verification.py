from atlas.fingerprints import build_fingerprint
from atlas.models import ContractPair, EquivalenceDecision, Market, MatchStatus
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee


def verify_equivalence(market_a: Market, market_b: Market, pair_id: str = "pair-1") -> ContractPair:
    fingerprint_a = build_fingerprint(market_a)
    fingerprint_b = build_fingerprint(market_b)
    fields = [
        "event_subject",
        "event_action",
        "contract_scope",
        "affirmative_outcome",
        "signed_line",
        "settlement_policy",
        "threshold",
        "threshold_upper",
        "threshold_operator",
        "threshold_unit",
        "measurement_period",
        "resolution_source",
        "revision_policy",
        "market_type",
    ]
    differences = [
        f.upper() + "_MISMATCH"
        for f in fields
        if getattr(fingerprint_a, f) != getattr(fingerprint_b, f)
    ]
    if fingerprint_a.participants != fingerprint_b.participants:
        differences.append("PARTICIPANTS_MISMATCH")
    guarantee_code = _guarantee_blocker(market_a, market_b, fingerprint_a, fingerprint_b)
    relationship_codes: list[str] = []
    if not differences and guarantee_code is None:
        status = MatchStatus.APPROVED_EQUIVALENT
    elif guarantee_code is None and _is_strict_inverse(fingerprint_a, fingerprint_b, differences):
        status = MatchStatus.APPROVED_INVERSE
        relationship_codes = [
            "AFFIRMATIVE_OUTCOME_COMPLEMENT",
            "SIGNED_LINE_COMPLEMENT",
        ]
        differences = []
    else:
        status = MatchStatus.REVIEW_REQUIRED
        if guarantee_code and guarantee_code not in differences:
            differences.append(guarantee_code)
    decision = EquivalenceDecision(
        status=status,
        mismatch_codes=differences,
        relationship_codes=relationship_codes,
        fingerprint_a=fingerprint_a,
        fingerprint_b=fingerprint_b,
    )
    return ContractPair(
        pair_id=pair_id,
        market_a=market_a,
        market_b=market_b,
        status=status,
        match_confidence=1 if not differences else 0,
        differences=differences,
        decision=decision,
    )


def _is_strict_inverse(left: object, right: object, differences: list[str]) -> bool:
    if set(differences) != {
        "AFFIRMATIVE_OUTCOME_MISMATCH",
        "SIGNED_LINE_MISMATCH",
    }:
        return False
    if left.market_type != "spread" or right.market_type != "spread":
        return False
    if not left.settlement_policy or "fair_price" in left.settlement_policy:
        return False
    if left.settlement_policy != right.settlement_policy:
        return False
    if left.participants != right.participants:
        return False
    if left.affirmative_outcome not in left.participants:
        return False
    if right.affirmative_outcome not in right.participants:
        return False
    if left.affirmative_outcome == right.affirmative_outcome:
        return False
    if left.signed_line is None or right.signed_line is None:
        return False
    return left.signed_line == -right.signed_line


def _guarantee_blocker(
    market_a: Market,
    market_b: Market,
    fingerprint_a: object,
    fingerprint_b: object,
) -> str | None:
    statuses = {
        assess_settlement_guarantee(market_a, fingerprint_a)["status"],
        assess_settlement_guarantee(market_b, fingerprint_b)["status"],
    }
    if GuaranteeStatus.NON_GUARANTEED.value in statuses:
        return "NON_GUARANTEED_SETTLEMENT"
    if GuaranteeStatus.UNKNOWN.value in statuses:
        return "SETTLEMENT_GUARANTEE_UNKNOWN"
    return None
