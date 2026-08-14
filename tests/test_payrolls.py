"""US nonfarm-payrolls canonical family against real venue-published texts.

Fixture texts are frozen copies of real venue rules captured 2026-08-14:

- Kalshi ``KXPAYROLLS-26JUL`` (13 strict "above X" markets incl. the negative
  ``T-25000`` strike; settled 2026-08-07 off a negative July print: only
  ``T-25000`` yes) and ``KXPAYROLLS-26JUN`` (``T50000`` yes / ``T60000`` no).
  Kalshi publishes NO revision, missing-data, or precision clauses — that
  absence must stay visible as an empty settlement policy and an UNKNOWN
  guarantee. Known metadata quirk: the KXPAYROLLS series-level
  settlement-source URL points at the BLS PPI release page
  (https://www.bls.gov/news.release/ppi.nr0.htm); the rules TEXT names the
  "Bureau of Labor Statistics Monthly Employment Situation Report" and the
  text is what the normalizer tokenizes.
- Polymarket-US event ``usnfp-sa-july-2026-08-07`` ("Jobs Added in July
  2026"): strict "Above X" outcomes at strikes identical to Kalshi's at
  0/50k/100k, all resolved No; publishes a first-print revision exclusion and
  a terminal three-month previous-month fallback. The June event
  ``uschange-gte-june-2026-07-02`` used "At least X" (>=) phrasing instead —
  the venue switched gte->gt between events, so the operator must be read per
  event. The live adapter surfaces the per-outcome title ("Above 50,000")
  alongside the shared event title; fixtures place it in ``subtitle``.
  The "sa" slug token is not published in the body text, so no adjustment
  basis enters the scope.
- Polymarket-Global event ``how-many-jobs-added-in-july-20260702221813246``
  (6 range buckets; "<0" resolved Yes) and the August sibling: half-open
  [L, U) buckets via the published exact-boundary-to-higher-bracket rule,
  plus a last-available-month fallback and NO revision clause.
"""

from decimal import Decimal

from atlas.backfill import _historical_label
from atlas.fingerprints import build_fingerprint
from atlas.models import MatchStatus
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.venues.fixtures import fixture_markets
from atlas.verification import verify_equivalence

KALSHI_TITLE = "Will above {strike} jobs be added in {month} 2026?"
KALSHI_RULES = (
    "If the increase in total non-farm payroll employment is above {strike} as reported "
    "by the Bureau of Labor Statistics Monthly Employment Situation Report for the month "
    "of {month} 2026, then the market resolves to Yes.\n\n"
    "The market closes at 8:29 AM ET on the expected date of the data release."
)

PM_US_EVENT_TITLE = "Jobs Added in {month} 2026"
PM_US_JULY_RULES = (
    "If the change in total U.S. nonfarm payroll employment reported by the Bureau of "
    "Labor Statistics (BLS) in the Employment Situation Report for July 2026 is above "
    "{strike}, this market will settle to Yes. Outcome verified by BLS. \n\n"
    "Settlement is based on the Employment Situation Report release published by the "
    "Bureau of Labor Statistics (BLS), currently scheduled for release on August 7, "
    "2026. Any subsequent revisions to this figure will not be considered for "
    "settlement purposes.\n\n"
    "If no qualifying change in U.S. nonfarm payroll employment figure for the "
    "specified month is released within three months of the Employment Situation "
    "Report scheduled release date, this market will resolve based on data from the "
    "most recent previous month with available data."
)
PM_US_JUNE_RULES = (
    "If the change in total U.S. nonfarm payroll employment reported by the Bureau of "
    "Labor Statistics (BLS) in the Employment Situation Report for June 2026 is at "
    "least {strike}, this market will settle to Yes. Outcome verified by BLS. \n\n"
    "Settlement is based on the Employment Situation Report release published by the "
    "Bureau of Labor Statistics (BLS), currently scheduled for release on July 2, "
    "2026. Any subsequent revisions to this figure will not be considered for "
    "settlement purposes.\n\n"
    "If no qualifying change in U.S. nonfarm payroll employment figure for the "
    "specified month is released within three months of the Employment Situation "
    "Report scheduled release date, this market will resolve based on data from the "
    "most recent previous month with available data."
)

