"""US real GDP growth canonical family against real venue-published texts.

Fixture texts are frozen copies of real venue rules captured live 2026-08-14:
Kalshi ``KXGDP-26JUL30-T1.5`` (settled ``no``) and its open Q3 sibling
``KXGDP-26OCT30-T1.5``; Polymarket US event ``us-saa-q2-2026-07-30`` market
``gdpc-us-saa-q2-2026-07-30-atl1pt5`` ("At least 1.5%", settled ``yes``) and
the open Q3 event ``us-saa-q3-2026-10-29`` market
``gdpc-us-saa-q3-2026-10-29-gt1pt5`` ("Above 1.5%"); Polymarket Global (Gamma)
event ``us-gdp-growth-in-q2-2026`` market
``will-us-gdp-growth-in-q2-2026-be-between-1pt5-and-2pt0`` (bucket "1.5-2.0%",
settled ``yes`` via the published exact-boundary-to-higher-bracket rule).

The load-bearing shape: the Q2 2026 advance print was exactly 1.5%, so
Kalshi's strict "more than 1.5" settled No while Polymarket US "at least 1.5%"
settled Yes — a REALIZED boundary divergence at an identical strike on the same
canonical subject. That pair is an overlap pair, NOT a complement: it must
verify REVIEW_REQUIRED with the honest operator mismatch, must never approve
(``_is_threshold_complement`` rejects >= vs > at the same strike), and with the
real settled outcomes it mints an evidence-backed ("REJECTED", "DIVERGED")
historical label. Kalshi publishes no revision or missing-data clauses — its
legs stay honestly UNKNOWN; that is correct, not a bug.
"""

from decimal import Decimal

from atlas.backfill import _historical_label
from atlas.fingerprints import build_fingerprint
from atlas.models import MatchStatus
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.venues.fixtures import fixture_markets
from atlas.verification import _is_threshold_complement, verify_equivalence

KALSHI_GDP_TITLE = "Will **real GDP** increase by more than {strike}% in {quarter}?"
# Real KXGDP rules template (rules_primary + rules_secondary); only the strike
# and quarter vary across the ladder.
KALSHI_GDP_RULES = (
    "If real GDP (as measured by the BEA’s seasonally adjusted and annualized Advance "
    "Estimate) increases by more than {strike}, then the market resolves to Yes.\n\n"
    "The market will close at 8:29 AM on the day of the expected release of the data. The "
    "market will expire at the first 10:00 AM following the release of the data for "
    "{quarter}, or 3 months following that expected date of data release. Please note the "
    "Expiration Value is the one-decimal value published by the BEA."
)

PM_US_Q2_TITLE = "US GDP growth in Q2 2026"
PM_US_Q2_ATL15_OUTCOME = "At least 1.5%"
PM_US_Q2_RULES = (
    "If the seasonally adjusted annualized rate of real U.S. GDP growth for Q2 2026 is at "
    "least 1.5%, this market will settle to Yes. Outcome verified by BEA.\n\n"
    "Settlement is based on the Advance Estimate GDP release published by the Bureau of "
    "Economic Analysis (BEA), currently scheduled for release on July 30, 2026. Any "
    "subsequent revisions to this figure (including those published in the Second Estimate "
    "or Third Estimate) will not be considered for settlement purposes.\n\n"
    "If the BEA does not publish the seasonally adjusted annualized rate of real GDP Growth "
    "on the Advance Estimate scheduled release date, the first figure officially published "
    "by the BEA for the Q2 2026 seasonally adjusted annualized rate of real GDP growth will "
    "be used for settlement (e.g., in the Second or Third Estimate). If no qualifying "
    "figure is published within three months of the scheduled release date, this market "
    "will resolve based on data from the most recent previous quarter with available data."
)

PM_US_Q3_TITLE = "US GDP Growth in Q3 2026?"
PM_US_Q3_GT15_OUTCOME = "Above 1.5%"
PM_US_Q3_RULES = (
    "This market will settle to Yes if the seasonally adjusted annualized rate of real "
    "U.S. GDP growth for Q3 2026 is above 1.5%. Outcome sourced from the Bureau of "
    "Economic Analysis. \n\n"
    "Settlement is based on the Advance Estimate GDP release published by the BEA, "
    "currently scheduled for October 29, 2026. Any subsequent revisions to this figure "
    "will not be considered for settlement purposes.\n\n"
    "If the figure is not published on the scheduled date, the first officially published "
    "Q3 2026 figure will be used. If no qualifying GDP figure is released within three "
    "months of the Advance Estimate GDP scheduled release date, this market will resolve "
    "based on data from the most recent previous quarter with available data."
)

