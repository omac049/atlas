"""US core PCE canonical family against real venue-published texts.

Fixture texts are frozen copies of real venue rules captured live 2026-08-14:
Kalshi ``KXPCECORE-26JUN`` (strict "above X%" ladder, settled: T0.0 ``yes`` /
T0.1 ``no`` off the exact 0.1% June MoM print) and ``KXPCECORE-26JUL`` (open),
and the Polymarket-Global Gamma events ``core-pce-mom-june-2026-...`` (7 exact
one-decimal buckets, "0.1%" resolved Yes), ``core-pce-mom-july-2026-...``
(open, with negative buckets), and ``core-pce-yoy-june-2026-...`` /
``core-pce-yoy-july-2026-...`` (YoY grid — NO Kalshi counterpart exists, and
Polymarket-US lists no PCE events at all). Kalshi publishes only the
"(single-decimal)" precision clause — no revision clause, no missing-data
fallback, no adjustment basis — and those absences must stay visible. Gamma
publishes the BEA source, "seasonally adjusted", the one-decimal precision
clause, and a terminal previous-month missing-data fallback (no revision
clause). Core PCE texts say "excluding food and energy" exactly like core CPI,
so the confusion tests pin both directions of the CPI/PCE boundary.
"""

from decimal import Decimal

from atlas.backfill import _historical_label
from atlas.fingerprints import build_fingerprint
from atlas.models import MatchStatus
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.venues.fixtures import fixture_markets
from atlas.verification import verify_equivalence

KALSHI_PCE_TITLE = "Will the rate of core PCE inflation be above {strike}% in {month} 2026?"
KALSHI_PCE_RULES = (
    "If the (single-decimal) month-over-month percent change in the Personal Consumption "
    "Expenditures Price Index excluding food and energy is above {strike}% in {month} 2026 "
    "according to the Bureau of Economic Analysis, then the market resolves to Yes.\n\n"
    "The market will close at 8:25 AM ET on the expected release of the data. It will "
    "expire at the sooner of the first 10:00 AM ET following the release of the data, or "
    "one week after the expected release of the data."
)

# Gamma core PCE MoM template (June 2026 event verbatim; July differs only in
# month names, release date, and bucket grid).
PM_PCE_MOM_RULES = (
    "This is a market about core inflation (excluding food and energy) over the 1-month "
    "period ending {month} 2026, seasonally adjusted, as reported by the Bureau of "
    "Economic Analysis.\n\n"
    "This market will resolve to the percentage change in the Personal Consumption "
    "Expenditures Price Index excluding food and energy (Core PCE) over the 1-month "
    "period ending in {month} 2026 according to the monthly Bureau of Economic Analysis "
    "(BEA) report.\n\n"
    "The resolution source for this market will be the BEA Personal Income and Outlays "
    "report for {month} 2026 "
    "(https://www.bea.gov/data/personal-consumption-expenditures-price-index), currently "
    "scheduled to be released on {release}, at 8:30 AM ET. Resolution of this market "
    "will take place upon release of the aforementioned data.\n\n"
    "Note: the resolution source for this market will be the official monthly BEA "
    "Personal Income and Outlays news release, which reports core inflation (all items "
    "less food and energy) over 1-month periods to only one decimal point (e.g., 0.3%). "
    "Thus, this is the level of precision that will be used when resolving the market. "
    "For the avoidance of doubt, this market resolves on the core PCE figure — the PCE "
    "price index excluding food and energy — not the headline all-items (total) PCE "
    "price index figure.\n\n"
    "If the BEA does not release the relevant figures on the scheduled date, this market "
    "may remain open up until the scheduled release time of the next Personal Income and "
    "Outlays report (https://www.bea.gov/news/schedule). If the information is not "
    "released by that time, this market will resolve according to the figures of the "
    "most recent previous month with available data."
)