GAMMA_RULES = (
    "This market will resolve according to the change in the total nonfarm payroll "
    "employment reported by the BLS \"Employment Situation Summary\" for {month} 2026, "
    "scheduled to be released on {release}, at 8:30 AM ET.\n\n"
    "If the reported value falls exactly between two brackets, then this market will "
    "resolve to the higher range bracket.\n\n"
    "If no data for the specified month is released by the date the next month's data "
    "is scheduled to be released, this market will resolve based on data from the last "
    "available month.\n\n"
    "The BLS \"Employment Situation Summary\" may be found here: "
    "https://www.bls.gov/bls/newsrels.htm"
)
GAMMA_JULY_RULES = GAMMA_RULES.format(month="July", release="August 7, 2026")
GAMMA_AUGUST_RULES = GAMMA_RULES.format(month="August", release="September 4, 2026")

PM_US_POLICY = "revision=first_official_release|missing=previous_month_within_3m"
GAMMA_POLICY = "missing=last_available_month_at_next_release|boundary=exact_to_higher_bracket"


def _market(venue_key: str, title: str, rules: str, subtitle: str | None = None):
    market = fixture_markets()[venue_key][0]
    market.title = title
    market.subtitle = subtitle
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


def _kalshi(strike: str, month: str = "July"):
    return _market(
        "kalshi",
        KALSHI_TITLE.format(strike=strike, month=month),
        KALSHI_RULES.format(strike=strike, month=month),
    )


def _pm_july(outcome: str, strike: str):
    return _market(
        "polymarket_us",
        PM_US_EVENT_TITLE.format(month="July"),
        PM_US_JULY_RULES.format(strike=strike),
        subtitle=outcome,
    )


def _pm_june(outcome: str, strike: str):
    return _market(
        "polymarket_us",
        PM_US_EVENT_TITLE.format(month="June"),
        PM_US_JUNE_RULES.format(strike=strike),
        subtitle=outcome,
    )


def _gamma(title: str, rules: str = GAMMA_JULY_RULES):
    # Polymarket-Global (Gamma) markets carry the bucket in the question title;
    # the shared fixture market stands in for the venue payload.
    return _market("polymarket_us", title, rules)


def test_kalshi_strike_canonicalizes_with_empty_policy():
    fingerprint = build_fingerprint(_kalshi("90000"))
    assert fingerprint.event_subject == "us_nonfarm_payrolls|2026-07"
    assert fingerprint.contract_scope == "nonfarm_payrolls"
    assert fingerprint.threshold == Decimal(90000)
    assert fingerprint.threshold_operator == ">"
    assert fingerprint.threshold_upper is None
    assert fingerprint.threshold_unit == "jobs"
    # The rules TEXT names the Employment Situation Report; the series-level
    # settlement-source URL (which points at the BLS PPI page) is not consulted.
    assert fingerprint.resolution_source == "us_bls_employment_situation"
    # Kalshi publishes no revision, missing-data, or precision clauses; the
    # absence must stay visible rather than being inferred away.
    assert fingerprint.settlement_policy is None
    assert fingerprint.revision_policy is None


def test_kalshi_negative_strike_parses():
    fingerprint = build_fingerprint(_kalshi("-25000"))
    assert fingerprint.event_subject == "us_nonfarm_payrolls|2026-07"
    assert fingerprint.threshold == Decimal(-25000)
    assert fingerprint.threshold_operator == ">"


def test_comma_and_unicode_minus_strikes_parse():
    # Kalshi's subtitle spelling ("Above -25,000") and the unicode-minus
    # variant of it must both read as the same negative raw count.
    for spelled in ("-25,000", "−25,000"):
        fingerprint = build_fingerprint(_kalshi(spelled))
        assert fingerprint.threshold == Decimal(-25000), spelled
        assert fingerprint.threshold_operator == ">", spelled


