"""CPI sibling families (headline MoM, core YoY, core MoM) against real venue texts.

Fixture texts are frozen copies of real venue rules captured live 2026-08-12:
Kalshi KXCPI-26JUL (signed strikes, no published agency name or adjustment basis),
KXCPICORE-26JUL (explicit BLS + seasonally adjusted), and the Polymarket July 2026
monthly / core-YoY / core-MoM events (all the same template as the annual event).
The core-MoM tail pair is a second exact operator complement with every non-policy
term matching; the headline-MoM pair additionally exposes Kalshi's unpublished
agency and adjustment basis as honest gaps.
"""

from decimal import Decimal

from atlas.fingerprints import build_fingerprint
from atlas.models import MatchStatus
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.venues.fixtures import fixture_markets
from atlas.verification import verify_equivalence

KALSHI_MOM_TITLE = "Will CPI rise more than 0.1% in July 2026?"
KALSHI_MOM_NEG_TITLE = "Will CPI rise more than -0.1% in July 2026?"
KALSHI_MOM_RULES = (
    "If the Consumer Price Index (CPI) increases by more than {strike}% (single-decimal) "
    "in July 2026, then the market resolves to Yes.\n\n"
    "The market will always close at 8:25 AM ET on the scheduled day of the data release "
    "(August 12, 2026). Please note: the Expiration Value is the single-decimal value "
    "published at the Source Agency. In the case of a delay in data caused by a federal "
    "government shutdown impacting the reliability of the Source Agency, the market’s "
    "latest Expiration Date will be extended to the sooner of the release of the "
    "Underlying or six months after the end of the government shutdown."
)

KALSHI_CORE_TITLE = "Will CPI Core rise more than 0.0% in July?"
KALSHI_CORE_RULES = (
    "If the seasonally adjusted Consumer Price Index for All Urban Consumers: All Items "
    "less Food and Energy for July 2026, as published by the Bureau of Labor Statistics, "
    "increases by above 0.0%, then the market resolves to Yes.\n\n"
    "Please note that the value of the Underlying is the single-decimal value reported "
    "by the BLS.\n\n"
    "In the case of a delay in data caused by a federal government shutdown impacting "
    "the reliability of the Source Agency, the market’s latest Expiration Date will be "
    "extended to the sooner of the release of the Underlying or six months after the "
    "end of the government shutdown"
)

PM_MONTHLY_RULES = (
    "This is a market about the one-month percent change in the seasonally adjusted "
    "Consumer Price Index for All Urban Consumers (CPI-U) published by the Bureau of "
    "Labor Statistics (BLS).\n\n"
    "This market will resolve to the one-month percent change in the seasonally "
    "adjusted Consumer Price Index for All Urban Consumers (CPI-U) in July 2026 "
    "according to the monthly BLS report.\n\n"
    "Note: the resolution source for this market will be the official monthly Consumer "
    "Price Index for All Urban Consumers (CPI-U) which BLS reports to one decimal point "
    "(e.g. 0.4%). Thus, this is the level of precision that will be used when resolving "
    "the market.\n\n"
    "If the BLS does not release the relevant figures on the scheduled date, this "
    "market may remain open up until the scheduled release time of the next CPI report "
    "(https://www.bls.gov/schedule). If the information is not released by that time, "
    "this market will resolve according to the figures of the most recent previous "
    "month with available data."
)

PM_CORE_YOY_RULES = (
    "This is a market about core inflation (excluding food and energy) over the "
    "12-month period ending July 2026, before seasonal adjustment, as reported by the "
    "Bureau of Labor Statistics.\n\n"
    "This market will resolve to the percentage change in the Consumer Price Index for "
    "All Urban Consumers excluding food and energy (Core CPI-U) over the 12-month "
    "period ending in July 2026 according to the monthly Bureau of Labor Statistics "
    "(BLS) report.\n\n"
    "Note: the resolution source for this market will be the official monthly BLS CPI "
    "news release, which reports core inflation (all items less food and energy) over "
    "12-month periods to only one decimal point (e.g., 2.8%).\n\n"
    "If the BLS does not release the relevant figures on the scheduled date, this "
    "market may remain open up until the scheduled release time of the next CPI "
    "report. If the information is not released by that time, this market will resolve "
    "according to the figures of the most recent previous month with available data."
)

