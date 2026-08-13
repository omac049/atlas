"""FOMC per-meeting rate-decision bucket normalization across venues.

Fixture texts are frozen copies of real venue-published rules captured 2026-08-11
(Kalshi KXFEDDECISION-26JUL-H25, Polymarket Global July 2026 25 bps market).
"""

from decimal import Decimal

from atlas.fingerprints import build_fingerprint
from atlas.models import MatchStatus
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.venues.fixtures import fixture_markets
from atlas.verification import verify_equivalence

KALSHI_H25_TITLE = "Will the Federal Reserve Hike rates by 25bps at their July 2026 meeting?"
KALSHI_H25_RULES = (
    "If the Federal Reserve does a Hike of 25bps on July 29, 2026, then the market "
    "resolves to Yes.\n\nThis market is mutually exclusive. Therefore, if the Federal "
    "Reserve hikes by 50bps, the 50bps market will resolve to Yes and the 25bps market "
    "will resolve to No. Only one bucket, at maximum, can resolve to Yes. Note 4/28/25: "
    "For the markets beginning after the May meeting, if a scheduled FOMC meeting is "
    "canceled and does not occur on its scheduled date, then the strike for "
    '"Fed maintains rate" will resolve to Yes and all others will resolve to No.'
)
POLYMARKET_25_TITLE = "Will the Fed increase interest rates by 25 bps after the July 2026 meeting?"
POLYMARKET_25_RULES = (
    "The FED interest rates are defined in this market by the upper bound of the target "
    "federal funds range. The decisions on the target federal funds range are made by the "
    "Federal Open Market Committee (FOMC) meetings.\n\nThis market will resolve to the "
    "amount of basis points the upper bound of the target federal funds rate is changed by "
    "versus the level it was prior to the Federal Reserve's July 2026 meeting.\n\nIf the "
    "target federal funds rate is changed to a level not expressed in the displayed "
    "options, the change will be rounded up to the nearest 25 and will resolve to the "
    "relevant bracket. (e.g. if there's a cut/increase of 12.5 bps it will be considered "
    "to be 25 bps)\n\nThe resolution source for this market is the FOMC's statement after "
    "its meeting scheduled for July 28-29, 2026 according to the official calendar. "
    "If no statement is released by the end date of the next scheduled meeting, this "
    'market will resolve to the "No change" bracket.'
)


def _market(venue_key: str, title: str, rules: str):
    market = fixture_markets()[venue_key][0]
    market.title = title
    market.raw_rules_text = rules
    market.description = None
    market.resolution_source = "unknown"
    return market


def test_kalshi_fomc_bucket_canonicalizes():
    fingerprint = build_fingerprint(_market("kalshi", KALSHI_H25_TITLE, KALSHI_H25_RULES))
    assert fingerprint.event_subject == "us_fomc_rate_decision|2026-07"
    assert fingerprint.contract_scope == "fomc_rate_change_bucket"
    assert fingerprint.affirmative_outcome == "increase"
    assert fingerprint.threshold == Decimal(25)
    assert fingerprint.threshold_operator == "="
    assert fingerprint.threshold_unit == "bps"
    assert fingerprint.resolution_source == "federal_reserve"
    assert fingerprint.settlement_policy == "no_meeting=no_change_bucket"


def test_polymarket_fomc_bucket_canonicalizes_with_rounding_policy():
    fingerprint = build_fingerprint(
        _market("polymarket_us", POLYMARKET_25_TITLE, POLYMARKET_25_RULES)
    )
    assert fingerprint.event_subject == "us_fomc_rate_decision|2026-07"
    assert fingerprint.contract_scope == "fomc_rate_change_bucket"
    assert fingerprint.threshold == Decimal(25)
    assert fingerprint.threshold_operator == "="
    assert fingerprint.settlement_policy == "no_meeting=no_change_bucket|rounding=up_nearest_25bps"


def test_fomc_cross_venue_pair_blocks_only_on_published_rounding_divergence():
    """The July 2026 pair matches on every rule term; the single honest blocker is
    Polymarket's published round-up policy, which Kalshi does not publish."""
    pair = verify_equivalence(
        _market("kalshi", KALSHI_H25_TITLE, KALSHI_H25_RULES),
        _market("polymarket_us", POLYMARKET_25_TITLE, POLYMARKET_25_RULES),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert pair.differences == ["SETTLEMENT_POLICY_MISMATCH"]


def test_fomc_buckets_with_published_fallback_are_guaranteed():
    for venue_key, title, rules in (
        ("kalshi", KALSHI_H25_TITLE, KALSHI_H25_RULES),
        ("polymarket_us", POLYMARKET_25_TITLE, POLYMARKET_25_RULES),
    ):
        assessment = assess_settlement_guarantee(_market(venue_key, title, rules))
        assert assessment["status"] == GuaranteeStatus.GUARANTEED
        assert assessment["reason_codes"] == ["COMPLETE_FOMC_DECISION_BUCKET_POLICY"]


def test_fomc_bucket_without_fallback_policy_stays_unknown():
    rules = "If the Federal Reserve does a Hike of 25bps on July 29, 2026, then the market resolves to Yes."
    assessment = assess_settlement_guarantee(_market("kalshi", KALSHI_H25_TITLE, rules))
    assert assessment["status"] == GuaranteeStatus.UNKNOWN


def test_open_ended_bucket_operators_parse_distinctly_and_approve_via_preimage():
    """SEMANTIC FLIP, named in the 2026-08-13 gated review sign-off: this pair
    previously pinned REVIEW_REQUIRED. Under the approved preimage-equality
    rule (docs/decisions/2026-08-12-fed-rounding-preimage-equality.md), K ">25"
    unrounded and PM "50+" rounded-up share the exact Yes-set (25, inf) and now
    approve. The raw operator/threshold parses stay distinct — the approval
    comes from the preimage computation, not from blurring the parse."""
    kalshi = _market(
        "kalshi",
        "Will the Federal Reserve Hike rates by >25bps at their July 2026 meeting?",
        KALSHI_H25_RULES.replace("Hike of 25bps", "Hike of >25bps"),
    )
    polymarket = _market(
        "polymarket_us",
        "Will the Fed increase interest rates by 50+ bps after the July 2026 meeting?",
        POLYMARKET_25_RULES,
    )
    kalshi_fp, polymarket_fp = build_fingerprint(kalshi), build_fingerprint(polymarket)
    assert (kalshi_fp.threshold, kalshi_fp.threshold_operator) == (Decimal(25), ">")
    assert (polymarket_fp.threshold, polymarket_fp.threshold_operator) == (Decimal(50), ">=")
    assert verify_equivalence(kalshi, polymarket).status is MatchStatus.APPROVED_EQUIVALENT


def test_zero_bps_bucket_canonicalizes_as_maintain():
    fingerprint = build_fingerprint(
        _market(
            "kalshi",
            "Will the Federal Reserve Hike rates by 0bps at their July 2026 meeting?",
            KALSHI_H25_RULES.replace("Hike of 25bps", "Hike of 0bps"),
        )
    )
    assert fingerprint.affirmative_outcome == "maintain"
    assert fingerprint.threshold == Decimal(0)


def test_cumulative_deadline_contract_is_not_captured_as_bucket():
    fingerprint = build_fingerprint(
        _market(
            "polymarket_us",
            "Fed rate cut by July 2026 meeting?",
            "This market will resolve to Yes if the Fed cuts rates at any point before "
            "the end of the July 2026 FOMC meeting. Otherwise it resolves to No.",
        )
    )
    assert fingerprint.contract_scope != "fomc_rate_change_bucket"