GAMMA_BUCKET_TITLE = "Will US GDP growth in Q2 2026 be between 1.5% and 2.0%?"
GAMMA_LOW_TITLE = "Will US GDP growth in Q2 2026 be less than 1.0%?"
GAMMA_HIGH_TITLE = "Will US GDP growth in Q2 2026 be greater than 3.5%?"
GAMMA_RULES = (
    'This market will resolve according to the seasonally adjusted and annualized GDP '
    '"Advance Estimate" release for Q2 of 2026, scheduled for July 30, 2026.\n\n'
    "If the reported value falls exactly between two brackets, then this market will "
    "resolve to the higher range bracket.\n\n"
    "The GDP release will be made available here: "
    "https://www.bea.gov/data/gdp/gross-domestic-product\n\n"
    'Note: data in the first available GDP report is labelled by the BEA as an "Advance '
    'Estimate". The data found in the advance estimate will be used to resolve this '
    "market. Data may be revised during the following quarter or as a part of the next "
    "estimate's publication, however any revisions to GDP report data made after the "
    "release of the advance estimate will not be considered for this market's "
    "resolution.\n\n"
    "If the advance estimate is not released, this market will resolve based on the first "
    "officially published figure for real GDP for the specified quarter (e.g., the "
    "‘second’ or ‘third’ estimate, etc.), as reported by the BEA. If no official estimate "
    "is released by the date the next quarter's advanced estimate is scheduled to be "
    "published, this market will resolve based on the most recent previous figure "
    "released by the BEA."
)


def _market(venue_key: str, title: str, rules: str, raw: dict | None = None):
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
    market.raw_market_json = raw or {}
    return market


def _kalshi(strike: str = "1.5", quarter: str = "Q2 2026"):
    return _market(
        "kalshi",
        KALSHI_GDP_TITLE.format(strike=strike, quarter=quarter),
        KALSHI_GDP_RULES.format(strike=strike, quarter=quarter),
    )


def _pm_us_q2(outcome: str = PM_US_Q2_ATL15_OUTCOME):
    # The PM-US event payload carries the market's own bucket in outcome_title;
    # the shared event title carries no bucket at all.
    return _market(
        "polymarket_us", PM_US_Q2_TITLE, PM_US_Q2_RULES, raw={"outcome_title": outcome}
    )


def _pm_us_q3(outcome: str = PM_US_Q3_GT15_OUTCOME):
    return _market(
        "polymarket_us", PM_US_Q3_TITLE, PM_US_Q3_RULES, raw={"outcome_title": outcome}
    )


def _gamma(title: str):
    # Gamma legs ride the shared fixture base; normalization is text-driven and
    # the venue enum does not participate in the family's terms.
    return _market("polymarket_us", title, GAMMA_RULES)


def test_kalshi_strike_canonicalizes_with_published_gaps():
    fingerprint = build_fingerprint(_kalshi("1.5"))
    assert fingerprint.event_subject == "us_real_gdp_growth|2026-Q2:advance"
    assert fingerprint.contract_scope == "real_gdp_growth_saar"
    assert fingerprint.threshold == Decimal("1.5")
    assert fingerprint.threshold_operator == ">"
    assert fingerprint.threshold_unit == "percent"
    assert fingerprint.measurement_period == "2026-Q2:advance"
    assert fingerprint.resolution_source == "us_bea_gdp"
    # Kalshi publishes the one-decimal Expiration Value note but NO revision
    # clause and NO missing-data fallback; only the published token may appear.
    assert fingerprint.settlement_policy == "precision=bea_one_decimal"
    assert fingerprint.revision_policy is None


def test_pm_us_at_least_outcome_canonicalizes():
    fingerprint = build_fingerprint(_pm_us_q2())
    assert fingerprint.event_subject == "us_real_gdp_growth|2026-Q2:advance"
    assert fingerprint.contract_scope == "real_gdp_growth_saar"
    assert fingerprint.threshold == Decimal("1.5")
    assert fingerprint.threshold_operator == ">="
    assert fingerprint.threshold_unit == "percent"
    assert fingerprint.resolution_source == "us_bea_gdp"
    assert fingerprint.settlement_policy == (
        "revision=first_official_release|missing=first_within_3m_else_previous_quarter"
    )
    assert fingerprint.revision_policy == "first_official_release"