# Gamma core PCE YoY template (June 2026 event verbatim): the first paragraph
# names the index directly and the window is the 12-month period.
PM_PCE_YOY_RULES = (
    "This is a market about percentage change in the Personal Consumption Expenditures "
    "Price Index excluding food and energy (Core PCE) over the 12-month period ending "
    "{month} 2026, seasonally adjusted, as reported by the Bureau of Economic "
    "Analysis.\n\n"
    "This market will resolve to the percentage change in the Personal Consumption "
    "Expenditures Price Index excluding food and energy (Core PCE) over the 12-month "
    "period ending in {month} 2026 according to the monthly Bureau of Economic Analysis "
    "(BEA) report.\n\n"
    "The resolution source for this market will be the BEA Personal Income and Outlays "
    "report for {month} 2026 "
    "(https://www.bea.gov/data/personal-consumption-expenditures-price-index), currently "
    "scheduled to be released on {release}, at 8:30 AM ET. Resolution of this market "
    "will take place upon release of the aforementioned data.\n\n"
    "Note: the resolution source for this market will be the official monthly BEA "
    "Personal Income and Outlays news release, which reports core inflation (all items "
    "less food and energy) over 12-month periods to only one decimal point (e.g., "
    "3.3%). Thus, this is the level of precision that will be used when resolving the "
    "market. For the avoidance of doubt, this market resolves on the core PCE figure — "
    "the PCE price index excluding food and energy — not the headline all-items (total) "
    "PCE price index figure.\n\n"
    "If the BEA does not release the relevant figures on the scheduled date, this market "
    "may remain open up until the scheduled release time of the next Personal Income and "
    "Outlays report (https://www.bea.gov/news/schedule). If the information is not "
    "released by that time, this market will resolve according to the figures of the "
    "most recent previous month with available data."
)

PM_MOM_JUNE = PM_PCE_MOM_RULES.format(month="June", release="July 30, 2026")
PM_MOM_JULY = PM_PCE_MOM_RULES.format(month="July", release="August 26, 2026")
PM_YOY_JUNE = PM_PCE_YOY_RULES.format(month="June", release="July 30, 2026")
PM_YOY_JULY = PM_PCE_YOY_RULES.format(month="July", release="August 26, 2026")

PM_POLICY = "missing=previous_month_figures_at_next_release|precision=bea_one_decimal"


def _market(venue_key: str, title: str, rules: str):
    market = fixture_markets()[venue_key][0]
    market.title = title
    market.raw_rules_text = rules
    market.description = None
    market.resolution_source = "unknown"
    market.threshold = None
    market.threshold_upper = None
    market.threshold_operator = None
    market.revision_policy = None
    market.raw_market_json = {}
    return market


def _kalshi(strike: str, month: str = "June"):
    return _market(
        "kalshi",
        KALSHI_PCE_TITLE.format(strike=strike, month=month),
        KALSHI_PCE_RULES.format(strike=strike, month=month),
    )


def test_kalshi_strict_ladder_canonicalizes_with_precision_only_policy():
    fingerprint = build_fingerprint(_kalshi("0.4"))
    assert fingerprint.event_subject == "us_pce_core_mom|2026-06"
    # Kalshi's rules state no adjustment basis; the bare scope keeps that
    # absence visible rather than inferring BEA's seasonally-adjusted basis.
    assert fingerprint.contract_scope == "pce_core_mom"
    assert fingerprint.threshold == Decimal("0.4")
    assert fingerprint.threshold_operator == ">"
    assert fingerprint.threshold_unit == "percent"
    assert fingerprint.resolution_source == "us_bea_pce"
    # The only published outcome-determining clause is "(single-decimal)":
    # no revision clause, no missing-data fallback — honestly absent.
    assert fingerprint.settlement_policy == "precision=bea_one_decimal"
    assert fingerprint.revision_policy is None


