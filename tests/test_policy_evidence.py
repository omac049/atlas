from atlas.models import Market, VenueName
from atlas.policy_evidence import (
    CancellationDisposition,
    EvidenceOrigin,
    PolicyCompatibilityStatus,
    RevisionPolicy,
    assess_policy_compatibility,
    parse_market_policy_evidence,
    parse_policy_evidence,
)

WEATHER_CONDITION = (
    "the highest temperature recorded at Central Park (KNYC) in New York City for "
    "Aug 9, 2026 as reported by the National Weather Service's Climatological Report "
    "(Daily) is between 90F and 91F"
)


def _weather_rules(
    *,
    no_rule: str = "Otherwise, this market resolves to No.",
    cancellation: str = "If the observation is canceled, the market resolves to No.",
    revision: str = "The official and final value is taken from the latest version of that report.",
    verb: str = "resolves",
) -> str:
    return (
        f"If {WEATHER_CONDITION}, then the market {verb} to Yes. "
        f"{no_rule} {cancellation} {revision}"
    )


def test_current_weather_style_extracts_nws_latest_final_but_blocks_missing_cancellation():
    rules = (
        f"If {WEATHER_CONDITION}, then the market resolves to Yes. "
        "The official and final value is taken from the latest version of that report. "
        "Otherwise, this market resolves to No."
    )

    evidence = parse_policy_evidence(rules)

    assert evidence.affirmative_branch is not None
    assert evidence.negative_branch is not None
    assert evidence.revision.policy == RevisionPolicy.LATEST_FINAL_REPORT
    assert evidence.authoritative_source.canonical_name == "nws_climatological_report_daily"
    assert evidence.authoritative_source.origin == EvidenceOrigin.RULE_TEXT
    assert evidence.blockers == ["MISSING_CANCELLATION_POLICY"]
    assert evidence.complete is False


def test_complete_weather_policy_has_all_required_evidence():
    evidence = parse_policy_evidence(_weather_rules())

    assert evidence.complete is True
    assert evidence.blockers == []
    assert evidence.cancellation.disposition == CancellationDisposition.NO
    assert evidence.cancellation.guaranteed is True


def test_absent_explicit_no_is_blocked():
    evidence = parse_policy_evidence(_weather_rules(no_rule=""))

    assert "MISSING_NEGATIVE_BRANCH" in evidence.blockers
    assert evidence.negative_branch is None
    assert evidence.complete is False


def test_fair_price_cancellation_is_explicit_but_non_guaranteed():
    rules = _weather_rules(
        cancellation=(
            "If the observation is canceled, the exchange will settle the market at a "
            "fair market price."
        )
    )
    evidence = parse_policy_evidence(rules)

    assert evidence.cancellation.disposition == CancellationDisposition.FAIR_PRICE
    assert evidence.cancellation.guaranteed is False
    assert evidence.blockers == ["NON_GUARANTEED_CANCELLATION_POLICY"]


def test_void_with_explicit_refund_is_extracted_deterministically():
    rules = _weather_rules(
        cancellation="If the observation is voided, all positions and stakes are refunded."
    )
    evidence = parse_policy_evidence(rules)

    assert evidence.cancellation.disposition == CancellationDisposition.VOID_REFUND
    assert evidence.cancellation.guaranteed is True
    assert evidence.complete is True


def test_conflicting_revision_policies_are_a_mismatch():
    latest = parse_policy_evidence(_weather_rules())
    first = parse_policy_evidence(
        _weather_rules(
            revision="Any subsequent revisions to the first published report will not be used."
        )
    )

    assessment = assess_policy_compatibility(latest, first)

    assert assessment.status == PolicyCompatibilityStatus.MISMATCH
    assert assessment.compatible is False
    assert assessment.mismatch_codes == ["REVISION_POLICY_MISMATCH"]


def test_conflicting_revision_language_within_one_policy_is_blocked():
    evidence = parse_policy_evidence(
        _weather_rules(
            revision=(
                "Any subsequent revisions to the first published report will not be used. "
                "The official and final value is taken from the latest version of that report."
            )
        )
    )

    assert evidence.revision.policy == RevisionPolicy.CONFLICTING
    assert set(evidence.revision.matched_policies) == {
        RevisionPolicy.FIRST_OFFICIAL_RELEASE,
        RevisionPolicy.LATEST_FINAL_REPORT,
    }
    assert "CONFLICTING_REVISION_POLICY" in evidence.blockers


