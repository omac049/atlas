"""CPI YoY canonical family against real venue-published texts.

Fixture texts are frozen copies of real venue rules captured live 2026-08-12:
Kalshi KXCPIYOY-26JUL-T3.1 (settled ``yes`` off the 3.4% July print) and the
Polymarket "July Inflation US - Annual" event (settled the same day). Unlike the
fed-funds families, BOTH venues publish BLS one-decimal precision, so the only
published divergences are the missing-data fallback branch and Kalshi's absent
terminal fallback.
"""

from decimal import Decimal

from atlas.fingerprints import build_fingerprint
from atlas.models import MatchStatus
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.venues.fixtures import fixture_markets
from atlas.verification import verify_equivalence

KALSHI_T31_TITLE = "Will the rate of CPI inflation be above 3.1% for the year ending in July 2026?"
KALSHI_T31_RULES = (
    "If the Consumer Price Index (CPI) increases by more than 3.1% in the twelve months "
    "ending July 2026 (as represented by the one-decimal place value reported by the "
    "Bureau of Labor Statistics), then the market resolves to Yes.\n\n"
    "In the case of a delay in data caused by a federal government shutdown impacting the "
    "reliability of the Source Agency, the market’s latest Expiration Date will be "
    "extended to the sooner of the release of the Underlying or six months after the end "
    "of the government shutdown"
)

POLYMARKET_TAIL_TITLE = "Will annual inflation be 3.1% or less in July?"
POLYMARKET_EXACT_TITLE = "Will annual inflation be 3.4% in July?"
POLYMARKET_CPI_RULES = (
    "This is a market about inflation over the 12-month period ending July 2026, before "
    "seasonal adjustment, as reported by the Bureau of Labor Statistics.\n\n"
    "This market will resolve to the percentage change in the Consumer Price Index (CPI) "
    "over the 12-month period ending in July 2026 according to the monthly Bureau of "
    "Labor Statistics (BLS) report.\n\n"
    "The resolution source for this market will be the BLS Consumer Price Index report "
    "released for July 2026 (https://www.bls.gov/bls/news-release/cpi.htm), currently "
    "scheduled to be released on August 12, 2026, at 8:30 AM ET. Resolution of this "
    "market will take place upon release of the aforementioned data.\n\n"
    "Note: the resolution source for this market will be the official monthly BLS CPI "
    "news release, which reports inflation over 12-month periods to only one decimal "
    "point (e.g., 2.9%). Thus, this is the level of precision that will be used when "
    "resolving the market.\n\n"
    "If the BLS does not release the relevant figures on the scheduled date, this market "
    "may remain open up until the scheduled release time of the next CPI report "
    "(https://www.bls.gov/schedule). If the information is not released by that time, "
    "this market will resolve according to the figures of the most recent previous "
    "month with available data."
)


def _market(venue_key: str, title: str, rules: str):
    market = fixture_markets()[venue_key][0]
    market.title = title
    market.raw_rules_text = rules
    market.description = None
    market.resolution_source = "unknown"
    # Mirror the live adapters: these venue payloads carry no structured threshold.
    market.threshold = None
    market.threshold_upper = None
    market.threshold_operator = None
    market.raw_market_json = {}
    return market


def test_kalshi_cpi_strike_canonicalizes():
    fingerprint = build_fingerprint(_market("kalshi", KALSHI_T31_TITLE, KALSHI_T31_RULES))
    assert fingerprint.event_subject == "us_cpi_yoy|2026-07"
    assert fingerprint.contract_scope == "cpi_yoy_not_seasonally_adjusted"
    assert fingerprint.threshold == Decimal("3.1")
    assert fingerprint.threshold_operator == ">"
    assert fingerprint.threshold_unit == "percent"
    assert fingerprint.resolution_source == "us_bls_cpi"
    assert fingerprint.settlement_policy == (
        "precision=bls_one_decimal|delay=shutdown_extension_release_or_6m"
    )


def test_polymarket_tail_bucket_canonicalizes():
    fingerprint = build_fingerprint(
        _market("polymarket_us", POLYMARKET_TAIL_TITLE, POLYMARKET_CPI_RULES)
    )
    assert fingerprint.event_subject == "us_cpi_yoy|2026-07"
    assert fingerprint.threshold == Decimal("3.1")
    assert fingerprint.threshold_operator == "<="
    assert fingerprint.settlement_policy == (
        "missing=previous_month_figures_at_next_release|precision=bls_one_decimal"
    )


def test_polymarket_exact_bucket_canonicalizes():
    fingerprint = build_fingerprint(
        _market("polymarket_us", POLYMARKET_EXACT_TITLE, POLYMARKET_CPI_RULES)
    )
    assert fingerprint.threshold == Decimal("3.4")
    assert fingerprint.threshold_operator == "="


def test_polymarket_leg_with_terminal_fallback_is_guaranteed():
    assessment = assess_settlement_guarantee(
        _market("polymarket_us", POLYMARKET_TAIL_TITLE, POLYMARKET_CPI_RULES)
    )
    assert assessment["status"] == GuaranteeStatus.GUARANTEED
    assert assessment["reason_codes"] == ["COMPLETE_CPI_RELEASE_AND_MISSING_DATA_POLICY"]


def test_kalshi_leg_with_extension_only_stays_unknown():
    """Kalshi publishes a shutdown delay-extension but no terminal missing-data
    fallback, so its leg must remain UNKNOWN rather than being inferred complete."""
    assessment = assess_settlement_guarantee(_market("kalshi", KALSHI_T31_TITLE, KALSHI_T31_RULES))
    assert assessment["status"] == GuaranteeStatus.UNKNOWN


def test_cross_venue_tail_pair_blocks_on_exactly_the_published_gaps():
    """PM <=3.1 vs Kalshi >3.1 is an exact logical complement on the same published
    one-decimal BLS value; the honest remaining gaps are the operator complement
    (inverse detection is a gated verifier change), the divergent missing-data
    fallback, and Kalshi's absent terminal fallback."""
    pair = verify_equivalence(
        _market("kalshi", KALSHI_T31_TITLE, KALSHI_T31_RULES),
        _market("polymarket_us", POLYMARKET_TAIL_TITLE, POLYMARKET_CPI_RULES),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert pair.differences == [
        "SETTLEMENT_POLICY_MISMATCH",
        "THRESHOLD_OPERATOR_MISMATCH",
        "SETTLEMENT_GUARANTEE_UNKNOWN",
    ]


def test_non_us_annual_inflation_without_bls_is_not_captured():
    market = _market(
        "polymarket_us",
        "Will Brazil's annual inflation be 4.5% or less for the year ending December 2026?",
        "This market resolves according to the IPCA index published by IBGE for the "
        "twelve months ending December 2026.",
    )
    fingerprint = build_fingerprint(market)
    assert fingerprint.event_subject != "us_cpi_yoy|2026-12"