def test_polymarket_us_july_above_canonicalizes():
    fingerprint = build_fingerprint(_pm_july("Above 50,000", "50,000"))
    assert fingerprint.event_subject == "us_nonfarm_payrolls|2026-07"
    # The "sa" slug token is not published in the body text, so no adjustment
    # basis may enter the scope.
    assert fingerprint.contract_scope == "nonfarm_payrolls"
    assert fingerprint.threshold == Decimal(50000)
    assert fingerprint.threshold_operator == ">"
    assert fingerprint.threshold_unit == "jobs"
    assert fingerprint.resolution_source == "us_bls_employment_situation"
    assert fingerprint.settlement_policy == PM_US_POLICY
    assert fingerprint.revision_policy == "first_official_release"


def test_polymarket_us_june_at_least_canonicalizes():
    """The June event published "At least X" (>=) where July published strict
    "Above X" (>) — the operator must be read from each event's own text."""
    fingerprint = build_fingerprint(_pm_june("At least 50,000", "50,000"))
    assert fingerprint.event_subject == "us_nonfarm_payrolls|2026-06"
    assert fingerprint.threshold == Decimal(50000)
    assert fingerprint.threshold_operator == ">="
    assert fingerprint.settlement_policy == PM_US_POLICY


def test_gamma_range_buckets_canonicalize_half_open():
    """"between 0 and 50k" plus the published exact-boundary-to-higher-bracket
    rule is the half-open [0, 50000) bucket; "lose between 0 and 50k" is the
    negated range [-50000, 0)."""
    added = build_fingerprint(_gamma("Will the US add between 0 and 50k jobs in July?"))
    assert added.event_subject == "us_nonfarm_payrolls|2026-07"
    assert added.threshold == Decimal(0)
    assert added.threshold_upper == Decimal(50000)
    assert added.threshold_operator == "between_left_inclusive"
    assert added.settlement_policy == GAMMA_POLICY
    assert added.revision_policy is None
    lost = build_fingerprint(
        _gamma("Will the US lose between 0 and 50k jobs in August?", GAMMA_AUGUST_RULES)
    )
    assert lost.event_subject == "us_nonfarm_payrolls|2026-08"
    assert lost.threshold == Decimal(-50000)
    assert lost.threshold_upper == Decimal(0)
    assert lost.threshold_operator == "between_left_inclusive"


def test_gamma_tail_buckets_canonicalize():
    lose = build_fingerprint(_gamma("Will the US lose jobs in July?"))
    assert (lose.threshold, lose.threshold_upper, lose.threshold_operator) == (
        Decimal(0),
        None,
        "<",
    )
    lose_deep = build_fingerprint(
        _gamma("Will the US lose more than 50k jobs in August?", GAMMA_AUGUST_RULES)
    )
    assert (lose_deep.threshold, lose_deep.threshold_operator) == (Decimal(-50000), "<")
    assert lose_deep.event_subject == "us_nonfarm_payrolls|2026-08"
    top = build_fingerprint(_gamma("Will the US add at least 200k jobs in July?"))
    assert (top.threshold, top.threshold_operator) == (Decimal(200000), ">=")


def test_range_without_published_boundary_rule_refuses_threshold():
    """Adjacent integer buckets share their endpoints; without the published
    exact-boundary rule the membership of 0 and 50,000 is unpublished, so the
    threshold is refused rather than inferred."""
    no_boundary_rules = (
        "This market will resolve according to the change in the total nonfarm payroll "
        "employment reported by the BLS \"Employment Situation Summary\" for July 2026."
    )
    fingerprint = build_fingerprint(
        _gamma("Will the US add between 0 and 50k jobs in July?", no_boundary_rules)
    )
    assert fingerprint.event_subject == "us_nonfarm_payrolls|2026-07"
    assert fingerprint.threshold is None
    assert fingerprint.threshold_operator is None


