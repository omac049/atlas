"""Settlement-timing asymmetry annotation (atlas/settlement_timing.py).

The tag is OBSERVABILITY ONLY: it records that one venue may settle weeks
before its twin (Kalshi's chamber-control contracts may be determined early on
"a consensus of media calls"; the Polymarket twin waits on official state
electoral authorities and the House). These tests pin the detection on the real
published wording, the pair-level asymmetry direction, and — most importantly —
that the annotation changes NO verification status and NO settlement-guarantee
verdict.
"""

from datetime import UTC, datetime

from atlas.models import MatchStatus
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.settlement_timing import (
    SETTLEMENT_TIMING_ASYMMETRIC,
    compare_settlement_timing,
    detect_settlement_timing_asymmetry,
    settlement_horizon_days,
    settlement_timing_annotation,
)
from atlas.venues.fixtures import fixture_markets
from atlas.verification import verify_equivalence

# Verified live on Kalshi CONTROLH-2026 (rules_secondary), 2026-08-19.
KALSHI_CHAMBER_CONTROL_RULES = (
    "This market may be determined early based on a consensus of media calls projecting "
    "control of the U.S. House. See full rules for details. Otherwise, victory will be "
    "determined by the party identification of the Speaker of the House on February 1, 2027."
)

POLYMARKET_CHAMBER_CONTROL_RULES = (
    "This market will resolve to Yes if the Democratic Party wins control of the United "
    "States House of Representatives in the 2026 United States midterm election. A party "
    "wins control if it wins a majority of the chamber's voting seats. Independents who "
    "formally caucus with a party by January 4th, 11:59 PM ET are attributed to that party. "
    "If no party wins a majority, control is determined by the party with which the first "
    "elected Speaker of the House is affiliated. Outcome sourced from relevant state "
    "electoral authorities, and the United States House of Representatives."
)


def _market(venue_key: str, rules: str, title: str = "Chamber control"):
    market = fixture_markets()[venue_key][0]
    market.title = title
    market.subtitle = None
    market.description = None
    market.raw_rules_text = rules
    market.resolution_text = rules
    market.raw_market_json = {}
    return market


def test_kalshi_chamber_control_early_determination_clause_is_detected():
    """The real CONTROLH-2026 secondary rules publish an early-determination
    clause on media consensus, with a dated Speaker-party fallback."""
    tags = detect_settlement_timing_asymmetry(_market("kalshi", KALSHI_CHAMBER_CONTROL_RULES))
    assert tags["venue"] == "kalshi"
    assert tags["early_determination"] is True
    assert "EARLY_MEDIA_CONSENSUS" in tags["early_codes"]
    assert "EARLY_DETERMINATION_CLAUSE" in tags["early_codes"]
    assert tags["fallback"] == {
        "code": "FALLBACK_SPEAKER_PARTY_ON_DATE",
        "date": "2027-02-01",
    }


def test_official_source_market_publishes_no_early_determination():
    tags = detect_settlement_timing_asymmetry(
        _market("polymarket_us", POLYMARKET_CHAMBER_CONTROL_RULES)
    )
    assert tags["early_determination"] is False
    assert tags["early_codes"] == []
    assert "OFFICIAL_STATE_ELECTORAL_AUTHORITIES" in tags["official_codes"]


def test_market_without_any_timing_language_returns_none():
    assert (
        detect_settlement_timing_asymmetry(
            _market("kalshi", "Resolves to Yes if the index closes above 100.", title="Index")
        )
        is None
    )


def test_pair_asymmetry_names_the_earlier_venue_and_why():
    asymmetry = compare_settlement_timing(
        _market("kalshi", KALSHI_CHAMBER_CONTROL_RULES),
        _market("polymarket_us", POLYMARKET_CHAMBER_CONTROL_RULES),
    )
    assert asymmetry["codes"][0] == SETTLEMENT_TIMING_ASYMMETRIC
    assert "LATE_LEG_OFFICIAL_SOURCE_ANCHORED" in asymmetry["codes"]
    assert asymmetry["early_venue"] == "kalshi"
    assert asymmetry["late_venue"] == "polymarket_us"
    assert "EARLY_MEDIA_CONSENSUS" in asymmetry["early_codes"]
    assert "OFFICIAL_STATE_ELECTORAL_AUTHORITIES" in asymmetry["late_codes"]
    assert asymmetry["early_fallback"]["date"] == "2027-02-01"


