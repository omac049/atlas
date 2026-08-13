"""US U-3 unemployment-rate canonical family against real venue-published texts.

Fixture texts are frozen copies of real venue rules captured live 2026-08-13:
Kalshi ``KXU3-26JUL-T4.1`` / ``-T3.9`` (settled ``no`` / ``yes`` off the 4.1%
July print) and the Polymarket "July Unemployment Rate" event (9 one-decimal
buckets with ``≤3.9%`` / ``≥4.7%`` tails, closed the same day). Kalshi's KXU3
rules publish NO revision, missing-data, or precision clauses — that absence
must stay visible as an empty settlement policy and an UNKNOWN guarantee.
Polymarket's current template publishes a first-release revision freeze, a
terminal last-available-month fallback, and the one-decimal precision clause;
its pre-fallback template (February 2025, also frozen here) lacks the terminal
branch and must not look complete either.
"""

from decimal import Decimal

from atlas.fingerprints import build_fingerprint
from atlas.models import MatchStatus
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.venues.fixtures import fixture_markets
from atlas.verification import verify_equivalence

KALSHI_U3_TITLE = "Will the unemployment rate (U-3) be above {strike}% in July?"
KALSHI_U3_RULES = (
    "If the seasonally adjusted unemployment rate (U-3) reported by the Bureau of Labor "
    "Statistics in the Employment Situation Report is above {strike}% in July 2026, then "
    "the market resolves to Yes."
)

PM_TAIL_LOW_TITLE = "Will the July 2026 unemployment rate be ≤3.9%?"
PM_TAIL_HIGH_TITLE = "Will the July 2026 unemployment rate be ≥4.7%?"
PM_EXACT_TITLE = "Will the July 2026 unemployment rate be 4.2%?"
PM_UNEMPLOYMENT_RULES = (
    "This market will resolve according to the seasonally adjusted unemployment rate "
    "(total unemployed, as a percent of the civilian labor force, official unemployment "
    "rate denoted as U-3) reported by the Bureau of Labor Statistics in the Employment "
    "Situation Report for July 2026.\n\n"
    "The resolution source for this market is the Monthly Employment Situation Report, "
    "published by the BLS every month at https://www.bls.gov/bls/news-release/empsit.htm, "
    "specifically the U-3 measure in Table A-15 for the month in question.\n\n"
    "The relevant data release is scheduled for August 7, 2026, at 8:30 AM ET. This "
    "market will resolve as soon as the relevant data is issued. Any revisions to the "
    "data after the first release will not count toward this market's resolution.\n\n"
    "If no data for the specified month is released by the date the next month's data is "
    "scheduled to be released, this market will resolve based on data from the last "
    "available month.\n\n"
    "Note: the resolution source for this market reports unemployment to one decimal "
    "point. Thus, this is the level of precision that will be used when resolving the "
    "market."
)

# February 2025 template: same subject and revision/precision clauses, but NO
# terminal missing-data fallback — the last-available-month branch was added to
# Polymarket's template later. Buckets are spelled out ("less than or equal
# to", "exactly") rather than symbolic.
PM_FEB25_LOW_TITLE = "Will the unemployment rate for February be less than or equal to 3.9%?"
PM_FEB25_HIGH_TITLE = "Will the unemployment rate for February be greater than or equal to 4.3%?"
PM_FEB25_EXACT_TITLE = "Will the unemployment rate for February be exactly 4.0%?"
PM_FEB25_RULES = (
    "This market will resolve according to the seasonally adjusted unemployment rate "
    "(total unemployed, as percent of the civilian labor force, official unemployment "
    "rate denoted as U-3) reported by the Bureau of Labor Statistics in the Employment "
    "Situation Report for February 2025.\n\n"
    "The resolution source for this market is the Monthly Employment Situation Report, "
    "published by the BLS every month at https://www.bls.gov/bls/news-release/empsit.htm, "
    "specifically the U-3 measure in the Table A-15 for the month in question.\n\n"
    "The next data release is scheduled for March 7, 2025, 8:30 AM ET. This market will "
    "resolve as soon as the relevant data is issued. Any revisions to the data after the "
    "first release will not count toward this market's resolution.\n\n"
    "Note: the resolution source for this market reports unemployment to one decimal "
    "point. Thus, this is the level of precision that will be used when resolving the "
    "market."
)

