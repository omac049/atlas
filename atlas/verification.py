from decimal import Decimal

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
    elif guarantee_code is None and _is_threshold_complement(
        fingerprint_a, fingerprint_b, differences
    ):
        status = MatchStatus.APPROVED_INVERSE
        relationship_codes = ["THRESHOLD_OPERATOR_COMPLEMENT"]
        differences = []
    elif guarantee_code is None and _is_fomc_preimage_equivalent(
        fingerprint_a, fingerprint_b, differences
    ):
        status = MatchStatus.APPROVED_EQUIVALENT
        relationship_codes = ["FOMC_ROUNDING_PREIMAGE_EQUALITY"]
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


def _is_threshold_complement(left: object, right: object, differences: list[str]) -> bool:
    """Two predicate contracts over the same published value are exact inverses when
    the ONLY rule difference is a complementary threshold operator at the identical
    threshold: `x > t` vs `x <= t`, or `x >= t` vs `x < t` — Yes on one side is No on
    the other for every possible published value, with no gap and no overlap.

    Gated verifier rule (review 2026-08-12): every other term — subject, action,
    scope, period, unit, source, revision, and the full settlement-policy token set —
    must already match exactly, so genuine published divergences (missing-data
    fallbacks, rounding, delay clauses) still refuse the pair, and the surrounding
    guarantee gate still requires both legs GUARANTEED before this is consulted.
    """
    if set(differences) != {"THRESHOLD_OPERATOR_MISMATCH"}:
        return False
    if left.affirmative_outcome != "predicate_true":
        return False
    if right.affirmative_outcome != "predicate_true":
        return False
    if left.threshold is None or left.threshold != right.threshold:
        return False
    if left.threshold_upper is not None or right.threshold_upper is not None:
        return False
    if not left.threshold_unit or left.threshold_unit != right.threshold_unit:
        return False
    return (left.threshold_operator, right.threshold_operator) in {
        (">", "<="),
        ("<=", ">"),
        (">=", "<"),
        ("<", ">="),
    }


def _is_fomc_preimage_equivalent(left: object, right: object, differences: list[str]) -> bool:
    """FOMC decision buckets whose Yes-sets over the raw rate change are
    identical under each leg's OWN published rounding policy.

    Gated verifier rule, signed off by the project owner 2026-08-13 under the
    ceiling-in-magnitude reading of Polymarket's published "rounded up to the
    nearest 25" clause (decision record:
    docs/decisions/2026-08-12-fed-rounding-preimage-equality.md). Each leg's
    preimage uses only that leg's published ``rounding=`` token — identity when
    none is published; nothing is inferred. Fires only for the per-meeting
    decision-bucket scope with every non-rounding term (including the
    no-meeting fallback token) already equal and both legs GUARANTEED; the
    exact-±25 buckets and the whole level family remain refused because their
    preimages genuinely differ.
    """
    allowed = {"THRESHOLD_MISMATCH", "THRESHOLD_OPERATOR_MISMATCH", "SETTLEMENT_POLICY_MISMATCH"}
    if not differences or not set(differences) <= allowed:
        return False
    if (
        left.contract_scope != "fomc_rate_change_bucket"
        or right.contract_scope != "fomc_rate_change_bucket"
    ):
        return False
    if _non_rounding_policies(left) != _non_rounding_policies(right):
        return False
    preimage = _fomc_preimage(left)
    return preimage is not None and preimage == _fomc_preimage(right)


def _non_rounding_policies(fingerprint: object) -> frozenset[str]:
    tokens = str(fingerprint.settlement_policy or "").split("|")
    return frozenset(token for token in tokens if token and not token.startswith("rounding="))


def _fomc_preimage(fingerprint: object) -> tuple | None:
    """Canonical Yes-set over the raw signed change (bps) for one decision leg.

    With the published round-up policy, any nonzero change rounds up in
    magnitude to the next 25bp multiple; without one the predicate applies to
    the raw change directly. Thresholds must sit on the 25bp grid — anything
    else is outside the proven table and returns None (no approval).
    """
    direction = fingerprint.affirmative_outcome
    threshold = fingerprint.threshold
    operator = fingerprint.threshold_operator
    if (
        fingerprint.threshold_upper is not None
        or threshold is None
        or operator is None
        or fingerprint.threshold_unit != "bps"
        or threshold % 25 != 0
    ):
        return None
    rounded = "rounding=up_nearest_25bps" in str(fingerprint.settlement_policy or "")
    if direction == "maintain":
        # round-up maps no nonzero change to zero, so the preimage is {0} either way
        return ("point", Decimal(0)) if threshold == 0 and operator == "=" else None
    if direction not in ("increase", "decrease"):
        return None
    sign = 1 if direction == "increase" else -1
    if operator == "=":
        if threshold == 0:
            return None
        if rounded:
            # (T-25, T] in magnitude, signed
            return ("magnitude_halfopen", sign, threshold - 25, threshold)
        return ("point", sign * threshold)
    if operator == ">":
        # on the 25bp grid, r(|d|) > T  <=>  |d| > T, so both cases coincide
        return ("magnitude_ray_open", sign, threshold)
    if operator == ">=":
        if rounded:
            # r(|d|) >= T  <=>  |d| > T - 25
            return ("magnitude_ray_open", sign, threshold - 25)
        return ("magnitude_ray_closed", sign, threshold)
    return None


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