def test_polymarket_mom_grid_buckets_canonicalize():
    cases = (
        ("Will Core PCE MoM be 0.0% or less in June?", PM_MOM_JUNE, "06", Decimal("0.0"), "<="),
        ("Will Core PCE MoM be 0.1% in June?", PM_MOM_JUNE, "06", Decimal("0.1"), "="),
        ("Will Core PCE MoM be 0.6% or more in June?", PM_MOM_JUNE, "06", Decimal("0.6"), ">="),
        ("Will Core PCE MoM be -0.2% or less in July?", PM_MOM_JULY, "07", Decimal("-0.2"), "<="),
        ("Will Core PCE MoM be -0.1% in July?", PM_MOM_JULY, "07", Decimal("-0.1"), "="),
        ("Will Core PCE MoM be 0.4% or more in July?", PM_MOM_JULY, "07", Decimal("0.4"), ">="),
    )
    for title, rules, month, threshold, operator in cases:
        fingerprint = build_fingerprint(_market("polymarket_us", title, rules))
        assert fingerprint.event_subject == f"us_pce_core_mom|2026-{month}", title
        assert fingerprint.contract_scope == "pce_core_mom_seasonally_adjusted", title
        assert (fingerprint.threshold, fingerprint.threshold_operator) == (
            threshold,
            operator,
        ), title
        assert fingerprint.resolution_source == "us_bea_pce", title
        assert fingerprint.settlement_policy == PM_POLICY, title
        assert fingerprint.revision_policy is None, title


def test_polymarket_yoy_grid_canonicalizes_under_its_own_subject():
    low = build_fingerprint(
        _market("polymarket_us", "Will Core PCE YoY be 3.3% or less in June?", PM_YOY_JUNE)
    )
    assert low.event_subject == "us_pce_core_yoy|2026-06"
    assert low.contract_scope == "pce_core_yoy_seasonally_adjusted"
    assert (low.threshold, low.threshold_operator) == (Decimal("3.3"), "<=")
    exact = build_fingerprint(
        _market("polymarket_us", "Will Core PCE YoY be 3.3% in July?", PM_YOY_JULY)
    )
    assert exact.event_subject == "us_pce_core_yoy|2026-07"
    assert (exact.threshold, exact.threshold_operator) == (Decimal("3.3"), "=")
    assert exact.settlement_policy == PM_POLICY


def test_title_bucket_wins_over_sibling_enumeration():
    """A description enumerating sibling buckets must not override the title."""
    fingerprint = build_fingerprint(
        _market(
            "polymarket_us",
            "Will Core PCE MoM be 0.2% in June?",
            PM_MOM_JUNE + " Sibling outcomes include: 0.0% or less, 0.6% or more.",
        )
    )
    assert (fingerprint.threshold, fingerprint.threshold_operator) == (Decimal("0.2"), "=")


def test_mom_and_yoy_subjects_never_cross_match():
    pair = verify_equivalence(
        _kalshi("0.0"),
        _market("polymarket_us", "Will Core PCE YoY be 3.3% or less in June?", PM_YOY_JUNE),
    )
    assert pair.status is not MatchStatus.APPROVED_EQUIVALENT
    assert pair.status is not MatchStatus.APPROVED_INVERSE
    assert "EVENT_SUBJECT_MISMATCH" in pair.differences


def test_pce_texts_file_under_pce_never_under_cpi():
    """Both venue texts say "excluding food and energy" exactly like core CPI —
    and the Gamma YoY boilerplate ("core inflation ... 12-month period") even
    satisfies the CPI YoY trigger — so every PCE leg must still file under a
    us_pce_* subject with a pce_* scope."""
    for market in (
        _kalshi("0.1"),
        _market("polymarket_us", "Will Core PCE MoM be 0.1% in June?", PM_MOM_JUNE),
        _market("polymarket_us", "Will Core PCE YoY be 3.9% or more in June?", PM_YOY_JUNE),
    ):
        fingerprint = build_fingerprint(market)
        assert (fingerprint.event_subject or "").startswith("us_pce_core_"), market.title
        assert (fingerprint.contract_scope or "").startswith("pce_"), market.title
        assert not (fingerprint.event_subject or "").startswith("us_cpi"), market.title