def test_equivalent_wording_is_compatible_but_not_exact():
    left = parse_policy_evidence(_weather_rules())
    right = parse_policy_evidence(
        _weather_rules(
            no_rule="Otherwise the market will settle to No.",
            cancellation="If the observation is cancelled, the market will settle to No.",
            revision="Use the latest final report as the official value.",
            verb="settles",
        ),
        authoritative_source="NWS Climatological Report Daily",
    )

    assessment = assess_policy_compatibility(left, right)

    assert assessment.status == PolicyCompatibilityStatus.COMPATIBLE
    assert assessment.compatible is True
    assert assessment.mismatch_codes == []


def test_identical_complete_policy_is_exact():
    left = parse_policy_evidence(_weather_rules())
    right = parse_policy_evidence(_weather_rules())

    assessment = assess_policy_compatibility(left, right)

    assert assessment.status == PolicyCompatibilityStatus.EXACT
    assert assessment.compatible is True


def test_incomplete_policies_are_never_compatible_even_when_identical():
    left = parse_policy_evidence("The market resolves to Yes if the report exceeds 90F.")
    right = parse_policy_evidence("The market resolves to Yes if the report exceeds 90F.")

    assessment = assess_policy_compatibility(left, right)

    assert assessment.status == PolicyCompatibilityStatus.INCOMPLETE
    assert assessment.compatible is False
    assert "LEFT_MISSING_NEGATIVE_BRANCH" in assessment.blockers
    assert "RIGHT_MISSING_AUTHORITATIVE_SOURCE" in assessment.blockers


def test_authoritative_source_mismatch_is_not_compatible():
    left = parse_policy_evidence(_weather_rules())
    right = parse_policy_evidence(
        _weather_rules().replace(
            "National Weather Service's Climatological Report (Daily)",
            "The Weather Company",
        )
    )

    assessment = assess_policy_compatibility(left, right)

    assert assessment.status == PolicyCompatibilityStatus.MISMATCH
    assert assessment.mismatch_codes == ["AUTHORITATIVE_SOURCE_MISMATCH"]


def test_market_adapter_never_infers_source_or_policy_from_title_and_category():
    market = Market(
        market_id="weather-1",
        venue=VenueName.KALSHI,
        venue_market_id="weather-1",
        title="NWS daily weather: latest final report",
        category="weather",
        resolution_source="unknown",
        resolution_text="The market resolves to Yes if the value is above 90F.",
        event_subject="weather_temperature|knyc|2026-08-09",
        event_action="daily_max_temperature",
    )

    evidence = parse_market_policy_evidence(market)

    assert evidence.authoritative_source is None
    assert evidence.revision is None
    assert "MISSING_AUTHORITATIVE_SOURCE" in evidence.blockers
    assert "MISSING_REVISION_POLICY" in evidence.blockers
    assert evidence.field_presence.raw_rules_text is False
    assert evidence.field_presence.resolution_text is True
    assert evidence.field_presence.resolution_source is False
    assert evidence.field_presence.cancellation_policy is False


def test_explicit_yes_no_market_sides_supply_binary_branches_not_exception_policy():
    question = (
        "Will the highest temperature recorded at KSFO on 2026-08-10 be between "
        "72F and 73F?"
    )
    market = Market(
        market_id="weather-sides-1",
        venue=VenueName.POLYMARKET_US,
        venue_market_id="weather-sides-1",
        title="72 to 73",
        resolution_source="NWS Climatological Report Daily",
        resolution_text=question,
        raw_rules_text=question,
        event_subject="weather_temperature|ksfo|2026-08-10",
        event_action="daily_max_temperature",
        raw_market_json={
            "question": question,
            "marketSides": [
                {"long": True, "description": "Yes"},
                {"long": False, "description": "No"},
            ],
        },
    )
    evidence = parse_market_policy_evidence(market)
    assert evidence.affirmative_branch is not None
    assert evidence.negative_branch is not None
    assert "MISSING_AFFIRMATIVE_BRANCH" not in evidence.blockers
    assert "MISSING_NEGATIVE_BRANCH" not in evidence.blockers
    assert "MISSING_CANCELLATION_POLICY" in evidence.blockers
    assert "MISSING_REVISION_POLICY" in evidence.blockers
    assert evidence.complete is False
    assert evidence.field_presence.explicit_binary_sides is True
    assert evidence.field_presence.affirmative_branch is True
    assert evidence.field_presence.negative_branch is True
    assert evidence.field_presence.cancellation_policy is False


