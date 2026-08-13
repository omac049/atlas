"""Evidence-backed REJECTED labels from same-subject review pairs.

Owner-signed decision 2026-08-13
(docs/decisions/2026-08-13-rejected-labels-from-review-pairs.md): a
REVIEW_REQUIRED pair may mint a trusted REJECTED label ONLY when both legs
share the same canonical subject and their terminal settled outcomes diverge —
settlement evidence disproving equivalence. Review pairs still can never
approve; agreement still proves nothing; unrelated divergent pairs stay
unlabeled. The July 2026 CPI tail pair (K >3.1 settled yes, PM <=3.1 settled
No) is the recurring real-world example, frozen from test_cpi_yoy.
"""

from test_cpi_yoy import (
    KALSHI_T31_RULES,
    KALSHI_T31_TITLE,
    POLYMARKET_CPI_RULES,
    POLYMARKET_TAIL_TITLE,
    _market,
)

from atlas.backfill import _historical_label, _label_priority
from atlas.models import MatchStatus
from atlas.verification import verify_equivalence


def _cpi_tail_pair():
    return verify_equivalence(
        _market("kalshi", KALSHI_T31_TITLE, KALSHI_T31_RULES),
        _market("polymarket_us", POLYMARKET_TAIL_TITLE, POLYMARKET_CPI_RULES),
    )


def test_same_subject_divergent_review_pair_mints_rejected():
    pair = _cpi_tail_pair()
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    label, relationship = _historical_label(pair, "yes", "no")
    assert (label, relationship) == ("REJECTED", "DIVERGED")


def test_same_subject_agreeing_review_pair_stays_inconclusive():
    """Agreement never proves equivalence — no label of any kind."""
    pair = _cpi_tail_pair()
    label, relationship = _historical_label(pair, "no", "no")
    assert label is None
    assert relationship == "INCONCLUSIVE"


def test_different_subject_divergent_pair_stays_unlabeled():
    """Unrelated contracts that settled differently are worthless negatives."""
    pair = verify_equivalence(
        _market("kalshi", KALSHI_T31_TITLE, KALSHI_T31_RULES),
        _market(
            "polymarket_us",
            "Will Core CPI MoM be 0.1% in July?",
            "This is a market about the one-month percent change in the Consumer Price "
            "Index for All Urban Consumers excluding food and energy in July 2026 as "
            "reported by the Bureau of Labor Statistics.",
        ),
    )
    label, relationship = _historical_label(pair, "yes", "no")
    assert label is None
    assert relationship == "INCONCLUSIVE"


def test_review_pairs_still_can_never_approve():
    pair = _cpi_tail_pair()
    for outcomes in (("yes", "no"), ("no", "no"), ("yes", "yes")):
        label, _ = _historical_label(pair, *outcomes)
        assert label != "APPROVED_EQUIVALENT"


def test_label_priority_orders_approved_then_complement_shaped_review():
    review_complement = _cpi_tail_pair()
    assert _label_priority(review_complement) == 1
    left = _market("kalshi", "Will something happen?", "Some rules.")
    right = _market("polymarket_us", "Will something else happen?", "Other rules.")
    # Distinct identities: the shared fixture base would otherwise hand both
    # markets the fixture pair's matching subject.
    left.event_action = "one thing"
    right.event_action = "another thing"
    unrelated = verify_equivalence(left, right)
    assert _label_priority(unrelated) == 2