def test_core_cpi_texts_never_file_under_pce():
    """The reverse confusion direction: core CPI texts publish the same
    "excluding food and energy" phrasing but no personal-consumption-
    expenditures reference, so they keep their CPI subjects."""
    kalshi_core_cpi = _market(
        "kalshi",
        "Will CPI Core rise more than 0.0% in July?",
        "If the seasonally adjusted Consumer Price Index for All Urban Consumers: All "
        "Items less Food and Energy for July 2026, as published by the Bureau of Labor "
        "Statistics, increases by above 0.0%, then the market resolves to Yes.",
    )
    fingerprint = build_fingerprint(kalshi_core_cpi)
    assert fingerprint.event_subject == "us_cpi_core_mom|2026-07"
    assert not (fingerprint.contract_scope or "").startswith("pce_")


def test_cpi_market_mentioning_the_pce_index_in_commentary_keeps_cpi():
    """A distant PCE-index mention without its published change window adjacent
    must not re-file a CPI market under the PCE subject."""
    fingerprint = build_fingerprint(
        _market(
            "polymarket_us",
            "Will monthly inflation increase by 0.2% or more in July?",
            "This is a market about the one-month percent change in the seasonally "
            "adjusted Consumer Price Index for All Urban Consumers (CPI-U) in July 2026 "
            "published by the Bureau of Labor Statistics (BLS). Context: the Federal "
            "Reserve prefers the Personal Consumption Expenditures Price Index as its "
            "long-run inflation gauge.",
        )
    )
    assert fingerprint.event_subject == "us_cpi_mom|2026-07"
    assert not (fingerprint.contract_scope or "").startswith("pce_")


def test_foreign_pce_style_contract_is_not_misfiled_under_us_subject():
    fingerprint = build_fingerprint(
        _market(
            "polymarket_us",
            "Will Eurozone Core PCE MoM be 0.2% in June?",
            "This market will resolve to the percentage change in the Personal "
            "Consumption Expenditures Price Index excluding food and energy over the "
            "1-month period ending in June 2026 for the Eurozone, as reported by "
            "Eurostat.",
        )
    )
    assert not (fingerprint.event_subject or "").startswith("us_pce")
    assert not (fingerprint.contract_scope or "").startswith("pce_")


def test_polymarket_legs_are_guaranteed_kalshi_stays_unknown():
    for title, rules in (
        ("Will Core PCE MoM be 0.1% in June?", PM_MOM_JUNE),
        ("Will Core PCE MoM be -0.2% or less in July?", PM_MOM_JULY),
        ("Will Core PCE YoY be 3.9% or more in June?", PM_YOY_JUNE),
    ):
        assessment = assess_settlement_guarantee(_market("polymarket_us", title, rules))
        assert assessment["status"] == GuaranteeStatus.GUARANTEED, title
        assert assessment["reason_codes"] == ["COMPLETE_PCE_RELEASE_AND_MISSING_DATA_POLICY"]
    # Kalshi publishes no missing-data fallback and no adjustment basis: UNKNOWN.
    assessment = assess_settlement_guarantee(_kalshi("0.1"))
    assert assessment["status"] == GuaranteeStatus.UNKNOWN
    assert assessment["reason_codes"] == ["FAMILY_POLICY_INCOMPLETE"]


def test_pce_scope_cannot_reach_guaranteed_via_generic_binary_fallback():
    """An explicit Otherwise-No sentence must not hand this family GUARANTEED
    through the generic yes/no-fallback grant."""
    market = _market(
        "kalshi",
        KALSHI_PCE_TITLE.format(strike="0.2", month="June"),
        KALSHI_PCE_RULES.format(strike="0.2", month="June")
        + ' Otherwise, this market will resolve to "No".',
    )
    fingerprint = build_fingerprint(market)
    assert (fingerprint.contract_scope or "").startswith("pce_")
    assessment = assess_settlement_guarantee(market, fingerprint)
    assert assessment["status"] == GuaranteeStatus.UNKNOWN
    assert "FAMILY_POLICY_INCOMPLETE" in assessment["reason_codes"]


