"""Fed funds target upper-bound *level* normalization across venues.

Fixture texts are frozen copies of real venue-published rules captured 2026-08-12
(Kalshi KXFED-26DEC-T3.50 and KXFEDFUNDSYEAR-28JAN01-T5.75, Polymarket
what-will-the-fed-rate-be-at-the-end-of-2026 markets).
"""

from decimal import Decimal

from atlas.fingerprints import build_fingerprint
from atlas.models import MatchStatus
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.venues.fixtures import fixture_markets
from atlas.verification import verify_equivalence

KALSHI_MEETING_TITLE = (
    "Will the upper bound of the federal funds rate be above 3.50% "
    "following the Fed's Dec 9, 2026 meeting?"
)
KALSHI_MEETING_RULES = (
    "If the upper bound of the target federal funds rate published on the Federal "
    "Reserve's official website is greater than 3.50% following the Federal Reserve's "
    "Dec 9, 2026 meeting, then the market resolves to Yes.\n\nThis market will expire "
    "the first 2:05 PM ET following the release of a Federal Reserve statement for "
    "their Dec 9, 2026 meeting or one week following the last day of that meeting."
)
KALSHI_YEAR_END_TITLE = (
    "Will the upper bound of the target range for the federal funds rate in effect "
    "at 11:59 PM ET on December 31, 2027 be above 5.75%?"
)
KALSHI_YEAR_END_RULES = (
    "If the upper bound of the target range for the federal funds rate in effect at "
    "11:59 PM ET on December 31, 2027, as published on the Federal Reserve’s official "
    "website, is above 5.75%, then the market resolves to Yes.\n\nIf the Federal "
    "Reserve specifies a single target rate rather than a target range, that target "
    "rate will be used."
)
POLYMARKET_LEVEL_TITLE = (
    "Will the upper bound of the target federal funds rate be 3.5% at the end of 2026?"
)
POLYMARKET_LEVEL_RULES = (
    "The FED rate is defined in this market by the upper bound of the target federal "
    "funds range. The decisions on the target federal fund range are made by the "
    "Federal Open Market Committee (FOMC) meetings.\n\nThis market will resolve "
    "according to the upper bound of the Federal Reserve’s target federal funds range "
    "after the December 2026 Federal Open Market Committee (FOMC) meeting, currently "
    "scheduled for December 8-9, 2026.\n\nThis market may resolve immediately after "
    "the statement for the FOMC’s December meeting, with relevant information about "
    "the FOMC’s decision on the target federal funds range, has been issued. If no "
    "FOMC decision on the target federal funds range for their December meeting has "
    "been issued by December 31, 2026, 11:59 PM ET, this market will resolve "
    "according to the upper bound of the target federal funds range at that time.\n\n"
    "The upper bound of the target federal funds range will be rounded to the nearest "
    "25 basis points for resolution of this market. If the upper bound of the target "
    "federal funds range falls exactly between two listed options, it will be rounded "
    "away from zero (e.g. if the upper bound is 2.875, with listed options of 3.0 & "
    "2.75, this market will resolve to 3.0).\n\nThe primary resolution source for "
    "this market will be official information from the Federal Reserve "
    "(https://www.federalreserve.gov/monetarypolicy/openmarket.htm)."
)


def _market(venue_key: str, title: str, rules: str):
    market = fixture_markets()[venue_key][0]
    market.title = title
    market.raw_rules_text = rules
    market.description = None
    market.resolution_source = "unknown"
    return market


def test_kalshi_per_meeting_threshold_canonicalizes():
    fingerprint = build_fingerprint(
        _market("kalshi", KALSHI_MEETING_TITLE, KALSHI_MEETING_RULES)
    )
    assert fingerprint.event_subject == "us_fed_funds_upper_bound|meeting:2026-12-09"
    assert fingerprint.contract_scope == "fed_funds_upper_bound_level"
    assert fingerprint.threshold == Decimal("3.50")
    assert fingerprint.threshold_operator == ">"
    assert fingerprint.threshold_unit == "percent"
    assert fingerprint.resolution_source == "federal_reserve"
    assert fingerprint.settlement_policy is None


def test_kalshi_year_end_snapshot_canonicalizes_with_single_rate_policy():
    fingerprint = build_fingerprint(
        _market("kalshi", KALSHI_YEAR_END_TITLE, KALSHI_YEAR_END_RULES)
    )
    assert fingerprint.event_subject == "us_fed_funds_upper_bound|snapshot:2027-12-31"
    assert fingerprint.threshold == Decimal("5.75")
    assert fingerprint.threshold_operator == ">"
    assert fingerprint.settlement_policy == "single_rate=target_rate_used"


def test_polymarket_level_bucket_canonicalizes_to_published_meeting_anchor():
    fingerprint = build_fingerprint(
        _market("polymarket_us", POLYMARKET_LEVEL_TITLE, POLYMARKET_LEVEL_RULES)
    )
    assert fingerprint.event_subject == "us_fed_funds_upper_bound|meeting:2026-12-09"
    assert fingerprint.contract_scope == "fed_funds_upper_bound_level"
    assert fingerprint.threshold == Decimal("3.5")
    assert fingerprint.threshold_operator == "="
    assert fingerprint.settlement_policy == (
        "no_decision=year_end_rate_snapshot|rounding=nearest_25bps_away_from_zero"
    )


