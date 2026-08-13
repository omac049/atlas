"""ISM PMI canonical family (manufacturing + services) against real venue texts.

Fixture texts are frozen copies of real venue rules captured live 2026-08-13:
Kalshi ``KXISMPMI-26JUL-57`` / ``-58`` (settled ``no`` off the 55.x July print),
Kalshi ``KXUSISMSERV-26JUN03-T54.0`` (settled ``yes``, May 2026 data), and the
Polymarket "ISM Manufacturing PMI - July 2026" / "ISM Services PMI - July 2026"
events (tag 105113, all resolved).

The load-bearing shape: Kalshi manufacturing "At least 57" and Polymarket's
"57.0+" tail are the SAME predicate — identical threshold, identical ``>=``
operator — so the only honest gaps left are the published ones: Polymarket's
terminal previous-month fallback (Kalshi publishes none) and therefore Kalshi's
UNKNOWN settlement guarantee. Kalshi's services series is an older template
that publishes no source, precision, or fallback in its rules (its event-level
settlement source is Trading Economics, not ISM), and those absences must stay
visible rather than being inferred away.
"""

from decimal import Decimal

from atlas.fingerprints import build_fingerprint
from atlas.models import MatchStatus
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.venues.fixtures import fixture_markets
from atlas.verification import verify_equivalence

KALSHI_MFG_TITLE = "ISM Manufacturing PMI in July 2026?"
# Real per-strike rules template (KXISMPMI-26JUL): only the strike varies.
KALSHI_MFG_RULES = (
    "If the headline PMI value in the ISM Manufacturing PMI Report On Business for "
    "July 2026, as published by ISM (one decimal place), is at least {strike}, then "
    "the market resolves to Yes."
)

KALSHI_SERV_TITLE = "Will US ISM services PMI for May 2026 be above 54.0?"
KALSHI_SERV_RULES = (
    "If US ISM services PMI for May 2026 is above \n\n54\n\n, then the market resolves to Yes."
)

PM_MFG_TAIL_TITLE = "Will ISM Manufacturing PMI be at least 57.0 in July?"
PM_MFG_LOW_TITLE = "Will ISM Manufacturing PMI be below 49.0 in July?"
PM_MFG_BRACKET_TITLE = "Will ISM Manufacturing PMI be between 55.0 and 55.9 in July?"
PM_MFG_RULES = (
    "This is a market about the ISM Manufacturing Purchasing Managers' Index (PMI) "
    "for July 2026, as reported by the Institute for Supply Management (ISM). A "
    "reading above 50 indicates expansion in the manufacturing sector relative to "
    "the previous month; a reading below 50 indicates contraction.\n\n"
    "This market will resolve to the bracket containing the ISM Manufacturing PMI "
    "for July 2026 according to the monthly ISM Manufacturing PMI Report On "
    "Business.\n\n"
    "The resolution source for this market will be the ISM Manufacturing PMI Report "
    "On Business released for July 2026 "
    "(https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/), "
    "currently scheduled to be released on August 3, 2026, at 10:00 AM ET. "
    "Resolution of this market will take place upon release of the aforementioned "
    "data.\n\n"
    "Note: although ISM describes the PMI reading using the term \"percent\" in its "
    "official release (e.g., \"The Manufacturing PMI registered 52.7 percent\"), the "
    "PMI is a diffusion index, not a true percentage. For the purposes of this "
    "market, the PMI reading will be treated as a plain numerical value, consistent "
    "with how the index is universally quoted in financial markets.\n\n"
    "Note: The ISM Manufacturing PMI is reported to one decimal point. Thus, this "
    "is the level of precision that will be used when resolving this market.\n\n"
    "If ISM does not release the relevant figures on the scheduled date, this "
    "market may remain open until the scheduled release time of the next ISM "
    "Manufacturing PMI report "
    "(https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar/). "
    "If the information is not released by that time, this market will resolve "
    "according to the figures of the most recent previous month with available data."
)