def test_kalshi_published_binary_structure_supplies_negative_branch():
    rules = (
        "If the maximum temperature recorded at San Francisco for Aug 12, 2026, is "
        "between 70-71° fahrenheit according to the National Weather Service's "
        "Climatological Report (Daily), then the market resolves to Yes. Please use "
        "the latest version of the data for the desired date."
    )
    market = Market(
        market_id="kalshi:KXHIGHTSFO-26AUG12-B70.5",
        venue=VenueName.KALSHI,
        venue_market_id="KXHIGHTSFO-26AUG12-B70.5",
        title="Will the maximum temperature be 70-71° on Aug 12, 2026?",
        resolution_source="NWS Climatological Report San Francisco",
        resolution_text=rules,
        raw_rules_text=rules,
        event_subject="weather_temperature|ksfo|2026-08-12",
        event_action="daily_max_temperature",
        raw_market_json={
            "ticker": "KXHIGHTSFO-26AUG12-B70.5",
            "market_type": "binary",
            "yes_sub_title": "70° to 71°",
            "no_sub_title": "Not 70° to 71°",
        },
    )

    evidence = parse_market_policy_evidence(market)

    assert evidence.affirmative_branch is not None
    assert evidence.negative_branch is not None
    assert evidence.negative_branch.condition == (
        f"complement_of:{evidence.affirmative_branch.condition}"
    )
    assert "MISSING_AFFIRMATIVE_BRANCH" not in evidence.blockers
    assert "MISSING_NEGATIVE_BRANCH" not in evidence.blockers
    assert "MISSING_CANCELLATION_POLICY" in evidence.blockers
    assert evidence.complete is False
    assert evidence.field_presence.explicit_binary_sides is True
    assert evidence.field_presence.negative_branch is True
    assert evidence.field_presence.cancellation_policy is False


def test_binary_structure_never_fabricates_an_affirmative_branch():
    market = Market(
        market_id="kalshi:KXNOAFFIRM-1",
        venue=VenueName.KALSHI,
        venue_market_id="KXNOAFFIRM-1",
        title="Will something happen?",
        resolution_source="unknown",
        resolution_text="Settlement details are described in the rulebook.",
        raw_rules_text="Settlement details are described in the rulebook.",
        event_subject="misc|2026-08-12",
        event_action="misc",
        raw_market_json={
            "ticker": "KXNOAFFIRM-1",
            "market_type": "binary",
            "yes_sub_title": "Yes",
            "no_sub_title": "No",
        },
    )

    evidence = parse_market_policy_evidence(market)

    assert evidence.affirmative_branch is None
    assert evidence.negative_branch is None
    assert "MISSING_AFFIRMATIVE_BRANCH" in evidence.blockers
    assert "MISSING_NEGATIVE_BRANCH" in evidence.blockers


def test_non_binary_kalshi_market_does_not_get_a_complement_branch():
    rules = (
        "If the reported value is above 10, then the market resolves to Yes."
    )
    market = Market(
        market_id="kalshi:KXSCALAR-1",
        venue=VenueName.KALSHI,
        venue_market_id="KXSCALAR-1",
        title="Scalar market",
        resolution_source="unknown",
        resolution_text=rules,
        raw_rules_text=rules,
        event_subject="misc|2026-08-12",
        event_action="misc",
        raw_market_json={"ticker": "KXSCALAR-1", "market_type": "scalar"},
    )

    evidence = parse_market_policy_evidence(market)

    assert evidence.negative_branch is None
    assert "MISSING_NEGATIVE_BRANCH" in evidence.blockers
    assert evidence.field_presence.explicit_binary_sides is False