def test_polymarket_tail_buckets_keep_published_operators():
    high = build_fingerprint(
        _market(
            "polymarket_us",
            "Will the upper bound of the target federal funds rate be ≥ 4.5% at the end of 2026?",
            POLYMARKET_LEVEL_RULES,
        )
    )
    low = build_fingerprint(
        _market(
            "polymarket_us",
            "Will the upper bound of the target federal funds rate be ≤1.0% at the end of 2026?",
            POLYMARKET_LEVEL_RULES,
        )
    )
    assert (high.threshold, high.threshold_operator) == (Decimal("4.5"), ">=")
    assert (low.threshold, low.threshold_operator) == (Decimal("1.0"), "<=")


def test_december_cross_venue_pair_is_compared_and_refused_on_published_terms():
    """The Dec 2026 overlap shares one canonical subject, so the verifier compares it
    and refuses on the real published divergences (exact-level vs strict-threshold
    structure and Polymarket's rounding/fallback policy) instead of never seeing it."""
    kalshi = _market("kalshi", KALSHI_MEETING_TITLE, KALSHI_MEETING_RULES)
    polymarket = _market("polymarket_us", POLYMARKET_LEVEL_TITLE, POLYMARKET_LEVEL_RULES)
    assert (
        build_fingerprint(kalshi).event_subject == build_fingerprint(polymarket).event_subject
    )
    pair = verify_equivalence(kalshi, polymarket, "diag:fed-funds-level-dec-2026")
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert "THRESHOLD_OPERATOR_MISMATCH" in pair.differences
    assert "SETTLEMENT_POLICY_MISMATCH" in pair.differences
    assert "EVENT_SUBJECT_MISMATCH" not in pair.differences


def test_meeting_and_snapshot_anchors_never_share_a_subject():
    meeting = build_fingerprint(_market("kalshi", KALSHI_MEETING_TITLE, KALSHI_MEETING_RULES))
    snapshot = build_fingerprint(
        _market("kalshi", KALSHI_YEAR_END_TITLE, KALSHI_YEAR_END_RULES)
    )
    assert meeting.event_subject != snapshot.event_subject


def test_year_end_snapshot_with_single_rate_fallback_is_guaranteed():
    assessment = assess_settlement_guarantee(
        _market("kalshi", KALSHI_YEAR_END_TITLE, KALSHI_YEAR_END_RULES)
    )
    assert assessment["status"] == GuaranteeStatus.GUARANTEED
    assert assessment["reason_codes"] == ["COMPLETE_FED_FUNDS_LEVEL_POLICY"]


def test_polymarket_meeting_anchor_with_no_decision_fallback_is_guaranteed():
    assessment = assess_settlement_guarantee(
        _market("polymarket_us", POLYMARKET_LEVEL_TITLE, POLYMARKET_LEVEL_RULES)
    )
    assert assessment["status"] == GuaranteeStatus.GUARANTEED
    assert assessment["reason_codes"] == ["COMPLETE_FED_FUNDS_LEVEL_POLICY"]


def test_kalshi_per_meeting_without_published_fallback_stays_unknown():
    assessment = assess_settlement_guarantee(
        _market("kalshi", KALSHI_MEETING_TITLE, KALSHI_MEETING_RULES)
    )
    assert assessment["status"] == GuaranteeStatus.UNKNOWN


def test_december_level_pair_enters_settlement_discovery_rankings():
    """With the global open catalog merged into the compatibility report, the live
    Dec 2026 overlap must appear in the persisted settlement-candidate rankings."""
    from atlas.discovery import compatibility_report

    kalshi = _market("kalshi", KALSHI_MEETING_TITLE, KALSHI_MEETING_RULES)
    polymarket = _market("polymarket_us", POLYMARKET_LEVEL_TITLE, POLYMARKET_LEVEL_RULES)
    report = compatibility_report([kalshi], [polymarket])
    rankings = report["settlement_discovery"]["rankings"]
    subjects = [item["event_subject"] for item in rankings]
    assert "us_fed_funds_upper_bound|meeting:2026-12-09" in subjects
    entry = rankings[subjects.index("us_fed_funds_upper_bound|meeting:2026-12-09")]
    assert entry["queue_status"] == "BLOCKED"
    assert entry["guarantee_reachable"] is True
    assert entry["polymarket_guarantee"]["status"] == GuaranteeStatus.GUARANTEED
    assert "THRESHOLD_OPERATOR_MISMATCH" in entry["mismatch_codes"]


def test_cumulative_hit_contracts_are_not_captured_as_levels():
    fingerprint = build_fingerprint(
        _market(
            "polymarket_us",
            "Will the fed rate hit 4.5% before 2027?",
            "This market resolves to Yes if the upper bound of the target federal funds "
            "rate hits 4.5% at any point before January 1, 2027.",
        )
    )
    assert fingerprint.contract_scope != "fed_funds_upper_bound_level"