PM_SERV_TAIL_TITLE = "Will ISM Services PMI be at least 58.0 in July?"
PM_SERV_RULES = (
    "This is a market about the ISM Services Purchasing Managers' Index (PMI) for "
    "July 2026, as reported by the Institute for Supply Management (ISM). A reading "
    "above 50 indicates expansion in the services sector relative to the previous "
    "month; a reading below 50 indicates contraction.\n\n"
    "This market will resolve to the bracket containing the ISM Services PMI for "
    "July 2026 according to the monthly ISM Services PMI Report On Business.\n\n"
    "The resolution source for this market will be the ISM Services PMI Report On "
    "Business released for July 2026 "
    "(https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/), "
    "currently scheduled to be released on August 5, 2026, at 10:00 AM ET. "
    "Resolution of this market will take place upon release of the aforementioned "
    "data.\n\n"
    "Note: although ISM describes the PMI reading using the term \"percent\" in its "
    "official release (e.g., \"The Services PMI registered 53.6 percent\"), the PMI "
    "is a diffusion index, not a true percentage. For the purposes of this market, "
    "the PMI reading will be treated as a plain numerical value, consistent with "
    "how the index is universally quoted in financial markets.\n\n"
    "Note: The ISM Services PMI is reported to one decimal point. Thus, this is the "
    "level of precision that will be used when resolving this market.\n\n"
    "If ISM does not release the relevant figures on the scheduled date, this "
    "market may remain open until the scheduled release time of the next ISM "
    "Services PMI report "
    "(https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar/). "
    "If the information is not released by that time, this market will resolve "
    "according to the figures of the most recent previous month with available data."
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


def test_kalshi_manufacturing_strike_canonicalizes():
    fingerprint = build_fingerprint(
        _market("kalshi", KALSHI_MFG_TITLE, KALSHI_MFG_RULES.format(strike="57"))
    )
    assert fingerprint.event_subject == "us_ism_manufacturing_pmi|2026-07"
    assert fingerprint.contract_scope == "ism_manufacturing_pmi"
    assert fingerprint.threshold == Decimal(57)
    assert fingerprint.threshold_operator == ">="
    assert fingerprint.threshold_unit == "index_points"
    assert fingerprint.resolution_source == "ism_report_on_business"
    # Kalshi publishes the ISM one-decimal precision inline but NO
    # missing-release fallback; only the published token may appear.
    assert fingerprint.settlement_policy == "precision=ism_one_decimal"


def test_kalshi_services_strike_canonicalizes_with_published_gaps():
    market = _market("kalshi", KALSHI_SERV_TITLE, KALSHI_SERV_RULES)
    # Mirror the live event-level enrichment: KXUSISMSERV names Trading
    # Economics, not ISM, as its settlement source.
    market.resolution_source = "Trading Economics"
    fingerprint = build_fingerprint(market)
    assert fingerprint.event_subject == "us_ism_services_pmi|2026-05"
    assert fingerprint.contract_scope == "ism_services_pmi"
    assert fingerprint.threshold == Decimal("54.0")
    assert fingerprint.threshold_operator == ">"
    assert fingerprint.resolution_source == "trading_economics"
    # The older services template publishes no precision and no fallback.
    assert fingerprint.settlement_policy is None


def test_polymarket_manufacturing_tail_canonicalizes():
    fingerprint = build_fingerprint(_market("polymarket_us", PM_MFG_TAIL_TITLE, PM_MFG_RULES))
    assert fingerprint.event_subject == "us_ism_manufacturing_pmi|2026-07"
    assert fingerprint.contract_scope == "ism_manufacturing_pmi"
    assert fingerprint.threshold == Decimal("57.0")
    assert fingerprint.threshold_operator == ">="
    assert fingerprint.threshold_unit == "index_points"
    assert fingerprint.resolution_source == "ism_report_on_business"
    assert fingerprint.settlement_policy == (
        "missing=previous_month_figures_at_next_release|precision=ism_one_decimal"
    )


def test_polymarket_manufacturing_bracket_canonicalizes():
    fingerprint = build_fingerprint(_market("polymarket_us", PM_MFG_BRACKET_TITLE, PM_MFG_RULES))
    assert fingerprint.threshold == Decimal("55.0")
    assert fingerprint.threshold_upper == Decimal("55.9")
    assert fingerprint.threshold_operator == "between_inclusive"
    low = build_fingerprint(_market("polymarket_us", PM_MFG_LOW_TITLE, PM_MFG_RULES))
    assert low.threshold == Decimal("49.0")
    assert low.threshold_operator == "<"


def test_polymarket_services_tail_canonicalizes():
    fingerprint = build_fingerprint(_market("polymarket_us", PM_SERV_TAIL_TITLE, PM_SERV_RULES))
    assert fingerprint.event_subject == "us_ism_services_pmi|2026-07"
    assert fingerprint.contract_scope == "ism_services_pmi"
    assert fingerprint.threshold == Decimal("58.0")
    assert fingerprint.threshold_operator == ">="
    assert fingerprint.resolution_source == "ism_report_on_business"


def test_polymarket_legs_with_terminal_fallback_are_guaranteed():
    for title, rules in (
        (PM_MFG_TAIL_TITLE, PM_MFG_RULES),
        (PM_MFG_BRACKET_TITLE, PM_MFG_RULES),
        (PM_SERV_TAIL_TITLE, PM_SERV_RULES),
    ):
        assessment = assess_settlement_guarantee(_market("polymarket_us", title, rules))
        assert assessment["status"] == GuaranteeStatus.GUARANTEED, title
        assert assessment["reason_codes"] == ["COMPLETE_ISM_RELEASE_AND_MISSING_DATA_POLICY"]


def test_kalshi_legs_stay_unknown():
    """Kalshi manufacturing publishes no missing-release fallback and services
    publishes no source, precision, or fallback at all — neither may be inferred
    complete, and the family's scope gate must keep the generic yes/no-fallback
    grant from ever reaching them."""
    for title, rules in (
        (KALSHI_MFG_TITLE, KALSHI_MFG_RULES.format(strike="57")),
        (KALSHI_SERV_TITLE, KALSHI_SERV_RULES),
    ):
        assessment = assess_settlement_guarantee(_market("kalshi", title, rules))
        assert assessment["status"] == GuaranteeStatus.UNKNOWN, title
        assert "FAMILY_POLICY_INCOMPLETE" in assessment["reason_codes"], title


def test_identical_threshold_pair_blocks_on_exactly_the_published_gaps():
    """Kalshi "At least 57" and Polymarket "57.0+" are the same predicate — same
    threshold, same >= operator, same subject, scope, period, unit, and ISM
    source. The only honest gaps are Polymarket's terminal previous-month
    fallback (absent from Kalshi's published rules) and, in consequence,
    Kalshi's UNKNOWN settlement guarantee."""
    pair = verify_equivalence(
        _market("kalshi", KALSHI_MFG_TITLE, KALSHI_MFG_RULES.format(strike="57")),
        _market("polymarket_us", PM_MFG_TAIL_TITLE, PM_MFG_RULES),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert pair.differences == [
        "SETTLEMENT_POLICY_MISMATCH",
        "SETTLEMENT_GUARANTEE_UNKNOWN",
    ]


def test_complement_tail_pair_blocks_on_exactly_the_published_gaps():
    """Polymarket "below 49.0" is the exact set complement of a Kalshi "at least
    49" strike (real per-strike template; July's published ladder started at 51).
    The operator complement could only ever flip under the gated inverse rule
    with BOTH legs guaranteed — Kalshi's missing fallback keeps it honest."""
    pair = verify_equivalence(
        _market("kalshi", KALSHI_MFG_TITLE, KALSHI_MFG_RULES.format(strike="49")),
        _market("polymarket_us", PM_MFG_LOW_TITLE, PM_MFG_RULES),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert pair.differences == [
        "SETTLEMENT_POLICY_MISMATCH",
        "THRESHOLD_OPERATOR_MISMATCH",
        "SETTLEMENT_GUARANTEE_UNKNOWN",
    ]


def test_manufacturing_and_services_never_cross_match():
    pair = verify_equivalence(
        _market("kalshi", KALSHI_MFG_TITLE, KALSHI_MFG_RULES.format(strike="58")),
        _market("polymarket_us", PM_SERV_TAIL_TITLE, PM_SERV_RULES),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert "EVENT_SUBJECT_MISMATCH" in pair.differences
    assert "CONTRACT_SCOPE_MISMATCH" in pair.differences


def test_exact_bracket_pair_refuses_on_threshold_terms():
    """A one-decimal bracket (55.0-55.9) is NOT the same predicate as an open
    "at least 55" strike even though the July print (55.x) settled both Yes."""
    pair = verify_equivalence(
        _market("kalshi", KALSHI_MFG_TITLE, KALSHI_MFG_RULES.format(strike="55")),
        _market("polymarket_us", PM_MFG_BRACKET_TITLE, PM_MFG_RULES),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert "THRESHOLD_UPPER_MISMATCH" in pair.differences
    assert "THRESHOLD_OPERATOR_MISMATCH" in pair.differences


def test_unmodeled_comparison_fails_safe_to_no_threshold():
    """An unmodeled bucket phrasing must refuse the threshold outright — never
    fall through to the shared rules text, whose "a reading above 50 indicates
    expansion" explainer would otherwise be read as a strike. A threshold-less
    leg can never be guarantee-complete, so the pair can never approve."""
    market = _market(
        "polymarket_us", "Will ISM Manufacturing PMI be at most 49.0 in July?", PM_MFG_RULES
    )
    fingerprint = build_fingerprint(market)
    assert fingerprint.event_subject == "us_ism_manufacturing_pmi|2026-07"
    assert fingerprint.threshold is None
    assert fingerprint.threshold_operator is None
    assert assess_settlement_guarantee(market)["status"] == GuaranteeStatus.UNKNOWN


def test_sp_global_pmi_is_not_misfiled():
    """The S&P Global US Manufacturing PMI is a different survey: a market that
    merely mentions ISM without the published index phrase must not be captured,
    and a text naming BOTH ISM indices is ambiguous and must not be captured."""
    other_survey = build_fingerprint(
        _market(
            "polymarket_us",
            "Will the S&P Global US Manufacturing PMI be above 52.0 in July 2026?",
            "This market resolves off the S&P Global Flash US Manufacturing PMI, "
            "not the report published by ISM.",
        )
    )
    assert not str(other_survey.event_subject).startswith("us_ism_")
    ambiguous = build_fingerprint(
        _market(
            "polymarket_us",
            "Will the ISM Manufacturing PMI exceed the ISM Services PMI in July 2026?",
            "Resolves Yes if the ISM Manufacturing PMI reading is above the ISM "
            "Services PMI reading for July 2026.",
        )
    )
    assert not str(ambiguous.event_subject).startswith("us_ism_")