def test_symmetric_pairs_emit_nothing():
    """Neither-early and both-early pairs are symmetric in timing terms."""
    plain_kalshi = _market("kalshi", POLYMARKET_CHAMBER_CONTROL_RULES)
    plain_polymarket = _market("polymarket_us", POLYMARKET_CHAMBER_CONTROL_RULES)
    assert compare_settlement_timing(plain_kalshi, plain_polymarket) is None
    both_early_kalshi = _market("kalshi", KALSHI_CHAMBER_CONTROL_RULES)
    both_early_polymarket = _market("polymarket_us", KALSHI_CHAMBER_CONTROL_RULES)
    assert compare_settlement_timing(both_early_kalshi, both_early_polymarket) is None


def test_horizon_uses_the_later_leg_because_capital_unlocks_last():
    """A locked basket releases capital only when BOTH legs settle."""
    kalshi = _market("kalshi", KALSHI_CHAMBER_CONTROL_RULES)
    kalshi.close_time = datetime(2026, 11, 10, tzinfo=UTC)
    polymarket = _market("polymarket_us", POLYMARKET_CHAMBER_CONTROL_RULES)
    polymarket.close_time = datetime(2027, 1, 4, tzinfo=UTC)
    days, basis = settlement_horizon_days(kalshi, polymarket, "2026-12-05T00:00:00+00:00")
    assert days == "30.0"
    assert basis == "polymarket_us_close_time"


def test_horizon_is_none_without_published_anchors():
    days, basis = settlement_horizon_days(
        _market("kalshi", KALSHI_CHAMBER_CONTROL_RULES),
        _market("polymarket_us", POLYMARKET_CHAMBER_CONTROL_RULES),
        "2026-12-05T00:00:00+00:00",
    )
    assert days is None
    assert basis is None


def test_annotation_keys_are_small_and_json_safe():
    annotation = settlement_timing_annotation(
        _market("kalshi", KALSHI_CHAMBER_CONTROL_RULES),
        _market("polymarket_us", POLYMARKET_CHAMBER_CONTROL_RULES),
        observed_at="2026-08-19T00:00:00+00:00",
    )
    assert set(annotation) == {
        "asymmetric",
        "codes",
        "early_venue",
        "early_codes",
        "days_to_settlement",
        "horizon_basis",
    }
    assert annotation["asymmetric"] is True
    assert all(isinstance(value, str) for value in annotation["codes"])


def test_annotation_changes_no_verdict_and_no_verification_status():
    """HARD INVARIANT: the timing tag is observability only. It must not move a
    settlement-guarantee verdict, its blocker codes, or a verification status —
    an asymmetry is a caution signal, never an approval input."""
    kalshi = _market("kalshi", KALSHI_CHAMBER_CONTROL_RULES)
    polymarket = _market("polymarket_us", POLYMARKET_CHAMBER_CONTROL_RULES)

    guarantee_before = assess_settlement_guarantee(kalshi)
    verification_before = verify_equivalence(kalshi, polymarket, "timing-test")

    annotation = settlement_timing_annotation(kalshi, polymarket)
    assert annotation["asymmetric"] is True

    guarantee_after = assess_settlement_guarantee(kalshi)
    verification_after = verify_equivalence(kalshi, polymarket, "timing-test")
    assert guarantee_after == guarantee_before
    assert verification_after.status == verification_before.status
    assert list(verification_after.differences) == list(verification_before.differences)
    # And the tag never appears among the deterministic gate's codes.
    assert SETTLEMENT_TIMING_ASYMMETRIC not in guarantee_after["reason_codes"]
    assert SETTLEMENT_TIMING_ASYMMETRIC not in verification_after.differences
    # An asymmetric pair can never reach a trusted approval label.
    assert verification_after.status not in {
        MatchStatus.APPROVED_EQUIVALENT,
        MatchStatus.APPROVED_INVERSE,
    }
    assert guarantee_after["status"] in {
        GuaranteeStatus.GUARANTEED.value,
        GuaranteeStatus.NON_GUARANTEED.value,
        GuaranteeStatus.UNKNOWN.value,
    }