PM_JAPAN_TITLE = "Will Japan’s January 2026 unemployment rate be ≤2.1%?"
PM_JAPAN_RULES = (
    "This market will resolve according to the seasonally adjusted unemployment rate "
    "(total unemployment rate for both sexes, displayed as a percent) reported in the "
    "Japan Labour Force Survey for January 2026.\n\n"
    "The resolution source for this market is e-Stat, the portal site of Official "
    "Statistics of Japan."
)


def _market(venue_key: str, title: str, rules: str):
    market = fixture_markets()[venue_key][0]
    market.title = title
    market.raw_rules_text = rules
    market.description = None
    market.resolution_source = "unknown"
    # Mirror the live adapters: these venue payloads carry no structured
    # threshold and neither adapter populates a revision-policy field.
    market.threshold = None
    market.threshold_upper = None
    market.threshold_operator = None
    market.revision_policy = None
    market.raw_market_json = {}
    return market


def _kalshi(strike: str):
    return _market(
        "kalshi",
        KALSHI_U3_TITLE.format(strike=strike),
        KALSHI_U3_RULES.format(strike=strike),
    )


def test_kalshi_u3_strike_canonicalizes_with_empty_policy():
    fingerprint = build_fingerprint(_kalshi("4.1"))
    assert fingerprint.event_subject == "us_unemployment_rate|2026-07"
    assert fingerprint.contract_scope == "unemployment_rate_u3_seasonally_adjusted"
    assert fingerprint.threshold == Decimal("4.1")
    assert fingerprint.threshold_operator == ">"
    assert fingerprint.threshold_unit == "percent"
    assert fingerprint.resolution_source == "us_bls_employment_situation"
    # Kalshi publishes no revision, missing-data, or precision clauses; the
    # absence must stay visible rather than being inferred away.
    assert fingerprint.settlement_policy is None
    assert fingerprint.revision_policy is None


def test_polymarket_exact_bucket_canonicalizes():
    fingerprint = build_fingerprint(
        _market("polymarket_us", PM_EXACT_TITLE, PM_UNEMPLOYMENT_RULES)
    )
    assert fingerprint.event_subject == "us_unemployment_rate|2026-07"
    assert fingerprint.contract_scope == "unemployment_rate_u3_seasonally_adjusted"
    assert fingerprint.threshold == Decimal("4.2")
    assert fingerprint.threshold_operator == "="
    assert fingerprint.resolution_source == "us_bls_employment_situation"
    assert fingerprint.settlement_policy == (
        "revision=first_official_release"
        "|missing=last_available_month_at_next_release"
        "|precision=bls_one_decimal"
    )
    assert fingerprint.revision_policy == "first_official_release"


def test_polymarket_tail_buckets_canonicalize():
    low = build_fingerprint(_market("polymarket_us", PM_TAIL_LOW_TITLE, PM_UNEMPLOYMENT_RULES))
    assert (low.threshold, low.threshold_operator) == (Decimal("3.9"), "<=")
    high = build_fingerprint(_market("polymarket_us", PM_TAIL_HIGH_TITLE, PM_UNEMPLOYMENT_RULES))
    assert (high.threshold, high.threshold_operator) == (Decimal("4.7"), ">=")


def test_polymarket_spelled_out_buckets_canonicalize():
    cases = (
        (PM_FEB25_LOW_TITLE, Decimal("3.9"), "<="),
        (PM_FEB25_HIGH_TITLE, Decimal("4.3"), ">="),
        (PM_FEB25_EXACT_TITLE, Decimal("4.0"), "="),
    )
    for title, threshold, operator in cases:
        fingerprint = build_fingerprint(_market("polymarket_us", title, PM_FEB25_RULES))
        assert fingerprint.event_subject == "us_unemployment_rate|2025-02", title
        assert (fingerprint.threshold, fingerprint.threshold_operator) == (
            threshold,
            operator,
        ), title


def test_polymarket_leg_with_terminal_fallback_is_guaranteed():
    assessment = assess_settlement_guarantee(
        _market("polymarket_us", PM_TAIL_LOW_TITLE, PM_UNEMPLOYMENT_RULES)
    )
    assert assessment["status"] == GuaranteeStatus.GUARANTEED
    assert assessment["reason_codes"] == ["COMPLETE_UNEMPLOYMENT_RELEASE_POLICY"]