PM_CORE_MOM_RULES = (
    "This is a market about the one-month percent change in the Consumer Price Index "
    "for All Urban Consumers excluding food and energy in July 2026 as reported by the "
    "Bureau of Labor Statistics.\n\n"
    "This market will resolve to the one-month percentage change in the Consumer Price "
    "Index for All Urban Consumers excluding food and energy (Core CPI-U) in July 2026 "
    "according to the monthly Bureau of Labor Statistics (BLS) report (seasonally "
    "adjusted change from the preceding month).\n\n"
    "Note: the resolution source for this market will be the official monthly BLS CPI "
    "news release, which reports core inflation (all items less food and energy) over "
    "1-month periods to only one decimal point (e.g., 0.3%).\n\n"
    "If the BLS does not release the relevant figures on the scheduled date, this "
    "market may remain open up until the scheduled release time of the next CPI "
    "report. If the information is not released by that time, this market will resolve "
    "according to the figures of the most recent previous month with available data."
)


def _market(venue_key: str, title: str, rules: str):
    market = fixture_markets()[venue_key][0]
    market.title = title
    market.raw_rules_text = rules
    market.description = None
    market.resolution_source = "unknown"
    market.threshold = None
    market.threshold_upper = None
    market.threshold_operator = None
    market.raw_market_json = {}
    return market


def test_kalshi_headline_mom_canonicalizes_with_signed_strike():
    fingerprint = build_fingerprint(
        _market("kalshi", KALSHI_MOM_NEG_TITLE, KALSHI_MOM_RULES.format(strike="-0.1"))
    )
    assert fingerprint.event_subject == "us_cpi_mom|2026-07"
    assert fingerprint.contract_scope == "cpi_mom"
    assert fingerprint.threshold == Decimal("-0.1")
    assert fingerprint.threshold_operator == ">"
    # Kalshi's headline-MoM rules name only a "Source Agency" and state no
    # adjustment basis; both absences must stay visible, not be inferred away.
    assert fingerprint.resolution_source != "us_bls_cpi"
    assert fingerprint.settlement_policy == (
        "precision=bls_one_decimal|delay=shutdown_extension_release_or_6m"
    )


def test_kalshi_core_mom_canonicalizes():
    fingerprint = build_fingerprint(_market("kalshi", KALSHI_CORE_TITLE, KALSHI_CORE_RULES))
    assert fingerprint.event_subject == "us_cpi_core_mom|2026-07"
    assert fingerprint.contract_scope == "cpi_core_mom_seasonally_adjusted"
    assert fingerprint.threshold == Decimal("0.0")
    assert fingerprint.threshold_operator == ">"
    assert fingerprint.resolution_source == "us_bls_cpi"


def test_polymarket_monthly_signed_buckets():
    cases = (
        ("Will monthly inflation decrease by 0.7% or more in July?", Decimal("-0.7"), "<="),
        ("Will monthly inflation decrease by 0.6% in July?", Decimal("-0.6"), "="),
        ("Will monthly inflation stay flat (0.0%) in July?", Decimal("0.0"), "="),
        ("Will monthly inflation increase by 0.1% or more in July?", Decimal("0.1"), ">="),
    )
    for title, threshold, operator in cases:
        fingerprint = build_fingerprint(_market("polymarket_us", title, PM_MONTHLY_RULES))
        assert fingerprint.event_subject == "us_cpi_mom|2026-07", title
        assert fingerprint.contract_scope == "cpi_mom_seasonally_adjusted", title
        assert fingerprint.threshold == threshold, title
        assert fingerprint.threshold_operator == operator, title