def test_unmodeled_phrasings_fail_safe_to_no_threshold():
    """Directional phrasings outside the modeled vocabulary must refuse the
    threshold outright — never fall through to later texts — so the leg can
    never look guarantee-complete."""
    for title in (
        "Will the US shed more than 50k jobs in July?",
        "Will nonfarm payrolls increase by more than 100,000 in July 2026?",
    ):
        market = _gamma(title)
        fingerprint = build_fingerprint(market)
        assert fingerprint.event_subject == "us_nonfarm_payrolls|2026-07", title
        assert fingerprint.threshold is None, title
        assert fingerprint.threshold_operator is None, title
        assert assess_settlement_guarantee(market)["status"] == GuaranteeStatus.UNKNOWN


def test_title_bucket_wins_over_sibling_enumeration():
    """The title states the market's OWN bucket; a description enumerating the
    sibling ladder must never re-strike the leg."""
    market = _kalshi("100000")
    market.raw_rules_text += (
        "\n\nThis event also lists markets for Above 0, Above 50000, and Above 125000."
    )
    fingerprint = build_fingerprint(market)
    assert fingerprint.threshold == Decimal(100000)
    assert fingerprint.threshold_operator == ">"


def test_polymarket_us_legs_with_revision_and_fallback_are_guaranteed():
    for market in (
        _pm_july("Above 50,000", "50,000"),
        _pm_june("At least 50,000", "50,000"),
    ):
        assessment = assess_settlement_guarantee(market)
        assert assessment["status"] == GuaranteeStatus.GUARANTEED
        assert assessment["reason_codes"] == ["COMPLETE_PAYROLLS_RELEASE_POLICY"]


def test_kalshi_and_gamma_legs_stay_unknown():
    """Kalshi publishes no revision or missing-data clauses at all, and Gamma
    publishes a fallback but no revision clause — neither may be inferred
    complete."""
    for market in (
        _kalshi("50000"),
        _gamma("Will the US add between 0 and 50k jobs in July?"),
    ):
        assessment = assess_settlement_guarantee(market)
        assert assessment["status"] == GuaranteeStatus.UNKNOWN
        assert "FAMILY_POLICY_INCOMPLETE" in assessment["reason_codes"]


def test_payrolls_scope_cannot_reach_guaranteed_via_generic_binary_fallback():
    """An explicit Otherwise-No sentence must not hand this family GUARANTEED
    through the generic yes/no-fallback grant."""
    market = _kalshi("50000")
    market.raw_rules_text = (
        "If the increase in total non-farm payroll employment is above 50000 as "
        "reported by the Bureau of Labor Statistics Monthly Employment Situation "
        "Report for the month of July 2026, then the market resolves to Yes. "
        "Otherwise, this market resolves to No."
    )
    fingerprint = build_fingerprint(market)
    assert fingerprint.contract_scope == "nonfarm_payrolls"
    assessment = assess_settlement_guarantee(market, fingerprint)
    assert assessment["status"] == GuaranteeStatus.UNKNOWN
    assert "FAMILY_POLICY_INCOMPLETE" in assessment["reason_codes"]


def test_identical_strike_pairs_block_on_exactly_the_published_gaps():
    """Kalshi's July ladder and Polymarket-US's July event share strict ">"
    strikes at 0 / 50,000 / 100,000 — identical subject, scope, threshold,
    operator, unit, period, and BLS source. The only honest remaining gaps are
    Kalshi's unpublished revision and missing-data clauses (settlement policy,
    revision policy) and, in consequence, its UNKNOWN settlement guarantee.
    Both legs of every pair settled No off the negative July print; agreeing
    outcomes on a review pair prove nothing and must stay unlabeled."""
    for kalshi_strike, pm_strike in (("0", "0"), ("50000", "50,000"), ("100000", "100,000")):
        pair = verify_equivalence(
            _kalshi(kalshi_strike),
            _pm_july(f"Above {pm_strike}", pm_strike),
        )
        assert pair.status is MatchStatus.REVIEW_REQUIRED, kalshi_strike
        assert pair.differences == [
            "SETTLEMENT_POLICY_MISMATCH",
            "REVISION_POLICY_MISMATCH",
            "SETTLEMENT_GUARANTEE_UNKNOWN",
        ], kalshi_strike
        # Real settled outcomes 2026-08-07: both legs No.
        assert _historical_label(pair, "no", "no") == (None, "INCONCLUSIVE")