def test_june_tail_pair_blocks_on_exactly_the_published_gaps():
    """K "above 0.0%" (settled yes off the exact 0.1% print) vs Gamma "0.0% or
    less" (settled no) is an exact operator complement on the same published
    one-decimal BEA value; the honest remaining gaps are Kalshi's unpublished
    adjustment basis (scope), its absent missing-data fallback (settlement
    policy), the operator complement, and the guarantee gate."""
    pair = verify_equivalence(
        _kalshi("0.0"),
        _market("polymarket_us", "Will Core PCE MoM be 0.0% or less in June?", PM_MOM_JUNE),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert pair.differences == [
        "CONTRACT_SCOPE_MISMATCH",
        "SETTLEMENT_POLICY_MISMATCH",
        "THRESHOLD_OPERATOR_MISMATCH",
        "SETTLEMENT_GUARANTEE_UNKNOWN",
    ]


def test_june_identical_strike_pair_is_refused_on_the_same_gaps():
    """K "above 0.1%" (settled no) vs Gamma exact "0.1%" (resolved Yes): the
    identical strike still leaves the operator, scope, policy, and guarantee
    differences — different predicates over the same print."""
    pair = verify_equivalence(
        _kalshi("0.1"),
        _market("polymarket_us", "Will Core PCE MoM be 0.1% in June?", PM_MOM_JUNE),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert pair.differences == [
        "CONTRACT_SCOPE_MISMATCH",
        "SETTLEMENT_POLICY_MISMATCH",
        "THRESHOLD_OPERATOR_MISMATCH",
        "SETTLEMENT_GUARANTEE_UNKNOWN",
    ]


def test_historical_labels_from_settled_june_outcomes():
    """Real settled June outcomes (MoM print exactly 0.1%). Review pairs never
    approve: divergent terminal outcomes on the same canonical subject may earn
    an evidence-backed REJECTED, agreement stays inconclusive."""
    complement = verify_equivalence(
        _kalshi("0.0"),
        _market("polymarket_us", "Will Core PCE MoM be 0.0% or less in June?", PM_MOM_JUNE),
    )
    # Kalshi T0.0 settled yes; Gamma <=0.0% resolved No.
    assert _historical_label(complement, "yes", "no") == ("REJECTED", "DIVERGED")
    identical = verify_equivalence(
        _kalshi("0.1"),
        _market("polymarket_us", "Will Core PCE MoM be 0.1% in June?", PM_MOM_JUNE),
    )
    # Kalshi T0.1 settled no; Gamma exact 0.1% resolved Yes.
    assert _historical_label(identical, "no", "yes") == ("REJECTED", "DIVERGED")
    agreeing = verify_equivalence(
        _kalshi("0.2"),
        _market("polymarket_us", "Will Core PCE MoM be 0.2% in June?", PM_MOM_JUNE),
    )
    # Kalshi T0.2 settled no; Gamma exact 0.2% resolved No: agreement proves
    # nothing for a review pair.
    assert _historical_label(agreeing, "no", "no") == (None, "INCONCLUSIVE")
    for pair in (complement, identical, agreeing):
        for outcomes in (("yes", "no"), ("no", "yes"), ("yes", "yes"), ("no", "no")):
            label, _ = _historical_label(pair, *outcomes)
            assert label != "APPROVED_EQUIVALENT"


def test_unmodeled_directional_phrasing_refuses_a_threshold():
    """"increase by 0.3% or less" has no modeled sign reading; the leg must
    refuse a threshold so it can never look guarantee-complete."""
    fingerprint = build_fingerprint(
        _market(
            "polymarket_us",
            "Will Core PCE MoM increase by 0.3% or less in June?",
            PM_MOM_JUNE,
        )
    )
    assert fingerprint.event_subject == "us_pce_core_mom|2026-06"
    assert fingerprint.threshold is None
    assert fingerprint.threshold_operator is None
    assessment = assess_settlement_guarantee(
        _market(
            "polymarket_us",
            "Will Core PCE MoM increase by 0.3% or less in June?",
            PM_MOM_JUNE,
        )
    )
    assert assessment["status"] == GuaranteeStatus.UNKNOWN
