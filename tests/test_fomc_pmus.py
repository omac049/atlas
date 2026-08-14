"""Polymarket US FOMC decision buckets — the real gateway phrasings.

Verbatim texts captured live 2026-08-14 from gateway.polymarket.us event
``usfed-fomc-2026-07-29`` (settled: no-change Yes, all others No) and the open
``usfed-fomc-2026-09-16`` event. Two structural findings drive these pins:

1. The settled JULY texts publish NO rounding clause and NO canceled-meeting
   fallback — so the July PM-US legs stay honestly UNKNOWN
   (``FAMILY_POLICY_INCOMPLETE``) and the settled no-change pair cannot approve
   despite agreeing outcomes. Owner authorization for this analysis: 2026-08-14
   ("2 - yes"); decision record
   docs/decisions/2026-08-14-pmus-fomc-rounding.md.
2. The SEPTEMBER texts publish a DIFFERENT rounding scheme than Gamma's
   round-up clause (nearest displayed option, ties away from zero) — captured
   as the distinct token ``rounding=nearest_bucket_away_from_zero``, which the
   signed round-up preimage table must refuse.
"""

from decimal import Decimal

from test_fomc_decision import KALSHI_H25_RULES, _market
from test_fomc_preimage import KALSHI_H0_RULES, KALSHI_H0_TITLE

from atlas.backfill import _historical_label
from atlas.fingerprints import build_fingerprint
from atlas.models import MatchStatus
from atlas.settlement import assess_settlement_guarantee
from atlas.verification import _fomc_preimage, verify_equivalence

PMUS_JULY_NOCHANGE_TITLE = "No Change"
PMUS_JULY_NOCHANGE_RULES = (
    "This market will settle to Yes if the Federal Reserve does not change the "
    "upper bound of the target federal funds rate at the July 2026 FOMC "
    "meeting. Outcome verified from the Federal Reserve."
)
PMUS_JULY_HIKE25_RULES = (
    "This market will settle to Yes if the Federal Reserve increases the upper "
    "bound of the target federal funds rate by 25 basis points at the July "
    "2026 FOMC meeting. Outcome verified from the Federal Reserve."
)
PMUS_JULY_CUT50_RULES = (
    "This market will settle to Yes if the Federal Reserve decreases the upper "
    "bound of the target federal funds rate by 50 basis points or more at the "
    "July 2026 FOMC meeting. Outcome verified from the Federal Reserve."
)
_PMUS_ROUNDING_CLAUSE = (
    "\n\nIf the change in the specified rate does not precisely match the "
    "displayed options, changes smaller than the smallest option of the same "
    "direction (increase/decrease) will be rounded to that smallest option, "
    "and changes greater than the smallest option of the same direction will "
    "be rounded to the nearest displayed option, with values exactly halfway "
    "between options being rounded away from zero."
)
PMUS_SEP_HIKE25_RULES = (
    "This market will settle to Yes if the Federal Reserve increases the upper "
    "bound of the target federal funds rate by 25 basis points at the "
    "September 2026 Federal Open Market Committee (FOMC) meeting, currently "
    "scheduled for September 15-16. Outcome sourced from the Federal Reserve."
    + _PMUS_ROUNDING_CLAUSE
)


def test_pmus_july_no_change_canonicalizes_with_empty_policy():
    fingerprint = build_fingerprint(
        _market("polymarket_us", PMUS_JULY_NOCHANGE_TITLE, PMUS_JULY_NOCHANGE_RULES)
    )
    assert fingerprint.event_subject == "us_fomc_rate_decision|2026-07"
    assert fingerprint.affirmative_outcome == "maintain"
    assert fingerprint.threshold == Decimal(0)
    assert fingerprint.threshold_operator == "="
    # The July text publishes neither a rounding clause nor a no-meeting
    # fallback; the empty policy stays visible rather than being inferred.
    assert fingerprint.settlement_policy is None


def test_pmus_bucket_phrasings_parse_signed_operators():
    hike = build_fingerprint(_market("polymarket_us", "25 bps Increase", PMUS_JULY_HIKE25_RULES))
    assert hike.affirmative_outcome == "increase"
    assert hike.threshold == Decimal(25)
    assert hike.threshold_operator == "="
    cut = build_fingerprint(_market("polymarket_us", "50+ bps Decrease", PMUS_JULY_CUT50_RULES))
    assert cut.affirmative_outcome == "decrease"
    assert cut.threshold == Decimal(50)
    assert cut.threshold_operator == ">="


def test_pmus_september_rounding_is_a_distinct_token():
    fingerprint = build_fingerprint(
        _market("polymarket_us", "25 bps Increase", PMUS_SEP_HIKE25_RULES)
    )
    assert fingerprint.event_subject == "us_fomc_rate_decision|2026-09"
    assert fingerprint.settlement_policy == "rounding=nearest_bucket_away_from_zero"


def test_pmus_legs_stay_unknown_without_published_no_meeting_fallback():
    for rules in (PMUS_JULY_NOCHANGE_RULES, PMUS_SEP_HIKE25_RULES):
        market = _market("polymarket_us", "bucket", rules)
        guarantee = assess_settlement_guarantee(market)
        assert guarantee["status"] == "UNKNOWN"
        assert "FAMILY_POLICY_INCOMPLETE" in guarantee["reason_codes"]


def test_settled_july_no_change_pair_stays_inconclusive():
    """K H0 yes ↔ PM-US no-change settlement=1: agreeing outcomes on the same
    subject prove nothing, and the PM-US leg's missing no-meeting branch keeps
    the pair below the both-guaranteed approval gate. No outcome combination
    may approve a review pair."""
    pair = verify_equivalence(
        _market("kalshi", KALSHI_H0_TITLE, KALSHI_H0_RULES),
        _market("polymarket_us", PMUS_JULY_NOCHANGE_TITLE, PMUS_JULY_NOCHANGE_RULES),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert "SETTLEMENT_GUARANTEE_UNKNOWN" in pair.differences
    assert _historical_label(pair, "yes", "yes") == (None, "INCONCLUSIVE")
    for outcomes in (("yes", "yes"), ("yes", "no"), ("no", "yes")):
        label, _ = _historical_label(pair, *outcomes)
        assert label != "APPROVED_EQUIVALENT"


def test_unrecognized_rounding_token_refuses_the_preimage():
    """Defense in depth below the guarantee gate: only the signed round-up
    reading has a proven preimage table, so any other published rounding token
    must return None instead of being treated as an unrounded leg."""
    fingerprint = build_fingerprint(
        _market("polymarket_us", "25 bps Increase", PMUS_SEP_HIKE25_RULES)
    )
    assert fingerprint.settlement_policy == "rounding=nearest_bucket_away_from_zero"
    assert _fomc_preimage(fingerprint) is None
    # The identical fingerprint under the signed round-up token still has one.
    rounded = fingerprint.model_copy(update={"settlement_policy": "rounding=up_nearest_25bps"})
    assert _fomc_preimage(rounded) is not None


def test_gamma_texts_still_parse_after_the_pmus_widening():
    fingerprint = build_fingerprint(
        _market(
            "polymarket_us",
            "Will the Fed increase interest rates by 25 bps after the July 2026 meeting?",
            KALSHI_H25_RULES,
        )
    )
    assert fingerprint.event_subject == "us_fomc_rate_decision|2026-07"