def test_pm_us_q3_switched_to_strict_above():
    """The open Q3 event switched to strict "Above X%" outcomes, matching
    Kalshi's operator; the fingerprint must read the published wording, not
    carry the Q2 template's >= forward."""
    fingerprint = build_fingerprint(_pm_us_q3())
    assert fingerprint.event_subject == "us_real_gdp_growth|2026-Q3:advance"
    assert fingerprint.threshold == Decimal("1.5")
    assert fingerprint.threshold_operator == ">"
    assert fingerprint.settlement_policy == (
        "revision=first_official_release|missing=first_within_3m_else_previous_quarter"
    )


def test_gamma_bucket_canonicalizes_with_boundary_token():
    fingerprint = build_fingerprint(_gamma(GAMMA_BUCKET_TITLE))
    assert fingerprint.event_subject == "us_real_gdp_growth|2026-Q2:advance"
    assert fingerprint.contract_scope == "real_gdp_growth_saar"
    assert fingerprint.threshold == Decimal("1.5")
    assert fingerprint.threshold_upper == Decimal("2.0")
    # Distinct from "between_inclusive": with the published exact-boundary rule
    # the bucket is half-open, not inclusive at both ends.
    assert fingerprint.threshold_operator == "between"
    assert fingerprint.resolution_source == "us_bea_gdp"
    assert fingerprint.settlement_policy == (
        "revision=first_official_release"
        "|missing=first_else_most_recent_at_next_release"
        "|boundary=exact_value_to_higher_bracket"
    )


def test_gamma_tail_buckets_canonicalize():
    low = build_fingerprint(_gamma(GAMMA_LOW_TITLE))
    assert (low.threshold, low.threshold_operator) == (Decimal("1.0"), "<")
    high = build_fingerprint(_gamma(GAMMA_HIGH_TITLE))
    assert (high.threshold, high.threshold_operator) == (Decimal("3.5"), ">")


def test_polymarket_legs_with_complete_policies_are_guaranteed():
    for market in (_pm_us_q2(), _pm_us_q3(), _gamma(GAMMA_BUCKET_TITLE)):
        assessment = assess_settlement_guarantee(market)
        assert assessment["status"] == GuaranteeStatus.GUARANTEED, market.title
        assert assessment["reason_codes"] == [
            "COMPLETE_GDP_RELEASE_AND_MISSING_DATA_POLICY"
        ], market.title


def test_kalshi_legs_stay_unknown():
    """Kalshi publishes no revision clause and no missing-data fallback — its
    legs stay honestly UNKNOWN, and the family's scope gate must keep the
    generic yes/no-fallback grant from ever reaching them."""
    for market in (_kalshi("1.5"), _kalshi("1.5", quarter="Q3 2026")):
        assessment = assess_settlement_guarantee(market)
        assert assessment["status"] == GuaranteeStatus.UNKNOWN, market.title
        assert assessment["reason_codes"] == ["FAMILY_POLICY_INCOMPLETE"], market.title


def test_gdp_scope_cannot_reach_guaranteed_via_generic_binary_fallback():
    market = _kalshi("1.5")
    market.raw_rules_text += ' Otherwise, this market will resolve to "No".'
    fingerprint = build_fingerprint(market)
    assert fingerprint.contract_scope == "real_gdp_growth_saar"
    assessment = assess_settlement_guarantee(market, fingerprint)
    assert assessment["status"] == GuaranteeStatus.UNKNOWN
    assert "FAMILY_POLICY_INCOMPLETE" in assessment["reason_codes"]


def test_realized_boundary_pair_reviews_with_operator_mismatch():
    """THE STAR PAIR: Kalshi ">1.5" (settled No) x PM-US ">=1.5" (settled Yes)
    at the identical strike on the same canonical subject. The advance print
    was exactly 1.5%, so the venues genuinely diverged; the pair must verify
    REVIEW_REQUIRED with the honest operator mismatch among its codes."""
    pair = verify_equivalence(_kalshi("1.5"), _pm_us_q2())
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert pair.differences == [
        "SETTLEMENT_POLICY_MISMATCH",
        "THRESHOLD_OPERATOR_MISMATCH",
        "REVISION_POLICY_MISMATCH",
        "SETTLEMENT_GUARANTEE_UNKNOWN",
    ]
    left = pair.decision.fingerprint_a
    right = pair.decision.fingerprint_b
    assert left.event_subject == right.event_subject
    assert left.threshold == right.threshold == Decimal("1.5")


