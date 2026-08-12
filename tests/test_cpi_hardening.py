"""Adversarial regressions from the 2026-08-12 independent review of f0d151c.

Every case here was a confirmed false-approval or wrong-fingerprint path on the
committed code: constructed-but-plausible texts where two economically different
contracts produced identical fingerprints, or where a misfiled leg could reach
GUARANTEED through the generic binary-fallback grant. Each must stay dead.
"""

from decimal import Decimal

from atlas.fingerprints import build_fingerprint
from atlas.models import MatchStatus
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.venues.fixtures import fixture_markets
from atlas.verification import verify_equivalence


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


def test_commentary_mention_of_cpi_does_not_capture_unrelated_markets():
    """Finding 1: 'cpi' + 'rise' co-occurrence must not erase a market's identity."""
    rules = (
        "This market resolves to Yes if the price of gold closes higher on the final "
        "trading day of July 2026 than on the first. Otherwise, this market will "
        "resolve to \"No\". Traders often watch the CPI release for July 2026."
    )
    gold_market = _market("kalshi", "Will gold rise in July 2026?", rules)
    silver_market = _market(
        "polymarket_us", "Will silver rise in July 2026?", rules.replace("gold", "silver")
    )
    # Real adapters derive distinct subjects from venue tickers/slugs; the shared
    # fixture base would otherwise hand both markets the same equivalent-pair
    # identity (via event_action) and mask the regression under test.
    gold_market.event_action = "gold price close"
    silver_market.event_action = "silver price close"
    gold = build_fingerprint(gold_market)
    silver = build_fingerprint(silver_market)
    assert not (gold.event_subject or "").startswith("us_cpi")
    assert not (silver.event_subject or "").startswith("us_cpi")
    pair = verify_equivalence(gold_market, silver_market)
    assert pair.status is not MatchStatus.APPROVED_EQUIVALENT
    assert "EVENT_SUBJECT_MISMATCH" in pair.differences


def test_unemployment_market_mentioning_cpi_keeps_its_family():
    fingerprint = build_fingerprint(
        _market(
            "kalshi",
            "Will the U.S. unemployment rate rise above 4.4% for July 2026?",
            "If the U.S. unemployment rate published by the BLS for July 2026 is above "
            "4.4%, this market resolves to Yes. Unlike CPI, which increases by more "
            "than 0.3% in a typical month, unemployment moves slowly.",
        )
    )
    assert fingerprint.event_subject == "us_unemployment_rate|2026-07"


def test_opposite_verbs_produce_distinct_signed_fingerprints():
    """Finding 2: rise/fall and increase/decrease must never collide."""
    cases = (
        ("Will CPI rise by 0.2% or more in July 2026?", Decimal("0.2"), ">="),
        ("Will CPI fall by 0.2% or more in July 2026?", Decimal("-0.2"), "<="),
        ("Will CPI increase by more than 0.3% in July 2026?", Decimal("0.3"), ">"),
        ("Will CPI decrease by more than 0.3% in July 2026?", Decimal("-0.3"), "<"),
        ("Will CPI drop by at least 0.4% in July 2026?", Decimal("-0.4"), "<="),
    )
    boilerplate = (
        "This is a market about the one-month percent change in the seasonally "
        "adjusted Consumer Price Index for All Urban Consumers (CPI-U) in July 2026."
    )
    for title, threshold, operator in cases:
        fingerprint = build_fingerprint(_market("polymarket_us", title, boilerplate))
        assert (fingerprint.threshold, fingerprint.threshold_operator) == (
            threshold,
            operator,
        ), title


def test_unmodeled_directional_phrasing_refuses_a_threshold():
    """Finding 2c: 'increase by 0.3% or less' must not silently drop 'or less'."""
    fingerprint = build_fingerprint(
        _market(
            "polymarket_us",
            "Will CPI increase by 0.3% or less in July 2026?",
            "This is a market about the one-month percent change in the seasonally "
            "adjusted Consumer Price Index (CPI-U) in July 2026.",
        )
    )
    assert fingerprint.threshold is None
    assert fingerprint.threshold_operator is None