def test_june_boundary_divergent_pair_blocks_on_operator():
    """June is the census's payrolls boundary divergence: Kalshi ">" 50000 vs
    Polymarket-US ">=" 50000 differ exactly at a 50,000 print. Both settled Yes
    off the June print in (50k, 60k], which proves nothing for a review pair."""
    pair = verify_equivalence(
        _kalshi("50000", month="June"),
        _pm_june("At least 50,000", "50,000"),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert pair.differences == [
        "SETTLEMENT_POLICY_MISMATCH",
        "THRESHOLD_OPERATOR_MISMATCH",
        "REVISION_POLICY_MISMATCH",
        "SETTLEMENT_GUARANTEE_UNKNOWN",
    ]
    assert _historical_label(pair, "yes", "yes") == (None, "INCONCLUSIVE")


def test_zero_strike_vs_gamma_lose_bucket_diverged_and_rejected():
    """Kalshi ">0" and Gamma "<0" leave a gap at exactly zero — not a
    complement — and the real settled outcomes diverged (July print -23,000:
    Kalshi No, Gamma Yes), which earns the evidence-backed REJECTED label for
    this same-canonical-subject review pair. It must never approve."""
    pair = verify_equivalence(
        _kalshi("0"),
        _gamma("Will the US lose jobs in July?"),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert pair.differences == [
        "SETTLEMENT_POLICY_MISMATCH",
        "THRESHOLD_OPERATOR_MISMATCH",
        "SETTLEMENT_GUARANTEE_UNKNOWN",
    ]
    assert _historical_label(pair, "no", "yes") == ("REJECTED", "DIVERGED")


def test_payrolls_is_not_misfiled_under_cpi_or_unemployment():
    fingerprint = build_fingerprint(_kalshi("50000"))
    assert not str(fingerprint.event_subject).startswith(("us_cpi", "us_unemployment"))
    assert not str(fingerprint.contract_scope).startswith(("cpi_", "unemployment_rate"))


def test_cpi_and_unemployment_are_not_misfiled_under_payrolls():
    u3 = build_fingerprint(
        _market(
            "kalshi",
            "Will the unemployment rate (U-3) be above 4.1% in July?",
            "If the seasonally adjusted unemployment rate (U-3) reported by the Bureau "
            "of Labor Statistics in the Employment Situation Report is above 4.1% in "
            "July 2026, then the market resolves to Yes.",
        )
    )
    assert u3.event_subject == "us_unemployment_rate|2026-07"
    assert not str(u3.contract_scope).startswith("nonfarm_payrolls")
    cpi = build_fingerprint(
        _market(
            "kalshi",
            "Will CPI rise more than 0.1% in July 2026?",
            "If the Consumer Price Index (CPI) increases by more than 0.1% "
            "(single-decimal) in July 2026, then the market resolves to Yes.",
        )
    )
    assert cpi.event_subject == "us_cpi_mom|2026-07"
    assert not str(cpi.contract_scope).startswith("nonfarm_payrolls")


def test_foreign_jobs_contract_is_not_misfiled_under_us_subject():
    fingerprint = build_fingerprint(
        _market(
            "polymarket_us",
            "Will Canada add at least 50,000 jobs in July 2026?",
            "If the change in Canada nonfarm payroll employment reported by Statistics "
            "Canada for July 2026 is at least 50,000, this market will settle to Yes.",
        )
    )
    assert not str(fingerprint.event_subject).startswith("us_nonfarm_payrolls")
    assert not str(fingerprint.contract_scope or "").startswith("nonfarm_payrolls")
