"""FOMC rounding preimage-equality rule — the 2026-08-13 signed-off decision.

Decision record: docs/decisions/2026-08-12-fed-rounding-preimage-equality.md.
The owner signed off on the ceiling-in-magnitude reading of Polymarket's
published round-up clause, so decision-bucket pairs whose Yes-sets over the raw
rate change are provably identical under each leg's OWN published policy now
approve: the maintain pair and both open tails. The exact-±25 buckets and the
entire fed-funds level family remain refused — their preimages genuinely
differ. Fixture texts are the frozen real venue rules from test_fomc_decision.
"""

from decimal import Decimal

from test_fomc_decision import KALSHI_H25_RULES, POLYMARKET_25_RULES, _market

from atlas.fingerprints import build_fingerprint
from atlas.models import MatchStatus
from atlas.verification import verify_equivalence

KALSHI_H0_TITLE = "Will the Federal Reserve Hike rates by 0bps at their July 2026 meeting?"
KALSHI_H0_RULES = KALSHI_H25_RULES.replace("Hike of 25bps", "Hike of 0bps")
KALSHI_H26_TITLE = "Will the Federal Reserve Hike rates by >25bps at their July 2026 meeting?"
KALSHI_H26_RULES = KALSHI_H25_RULES.replace("Hike of 25bps", "Hike of >25bps")

POLYMARKET_NO_CHANGE_TITLE = (
    "Will there be no change in Fed interest rates after the July 2026 meeting?"
)
POLYMARKET_50PLUS_TITLE = (
    "Will the Fed increase interest rates by 50+ bps after the July 2026 meeting?"
)


def test_polymarket_no_change_bucket_canonicalizes_as_maintain():
    fingerprint = build_fingerprint(
        _market("polymarket_us", POLYMARKET_NO_CHANGE_TITLE, POLYMARKET_25_RULES)
    )
    assert fingerprint.event_subject == "us_fomc_rate_decision|2026-07"
    assert fingerprint.affirmative_outcome == "maintain"
    assert fingerprint.threshold == Decimal(0)
    assert fingerprint.threshold_operator == "="
    assert "no_meeting=no_change_bucket" in (fingerprint.settlement_policy or "")


def test_maintain_pair_approves_via_preimage_equality():
    """{0} == {0}: the round-up clause cannot map any nonzero change to zero."""
    pair = verify_equivalence(
        _market("kalshi", KALSHI_H0_TITLE, KALSHI_H0_RULES),
        _market("polymarket_us", POLYMARKET_NO_CHANGE_TITLE, POLYMARKET_25_RULES),
    )
    assert pair.status is MatchStatus.APPROVED_EQUIVALENT
    assert pair.decision.relationship_codes == ["FOMC_ROUNDING_PREIMAGE_EQUALITY"]
    assert pair.differences == []


def test_open_tail_pair_approves_via_preimage_equality():
    """(25, inf) == (25, inf): >25 unrounded and 50+ rounded-up coincide."""
    pair = verify_equivalence(
        _market("kalshi", KALSHI_H26_TITLE, KALSHI_H26_RULES),
        _market("polymarket_us", POLYMARKET_50PLUS_TITLE, POLYMARKET_25_RULES),
    )
    assert pair.status is MatchStatus.APPROVED_EQUIVALENT
    assert pair.decision.relationship_codes == ["FOMC_ROUNDING_PREIMAGE_EQUALITY"]


def test_exact_25_pair_stays_refused_forever():
    """{25} != (0, 25]: a hypothetical 12.5bps hike resolves them differently."""
    pair = verify_equivalence(
        _market("kalshi", "Will the Federal Reserve Hike rates by 25bps at their July 2026 meeting?", KALSHI_H25_RULES),
        _market(
            "polymarket_us",
            "Will the Fed increase interest rates by 25 bps after the July 2026 meeting?",
            POLYMARKET_25_RULES,
        ),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert "SETTLEMENT_POLICY_MISMATCH" in pair.differences


def test_cross_meeting_buckets_never_approve():
    pair = verify_equivalence(
        _market("kalshi", KALSHI_H26_TITLE, KALSHI_H26_RULES),
        _market(
            "polymarket_us",
            "Will the Fed increase interest rates by 50+ bps after the September 2026 meeting?",
            POLYMARKET_25_RULES.replace("July 2026", "September 2026").replace(
                "July 28-29, 2026", "September 16, 2026"
            ),
        ),
    )
    assert pair.status is not MatchStatus.APPROVED_EQUIVALENT


def test_kalshi_cut_bucket_dropped_gt_quirk_is_survived_by_title_parsing():
    """Live data-quality quirk: Kalshi's Cut->25bps rules text drops the ">"
    ("does a Cut of   25bps"), making it read like the exact-25 bucket. The
    title keeps the ">" and must win; if this ever regresses, the C26 bucket
    would collide with C25 and could approve against the wrong PM bracket."""
    fingerprint = build_fingerprint(
        _market(
            "kalshi",
            "Will the Federal Reserve Cut rates by >25bps at their September 2026 meeting?",
            KALSHI_H25_RULES.replace("Hike of 25bps", "Cut of   25bps").replace(
                "July 29, 2026", "September 16, 2026"
            ),
        )
    )
    assert fingerprint.affirmative_outcome == "decrease"
    assert (fingerprint.threshold, fingerprint.threshold_operator) == (Decimal(25), ">")


def test_level_family_is_out_of_scope_for_the_preimage_rule():
    from test_fed_funds_level import (
        KALSHI_MEETING_RULES,
        KALSHI_MEETING_TITLE,
        POLYMARKET_LEVEL_RULES,
        POLYMARKET_LEVEL_TITLE,
    )

    pair = verify_equivalence(
        _market("kalshi", KALSHI_MEETING_TITLE, KALSHI_MEETING_RULES),
        _market("polymarket_us", POLYMARKET_LEVEL_TITLE, POLYMARKET_LEVEL_RULES),
    )
    assert pair.status is not MatchStatus.APPROVED_EQUIVALENT
