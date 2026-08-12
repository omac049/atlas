"""Gated verifier rule (review 2026-08-12): threshold-operator complement inverses.

`x > t` vs `x <= t` (and `x >= t` vs `x < t`) at the identical threshold over the
same published value are exact logical inverses. The rule is deliberately narrow:
every other fingerprint term including the full settlement-policy token set must
match, and both legs must be GUARANTEED, so the real July 2026 CPI pair — whose
venue texts genuinely diverge on the missing-data fallback — must STAY blocked
(pinned in test_cpi_yoy.py). The approval fixture below therefore uses the real
Polymarket text against a HYPOTHETICAL Kalshi text whose only change is publishing
the same terminal fallback — the exact revision that would make the pair labelable.
"""

from test_cpi_yoy import (
    KALSHI_T31_TITLE,
    POLYMARKET_CPI_RULES,
    POLYMARKET_TAIL_TITLE,
    _market,
)

from atlas.backfill import _historical_label
from atlas.fingerprints import build_fingerprint
from atlas.models import MatchStatus
from atlas.verification import _is_threshold_complement, verify_equivalence

# Hypothetical: Kalshi's real strike sentence plus Polymarket's published terminal
# missing-data fallback, WITHOUT the delay-extension clause — the token sets align.
KALSHI_T31_RULES_WITH_TERMINAL_FALLBACK = (
    "If the Consumer Price Index (CPI) increases by more than 3.1% in the twelve months "
    "ending July 2026 (as represented by the one-decimal place value reported by the "
    "Bureau of Labor Statistics), then the market resolves to Yes.\n\n"
    "If the information is not released by the scheduled release time of the next CPI "
    "report, this market will resolve according to the figures of the most recent "
    "previous month with available data."
)


def _complement_pair():
    kalshi = _market("kalshi", KALSHI_T31_TITLE, KALSHI_T31_RULES_WITH_TERMINAL_FALLBACK)
    polymarket = _market("polymarket_us", POLYMARKET_TAIL_TITLE, POLYMARKET_CPI_RULES)
    return kalshi, polymarket


def test_exact_complement_with_identical_published_policies_approves_inverse():
    kalshi, polymarket = _complement_pair()
    kalshi_fp = build_fingerprint(kalshi)
    polymarket_fp = build_fingerprint(polymarket)
    assert kalshi_fp.settlement_policy == polymarket_fp.settlement_policy
    assert (kalshi_fp.threshold_operator, polymarket_fp.threshold_operator) == (">", "<=")

    pair = verify_equivalence(kalshi, polymarket, "diag:cpi-complement")
    assert pair.status is MatchStatus.APPROVED_INVERSE
    assert pair.differences == []
    assert pair.decision.relationship_codes == ["THRESHOLD_OPERATOR_COMPLEMENT"]


def test_inverse_pair_with_complementary_outcomes_confirms_trusted_label():
    """End-to-end into the backfill labeler: an approved inverse whose settled
    outcomes complement produces a trusted CONFIRMED label; agreeing outcomes on an
    inverse pair are a divergence and must produce REJECTED."""
    kalshi, polymarket = _complement_pair()
    pair = verify_equivalence(kalshi, polymarket, "diag:cpi-complement")
    assert _historical_label(pair, "yes", "no") == ("APPROVED_EQUIVALENT", "CONFIRMED")
    assert _historical_label(pair, "yes", "yes") == ("REJECTED", "DIVERGED")


def test_gap_and_overlap_operator_pairs_are_not_complements():
    kalshi, polymarket = _complement_pair()
    left = build_fingerprint(kalshi)
    right = build_fingerprint(polymarket)
    only_operator = ["THRESHOLD_OPERATOR_MISMATCH"]
    # x > t vs x < t leaves the value t itself uncovered (a gap).
    assert not _is_threshold_complement(
        left, right.model_copy(update={"threshold_operator": "<"}), only_operator
    )
    # x >= t vs x <= t double-covers the value t (an overlap).
    assert not _is_threshold_complement(
        left.model_copy(update={"threshold_operator": ">="}), right, only_operator
    )
    # An exact-level bucket is never the complement of a one-sided threshold.
    assert not _is_threshold_complement(
        left, right.model_copy(update={"threshold_operator": "="}), only_operator
    )


def test_any_second_mismatch_disqualifies_the_complement():
    kalshi, polymarket = _complement_pair()
    left = build_fingerprint(kalshi)
    right = build_fingerprint(polymarket)
    assert not _is_threshold_complement(
        left, right, ["SETTLEMENT_POLICY_MISMATCH", "THRESHOLD_OPERATOR_MISMATCH"]
    )
    assert not _is_threshold_complement(
        left, right, ["THRESHOLD_MISMATCH", "THRESHOLD_OPERATOR_MISMATCH"]
    )


def test_non_predicate_or_incomplete_thresholds_are_not_complements():
    kalshi, polymarket = _complement_pair()
    left = build_fingerprint(kalshi)
    right = build_fingerprint(polymarket)
    only_operator = ["THRESHOLD_OPERATOR_MISMATCH"]
    assert not _is_threshold_complement(
        left.model_copy(update={"affirmative_outcome": "team_a"}), right, only_operator
    )
    assert not _is_threshold_complement(
        left.model_copy(update={"threshold": None}),
        right.model_copy(update={"threshold": None}),
        only_operator,
    )
    assert not _is_threshold_complement(
        left.model_copy(update={"threshold_upper": right.threshold}), right, only_operator
    )
    assert not _is_threshold_complement(
        left.model_copy(update={"threshold_unit": None}),
        right.model_copy(update={"threshold_unit": None}),
        only_operator,
    )