def test_boundary_overlap_operators_are_not_a_complement():
    """">" vs ">=" at the same strike is an OVERLAP pair, not a complement: at
    exactly the strike both predicates differ, everywhere else they agree. The
    gated inverse rule must reject it even with all other terms equal, so the
    pair could never flip to APPROVED_INVERSE no matter how complete the
    policies became."""
    pair = verify_equivalence(_kalshi("1.5"), _pm_us_q2())
    left = pair.decision.fingerprint_a
    right = pair.decision.fingerprint_b
    assert (left.threshold_operator, right.threshold_operator) == (">", ">=")
    assert _is_threshold_complement(left, right, ["THRESHOLD_OPERATOR_MISMATCH"]) is False
    assert _is_threshold_complement(right, left, ["THRESHOLD_OPERATOR_MISMATCH"]) is False


def test_realized_boundary_pair_mints_evidence_backed_rejection():
    """With the real settled outcomes (Kalshi ``no``, PM-US ``yes``) the
    same-subject review pair mints ("REJECTED", "DIVERGED") — an evidence-backed
    rejection from a genuinely realized boundary event. Agreement never proves
    anything and must stay inconclusive."""
    pair = verify_equivalence(_kalshi("1.5"), _pm_us_q2())
    assert _historical_label(pair, "no", "yes") == ("REJECTED", "DIVERGED")
    assert _historical_label(pair, "no", "no") == (None, "INCONCLUSIVE")


def test_gamma_boundary_bucket_pairs_end_to_end():
    """The Gamma "1.5-2.0%" bucket settled Yes off the exact-boundary rule while
    Kalshi ">1.5" settled No: the pair must refuse on the bucket's own terms and
    the realized divergence must mint the same evidence-backed rejection."""
    pair = verify_equivalence(_kalshi("1.5"), _gamma(GAMMA_BUCKET_TITLE))
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert "THRESHOLD_UPPER_MISMATCH" in pair.differences
    assert "THRESHOLD_OPERATOR_MISMATCH" in pair.differences
    assert "SETTLEMENT_POLICY_MISMATCH" in pair.differences
    assert _historical_label(pair, "no", "yes") == ("REJECTED", "DIVERGED")
    # Both-guaranteed does not help: PM-US ">=1.5" and the Gamma bucket carry
    # genuinely different published fallback branches and bucket terms.
    guaranteed_pair = verify_equivalence(_pm_us_q2(), _gamma(GAMMA_BUCKET_TITLE))
    assert guaranteed_pair.status is MatchStatus.REVIEW_REQUIRED
    assert "THRESHOLD_UPPER_MISMATCH" in guaranteed_pair.differences


def test_q3_same_operator_pair_blocks_on_exactly_the_published_gaps():
    """PM-US Q3 switched to strict "Above", so the Q3 pair is identical-strike
    same-operator; the only honest remaining gaps are the published policy
    branches Kalshi lacks and, in consequence, its UNKNOWN guarantee."""
    pair = verify_equivalence(_kalshi("1.5", quarter="Q3 2026"), _pm_us_q3())
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert pair.differences == [
        "SETTLEMENT_POLICY_MISMATCH",
        "REVISION_POLICY_MISMATCH",
        "SETTLEMENT_GUARANTEE_UNKNOWN",
    ]


def test_estimate_vintages_never_cross_match():
    """A leg anchored to the Second Estimate is a different measurement event
    from an Advance-Estimate leg of the same quarter, and a leg whose venue
    never names the estimate cannot share their subject or reach GUARANTEED."""
    second = _market(
        "kalshi",
        KALSHI_GDP_TITLE.format(strike="1.5", quarter="Q2 2026"),
        "If real GDP (as measured by the BEA’s seasonally adjusted and annualized Second "
        "Estimate) increases by more than 1.5, then the market resolves to Yes.",
    )
    fingerprint = build_fingerprint(second)
    assert fingerprint.event_subject == "us_real_gdp_growth|2026-Q2:second"
    pair = verify_equivalence(second, _pm_us_q2())
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert "EVENT_SUBJECT_MISMATCH" in pair.differences
    assert "MEASUREMENT_PERIOD_MISMATCH" in pair.differences


