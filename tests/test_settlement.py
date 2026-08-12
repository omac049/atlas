from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.venues.fixtures import fixture_markets
from atlas.venues.polymarket_us import PolymarketUSVenue


def test_fair_price_clause_is_non_guaranteed():
    market = fixture_markets()["kalshi"][0]
    market.raw_rules_text += " Cancellation resolves to fair market price."
    assessment = assess_settlement_guarantee(market)
    assert assessment["status"] == GuaranteeStatus.NON_GUARANTEED
    assert assessment["reason_codes"] == ["DISCRETIONARY_FAIR_PRICE_SETTLEMENT"]


def test_complete_house_control_tiebreak_is_guaranteed():
    description = """This market will settle to Yes if the Democratic Party wins control of the
    United States House of Representatives in the 2026 United States midterm election. A party
    wins control if it wins a majority of the chamber's voting seats. If no party wins a majority,
    control is determined by the party with which the first elected Speaker of the House is
    affiliated. Outcome sourced from relevant state electoral authorities, and the United States
    House of Representatives."""
    market = PolymarketUSVenue._normalize_market(
        {
            "slug": "house-control-dem",
            "title": "Democratic Party",
            "question": "U.S House Midterm Winner",
            "description": description,
            "resolutionSource": (
                "relevant state electoral authorities, and the United States House of Representatives"
            ),
            "gameStartTime": "2026-11-03T00:00:00Z",
            "marketType": "election",
        }
    )
    assessment = assess_settlement_guarantee(market)
    assert assessment["status"] == GuaranteeStatus.GUARANTEED


def test_explicit_yes_no_fallback_is_guaranteed():
    market = fixture_markets()["kalshi"][0]
    market.raw_rules_text = (
        "The market resolves to Yes if the rate is raised by the deadline. "
        "Otherwise the market will resolve to No."
    )
    assessment = assess_settlement_guarantee(market)
    assert assessment["status"] == GuaranteeStatus.GUARANTEED


def test_missing_exception_policy_remains_unknown():
    market = fixture_markets()["kalshi"][0]
    market.raw_rules_text = "Resolve YES if the Federal Reserve raises rates."
    assessment = assess_settlement_guarantee(market)
    assert assessment["status"] == GuaranteeStatus.UNKNOWN
    assert "UNPARSED_SETTLEMENT_POLICY" in assessment["reason_codes"]


def test_quoted_yes_no_outcome_words_are_recognized_as_explicit_fallback():
    """Polymarket Global's real boilerplate quotes the outcome word, e.g.
    `resolve to "Yes" if ... Otherwise, this market will resolve to "No".` The
    unquoted-only regex previously missed this and left every such market UNKNOWN."""
    market = fixture_markets()["kalshi"][0]
    market.raw_rules_text = (
        'This market will resolve to "Yes" if the event occurs by the deadline. '
        'Otherwise, this market will resolve to "No".'
    )
    assessment = assess_settlement_guarantee(market)
    assert assessment["status"] == GuaranteeStatus.GUARANTEED
    assert assessment["reason_codes"] == ["EXPLICIT_YES_NO_FALLBACK"]


def test_curly_quoted_yes_no_outcome_words_are_recognized_as_explicit_fallback():
    market = fixture_markets()["kalshi"][0]
    market.raw_rules_text = (
        "This market will resolve to “Yes” if the event occurs by the deadline. "
        "Otherwise, this market will resolve to “No”."
    )
    assessment = assess_settlement_guarantee(market)
    assert assessment["status"] == GuaranteeStatus.GUARANTEED
    assert assessment["reason_codes"] == ["EXPLICIT_YES_NO_FALLBACK"]