def test_polymarket_core_variants_canonicalize():
    yoy = build_fingerprint(
        _market("polymarket_us", "Will Core CPI YoY be 2.2% or less in July?", PM_CORE_YOY_RULES)
    )
    assert yoy.event_subject == "us_cpi_core_yoy|2026-07"
    assert yoy.contract_scope == "cpi_core_yoy_not_seasonally_adjusted"
    assert (yoy.threshold, yoy.threshold_operator) == (Decimal("2.2"), "<=")
    mom = build_fingerprint(
        _market("polymarket_us", "Will Core CPI MoM be 0.0% or less in July?", PM_CORE_MOM_RULES)
    )
    assert mom.event_subject == "us_cpi_core_mom|2026-07"
    assert mom.contract_scope == "cpi_core_mom_seasonally_adjusted"
    assert (mom.threshold, mom.threshold_operator) == (Decimal("0.0"), "<=")


def test_polymarket_sibling_legs_are_guaranteed_kalshi_stay_unknown():
    for title, rules in (
        ("Will monthly inflation decrease by 0.6% in July?", PM_MONTHLY_RULES),
        ("Will Core CPI YoY be 2.8% in July?", PM_CORE_YOY_RULES),
        ("Will Core CPI MoM be 0.2% in July?", PM_CORE_MOM_RULES),
    ):
        assessment = assess_settlement_guarantee(_market("polymarket_us", title, rules))
        assert assessment["status"] == GuaranteeStatus.GUARANTEED, title
        assert assessment["reason_codes"] == ["COMPLETE_CPI_RELEASE_AND_MISSING_DATA_POLICY"]
    for title, rules in (
        (KALSHI_MOM_TITLE, KALSHI_MOM_RULES.format(strike="0.1")),
        (KALSHI_CORE_TITLE, KALSHI_CORE_RULES),
    ):
        assessment = assess_settlement_guarantee(_market("kalshi", title, rules))
        assert assessment["status"] == GuaranteeStatus.UNKNOWN, title


def test_core_mom_tail_pair_blocks_on_exactly_the_published_gaps():
    """K Core >0.0 (settled yes) vs PM Core MoM <=0.0 (settled no) is the second
    exact operator complement: subject, scope, period, source, threshold all match;
    only the divergent missing-data branch, the operator complement, and Kalshi's
    absent terminal fallback remain."""
    pair = verify_equivalence(
        _market("kalshi", KALSHI_CORE_TITLE, KALSHI_CORE_RULES),
        _market("polymarket_us", "Will Core CPI MoM be 0.0% or less in July?", PM_CORE_MOM_RULES),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert pair.differences == [
        "SETTLEMENT_POLICY_MISMATCH",
        "THRESHOLD_OPERATOR_MISMATCH",
        "SETTLEMENT_GUARANTEE_UNKNOWN",
    ]


def test_headline_mom_pair_also_exposes_kalshi_unpublished_basis():
    pair = verify_equivalence(
        _market("kalshi", KALSHI_MOM_NEG_TITLE, KALSHI_MOM_RULES.format(strike="-0.1")),
        _market(
            "polymarket_us",
            "Will monthly inflation decrease by 0.1% in July?",
            PM_MONTHLY_RULES,
        ),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert "CONTRACT_SCOPE_MISMATCH" in pair.differences
    assert "RESOLUTION_SOURCE_MISMATCH" in pair.differences


def test_mom_and_yoy_families_never_cross_match():
    pair = verify_equivalence(
        _market("kalshi", KALSHI_MOM_TITLE, KALSHI_MOM_RULES.format(strike="0.1")),
        _market(
            "polymarket_us",
            "Will annual inflation be 3.4% in July?",
            PM_CORE_YOY_RULES.replace("core inflation (excluding food and energy)", "inflation"),
        ),
    )
    assert "EVENT_SUBJECT_MISMATCH" in pair.differences


def test_foreign_cpi_with_unhyphenated_window_is_not_misfiled():
    fingerprint = build_fingerprint(
        _market(
            "polymarket_us",
            "Will China's CPI increase by 0.6% over the 12 month period ending July 2026?",
            "This market resolves according to the CPI figures published by the National "
            "Bureau of Statistics (NBS) for the 12 month period ending July 2026.",
        )
    )
    assert not (fingerprint.event_subject or "").startswith("us_cpi")