def test_kalshi_leg_without_published_fallbacks_stays_unknown():
    assessment = assess_settlement_guarantee(_kalshi("4.1"))
    assert assessment["status"] == GuaranteeStatus.UNKNOWN
    assert assessment["reason_codes"] == ["FAMILY_POLICY_INCOMPLETE"]


def test_pre_fallback_polymarket_template_stays_unknown():
    """The February 2025 template publishes no terminal missing-data branch, so
    it must not be inferred complete off the revision and precision clauses."""
    assessment = assess_settlement_guarantee(
        _market("polymarket_us", PM_FEB25_EXACT_TITLE, PM_FEB25_RULES)
    )
    assert assessment["status"] == GuaranteeStatus.UNKNOWN
    assert "FAMILY_POLICY_INCOMPLETE" in assessment["reason_codes"]


def test_unemployment_scope_cannot_reach_guaranteed_via_generic_binary_fallback():
    """An explicit Otherwise-No sentence must not hand this family GUARANTEED
    through the generic yes/no-fallback grant."""
    market = _market(
        "kalshi",
        "Will the U.S. unemployment rate be above 4.4% for July 2026?",
        "If the U.S. unemployment rate published by the BLS for July 2026 is above 4.4%, "
        "then the market resolves to Yes. Otherwise, this market resolves to No.",
    )
    fingerprint = build_fingerprint(market)
    # No published U-3 designation or adjustment basis: the plain scope stays.
    assert fingerprint.contract_scope == "unemployment_rate"
    assessment = assess_settlement_guarantee(market, fingerprint)
    assert assessment["status"] == GuaranteeStatus.UNKNOWN
    assert "FAMILY_POLICY_INCOMPLETE" in assessment["reason_codes"]


def test_cross_venue_tail_pair_blocks_on_exactly_the_published_gaps():
    """K >3.9 (settled yes off the 4.1% print) vs PM ≤3.9 (settled no) is an
    exact logical complement on the same published one-decimal BLS value; the
    honest remaining gaps are Kalshi's absent revision/fallback/precision
    clauses (settlement policy, revision policy, guarantee) and the operator
    complement (inverse promotion requires both legs GUARANTEED)."""
    pair = verify_equivalence(
        _kalshi("3.9"),
        _market("polymarket_us", PM_TAIL_LOW_TITLE, PM_UNEMPLOYMENT_RULES),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert pair.differences == [
        "SETTLEMENT_POLICY_MISMATCH",
        "THRESHOLD_OPERATOR_MISMATCH",
        "REVISION_POLICY_MISMATCH",
        "SETTLEMENT_GUARANTEE_UNKNOWN",
    ]


def test_exact_bucket_pair_is_refused_on_threshold_terms():
    pair = verify_equivalence(
        _kalshi("4.1"),
        _market("polymarket_us", PM_EXACT_TITLE, PM_UNEMPLOYMENT_RULES),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert pair.differences == [
        "SETTLEMENT_POLICY_MISMATCH",
        "THRESHOLD_MISMATCH",
        "THRESHOLD_OPERATOR_MISMATCH",
        "REVISION_POLICY_MISMATCH",
        "SETTLEMENT_GUARANTEE_UNKNOWN",
    ]


def test_japan_unemployment_is_not_captured_into_us_subject():
    fingerprint = build_fingerprint(
        _market("polymarket_us", PM_JAPAN_TITLE, PM_JAPAN_RULES)
    )
    assert not (fingerprint.event_subject or "").startswith("us_unemployment")
    assert not (fingerprint.contract_scope or "").startswith("unemployment_rate")


def test_distant_u3_commentary_does_not_capture_an_unrelated_market():
    """The U-3 marker must sit adjacent to the unemployment-rate reference; a
    distant series mention in commentary is not a published subject."""
    fingerprint = build_fingerprint(
        _market(
            "kalshi",
            "Will nonfarm payrolls increase by more than 100,000 in July 2026?",
            "If total nonfarm payroll employment reported by the Bureau of Labor "
            "Statistics increases by more than 100,000 in July 2026, then the market "
            "resolves to Yes. Context: last month the unemployment rate held steady "
            "while broader measures such as U-6 and U-3 diverged.",
        )
    )
    assert not (fingerprint.event_subject or "").startswith("us_unemployment")