def test_unanchored_vintage_cannot_reach_guaranteed():
    """Constructed adversarial shape: complete revision and fallback clauses but
    no published estimate vintage. The subject stays bare and the family's
    complete-policy path must refuse it."""
    market = _market(
        "polymarket_us",
        "Will US GDP growth in Q2 2026 be at least 1.5%?",
        "If the seasonally adjusted annualized rate of real U.S. GDP growth for Q2 2026 "
        "is at least 1.5%, this market will settle to Yes. Outcome verified by BEA. Any "
        "subsequent revisions to this figure will not be considered for settlement "
        "purposes. If no qualifying figure is published within three months of the "
        "scheduled release date, this market will resolve based on data from the most "
        "recent previous quarter with available data.",
    )
    fingerprint = build_fingerprint(market)
    assert fingerprint.event_subject == "us_real_gdp_growth|2026-Q2"
    assert fingerprint.settlement_policy == (
        "revision=first_official_release|missing=first_within_3m_else_previous_quarter"
    )
    assessment = assess_settlement_guarantee(market, fingerprint)
    assert assessment["status"] == GuaranteeStatus.UNKNOWN
    assert "FAMILY_POLICY_INCOMPLETE" in assessment["reason_codes"]


def test_unmodeled_comparison_fails_safe_to_no_threshold():
    """An unmodeled bucket phrasing must refuse the threshold outright rather
    than fall through to the shared rules text. A threshold-less leg can never
    be guarantee-complete, so the pair can never approve."""
    market = _gamma("Will US GDP growth in Q2 2026 be at most 1.5%?")
    fingerprint = build_fingerprint(market)
    assert fingerprint.event_subject == "us_real_gdp_growth|2026-Q2:advance"
    assert fingerprint.threshold is None
    assert fingerprint.threshold_operator is None
    assert assess_settlement_guarantee(market, fingerprint)["status"] == GuaranteeStatus.UNKNOWN


def test_sibling_enumeration_in_description_cannot_override_the_title():
    market = _market(
        "polymarket_us",
        GAMMA_BUCKET_TITLE,
        GAMMA_RULES
        + " Sibling outcomes include: less than 1.0%, between 1.0% and 1.5%, greater "
        "than 3.5%.",
    )
    fingerprint = build_fingerprint(market)
    assert fingerprint.threshold == Decimal("1.5")
    assert fingerprint.threshold_upper == Decimal("2.0")
    assert fingerprint.threshold_operator == "between"


def test_foreign_gdp_is_never_filed_under_the_us_subject():
    uk = build_fingerprint(
        _market(
            "polymarket_us",
            "Will UK GDP growth in Q2 2026 be above 1.0%?",
            "This market resolves according to the first quarterly estimate of UK GDP "
            "growth for Q2 2026 published by the Office for National Statistics (ONS).",
        )
    )
    assert not (uk.event_subject or "").startswith("us_real_gdp_growth")
    china = build_fingerprint(
        _market(
            "polymarket_us",
            "Will China GDP growth in Q2 2026 be at least 5.0%?",
            "This market resolves according to the year-over-year GDP growth figure for "
            "Q2 2026 published by the National Bureau of Statistics of China (NBS).",
        )
    )
    assert not (china.event_subject or "").startswith("us_real_gdp_growth")


def test_other_macro_series_mentioning_gdp_keep_their_family():
    """A payrolls or PCE contract whose commentary mentions US GDP growth for
    the same quarter must not be re-filed under the GDP subject: the title
    names the market's own series."""
    payrolls = build_fingerprint(
        _market(
            "kalshi",
            "Will nonfarm payrolls increase by more than 100,000 in Q2 2026?",
            "If total nonfarm payroll employment reported by the Bureau of Labor "
            "Statistics increases by more than 100,000 in Q2 2026, then the market "
            "resolves to Yes. Context: real GDP growth slowed in Q2 2026 according to "
            "the BEA's advance estimate.",
        )
    )
    assert not (payrolls.event_subject or "").startswith("us_real_gdp_growth")
    pce = build_fingerprint(
        _market(
            "polymarket_us",
            "Will core PCE inflation be above 2.5% in Q2 2026?",
            "This market resolves according to the core Personal Consumption "
            "Expenditures price index for Q2 2026 published by the BEA. Commentary: US "
            "GDP growth in Q2 2026 was strong.",
        )
    )
    assert not (pce.event_subject or "").startswith("us_real_gdp_growth")


def test_cpi_contract_is_not_captured_by_the_gdp_family():
    fingerprint = build_fingerprint(
        _market(
            "kalshi",
            "Will the rate of CPI inflation be above 3.1% for the year ending in July 2026?",
            "If the Consumer Price Index (CPI) increases by more than 3.1% in the twelve "
            "months ending July 2026 (as represented by the one-decimal place value "
            "reported by the Bureau of Labor Statistics), then the market resolves to Yes.",
        )
    )
    assert (fingerprint.event_subject or "").startswith("us_cpi_yoy")
    assert not (fingerprint.event_subject or "").startswith("us_real_gdp_growth")