def test_title_bucket_wins_over_sibling_enumeration():
    """Finding 4: enumerated sibling buckets in the description must not override."""
    fingerprint = build_fingerprint(
        _market(
            "polymarket_us",
            "Will monthly inflation increase by 0.5% or more in July?",
            "This is a market about the one-month percent change in the seasonally "
            "adjusted Consumer Price Index (CPI-U) in July 2026. Sibling outcomes "
            "include: decrease by 0.7% or more, decrease by 0.6%, stay flat (0.0%).",
        )
    )
    assert (fingerprint.threshold, fingerprint.threshold_operator) == (Decimal("0.5"), ">=")


def test_incidental_us_word_does_not_unlock_foreign_cpi():
    """Finding 5a: the bare English word 'us' is not a US-jurisdiction marker."""
    fingerprint = build_fingerprint(
        _market(
            "polymarket_us",
            "Will China's CPI increase by 0.6% over the 12 month period ending July 2026?",
            "This market resolves according to the CPI figures published by the National "
            "Bureau of Statistics (NBS) for the 12 month period ending July 2026. "
            "Contact us for details.",
        )
    )
    assert not (fingerprint.event_subject or "").startswith("us_cpi")


def test_argentina_cpi_is_not_misfiled():
    fingerprint = build_fingerprint(
        _market(
            "polymarket_us",
            "Will Argentina monthly inflation increase by 2.0% or more in July 2026?",
            "This market resolves according to the monthly CPI figures published by "
            "INDEC for July 2026.",
        )
    )
    assert not (fingerprint.event_subject or "").startswith("us_cpi")


def test_boilerplate_more_than_cannot_shadow_the_strike():
    """Finding 3: 'halted for more than 30 minutes' boilerplate must not become
    the threshold; two different strikes must stay distinguishable."""
    boilerplate = (
        "If trading on the reference exchange is halted for more than 30 minutes, "
        "resolution will be delayed. If the price of Bitcoin is above ${strike} at "
        "the close of July 31, 2026, this market resolves to Yes. Otherwise, this "
        "market will resolve to \"No\"."
    )
    low = _market(
        "kalshi", "Will Bitcoin close above $100,000 in July 2026?",
        boilerplate.format(strike="100,000"),
    )
    high = _market(
        "polymarket_us", "Will Bitcoin close above $110,000 in July 2026?",
        boilerplate.format(strike="110,000"),
    )
    low_fp, high_fp = build_fingerprint(low), build_fingerprint(high)
    assert low_fp.threshold != Decimal(30)
    assert low_fp.threshold != high_fp.threshold
    assert verify_equivalence(low, high).status is not MatchStatus.APPROVED_EQUIVALENT


def test_cpi_scope_cannot_reach_guaranteed_via_generic_binary_fallback():
    """Finding 1 (guarantee bypass): specialized macro scopes earn GUARANTEED only
    through their own complete-policy path."""
    market = _market(
        "kalshi",
        "Will CPI rise more than 0.2% in July 2026?",
        "If the Consumer Price Index (CPI) increases by more than 0.2% "
        "(single-decimal) in July 2026, then the market resolves to Yes. Otherwise, "
        "this market will resolve to \"No\".",
    )
    fingerprint = build_fingerprint(market)
    assert (fingerprint.contract_scope or "").startswith("cpi_")
    assessment = assess_settlement_guarantee(market, fingerprint)
    assert assessment["status"] == GuaranteeStatus.UNKNOWN
    assert "FAMILY_POLICY_INCOMPLETE" in assessment["reason_codes"]


def test_yoy_verb_signed_buckets_no_longer_collide():
    """Pre-existing YoY exposure: verb-signed 12-month buckets now parse signed."""
    boilerplate = (
        "This is a market about inflation over the 12-month period ending July 2026, "
        "as reported by the Bureau of Labor Statistics."
    )
    up = build_fingerprint(
        _market(
            "polymarket_us",
            "Will inflation increase by 0.2% or more over the year ending July 2026?",
            boilerplate,
        )
    )
    down = build_fingerprint(
        _market(
            "polymarket_us",
            "Will inflation decrease by 0.2% or more over the year ending July 2026?",
            boilerplate,
        )
    )
    assert (up.threshold, up.threshold_operator) == (Decimal("0.2"), ">=")
    assert (down.threshold, down.threshold_operator) == (Decimal("-0.2"), "<=")
