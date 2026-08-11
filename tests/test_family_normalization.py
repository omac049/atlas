from atlas.discovery import compatibility_report
from atlas.fingerprints import build_fingerprint
from atlas.normalization import _parse_date_text
from atlas.settlement import GuaranteeStatus, assess_settlement_guarantee
from atlas.venues.kalshi import KalshiVenue
from atlas.venues.polymarket_us import PolymarketUSVenue
from atlas.verification import verify_equivalence

EXPLICIT_NO = " Otherwise, this market resolves to No."


def test_normalization_accepts_common_four_letter_september_abbreviation():
    assert _parse_date_text("sept 16, 2026").date().isoformat() == "2026-09-16"


def test_cpi_first_release_and_missing_data_policy_normalize_across_venues():
    rules = (
        "U.S. inflation, before seasonal adjustment, over the 12-month period ending in "
        "July 2026 (CPI YoY) is above 3.1%, then the market resolves to Yes. "
        "Outcome sourced from BLS. Settlement is based on the Consumer Price Index release "
        "published by BLS. Any subsequent revisions to this figure will not be considered. "
        "If BLS does not publish CPI YoY for July 2026, the first figure officially published "
        "within three months will be used; otherwise use the most recent previous month."
        + EXPLICIT_NO
    )
    kalshi = KalshiVenue._normalize_market(
        {
            "ticker": "KXCPI-26JUL-3.1",
            "title": "Will CPI YoY in July 2026 be above 3.1%?",
            "rules_primary": "If " + rules,
            "status": "open",
        }
    )
    polymarket = PolymarketUSVenue._normalize_market(
        {
            "slug": "cpic-uscpi-july-yoy-2026-08-12-gt3pt1pct",
            "title": "Above 3.1%",
            "question": "CPI YoY in July",
            "description": "This market will settle to Yes if " + rules,
            "resolutionSource": "BLS",
            "status": "MARKET_STATUS_OPEN",
        }
    )
    left, right = build_fingerprint(kalshi), build_fingerprint(polymarket)
    assert left.event_subject == right.event_subject == "us_cpi_yoy|2026-07"
    assert left.threshold == right.threshold
    assert left.revision_policy == right.revision_policy == "first_official_release"
    assert assess_settlement_guarantee(kalshi, left)["status"] == GuaranteeStatus.GUARANTEED
    assert verify_equivalence(kalshi, polymarket).status.value == "APPROVED_EQUIVALENT"


def _weather_market(venue: str, upper: int = 91):
    rules = (
        "If the highest temperature recorded at Central Park (KNYC) in New York City for "
        f"Aug 9, 2026 as reported by the National Weather Service's Climatological Report "
        f"(Daily) is between 90F and {upper}F, then the market resolves to Yes. The official "
        "and final value is taken from the latest version of that report."
        " If the observation is canceled, the market resolves to No." + EXPLICIT_NO
    )
    if venue == "kalshi":
        return KalshiVenue._normalize_market(
            {
                "ticker": "KXHIGHTNYC-26AUG09-B90",
                "title": f"Will the highest temperature be 90-{upper}° on Aug 9, 2026?",
                "rules_primary": rules,
                "floor_strike": 90,
                "cap_strike": upper,
                "strike_type": "between",
                "status": "open",
            }
        )
    return PolymarketUSVenue._normalize_market(
        {
            "slug": f"tc-temp-nychigh-2026-08-09-gte90lt{upper}f",
            "title": f"90 to {upper}",
            "question": "Highest temperature in NYC on August 9?",
            "description": rules,
            "status": "MARKET_STATUS_OPEN",
        }
    )


def test_weather_station_range_and_revision_policy_are_deterministic():
    kalshi, polymarket = _weather_market("kalshi"), _weather_market("polymarket")
    fingerprint = build_fingerprint(polymarket)
    assert fingerprint.event_subject == "weather_temperature|knyc|2026-08-09"
    assert fingerprint.threshold_upper == 91
    assert fingerprint.threshold_operator == "between_inclusive"
    assert verify_equivalence(kalshi, polymarket).status.value == "APPROVED_EQUIVALENT"


def test_weather_range_upper_bound_prevents_false_match():
    pair = verify_equivalence(_weather_market("kalshi"), _weather_market("polymarket", 92))
    assert "THRESHOLD_UPPER_MISMATCH" in pair.differences


def _crypto_market(venue: str, trimmed: bool = False):
    method = (
        "using a trimmed mean over the sixty second period, excluding the top 20% and "
        "bottom 20% of values"
        if trimmed
        else "using the simple average of the sixty seconds"
    )
    rules = (
        "The price of Bitcoin according to CF Benchmarks' Bitcoin Real-Time Index (BRTI) "
        "is between 35,000 and 39,999.99 at 5 PM EDT on Aug 14, 2026, then the market "
        f"resolves to Yes. The final value is calculated {method}." + EXPLICIT_NO
    )
    if venue == "kalshi":
        return KalshiVenue._normalize_market(
            {
                "ticker": "KXBTC-26AUG1417-B37500",
                "title": "Bitcoin price range on Aug 14, 2026?",
                "rules_primary": "If " + rules,
                "floor_strike": 35000,
                "cap_strike": 39999.99,
                "strike_type": "between",
                "status": "open",
            }
        )
    return PolymarketUSVenue._normalize_market(
        {
            "slug": "cpc-btc-pricerange-2026-08-14-37500",
            "title": "35,000 to 39,999.99",
            "question": "Bitcoin price at 5 PM EDT on Aug 14, 2026?",
            "description": "This market will settle to Yes if " + rules,
            "status": "MARKET_STATUS_OPEN",
        }
    )


def test_crypto_index_window_and_range_normalize_across_venues():
    kalshi, polymarket = _crypto_market("kalshi"), _crypto_market("polymarket")
    fingerprint = build_fingerprint(kalshi)
    assert fingerprint.event_subject == "crypto_price|btc|2026-08-14T21:00:00Z"
    assert fingerprint.resolution_source == "cf_benchmarks_brti"
    assert fingerprint.contract_scope == "price_at|60s_simple_average"
    assert verify_equivalence(kalshi, polymarket).status.value == "APPROVED_EQUIVALENT"


def test_crypto_methodology_difference_is_never_collapsed():
    pair = verify_equivalence(_crypto_market("kalshi"), _crypto_market("polymarket", True))
    assert "CONTRACT_SCOPE_MISMATCH" in pair.differences


def test_family_coverage_counts_normalized_shared_and_exact_contracts():
    report = compatibility_report(
        [_weather_market("kalshi"), _crypto_market("kalshi")],
        [_weather_market("polymarket"), _crypto_market("polymarket")],
    )
    coverage = report["normalization_coverage"]
    assert coverage["normalized_contracts"] == 4
    assert coverage["shared_events"] == 2
    assert coverage["guaranteed_shared_events"] == 2
    assert coverage["exact_rule_matches"] == 2
    for family in ("weather", "crypto"):
        assert coverage["families"][family]["status"] == "EXACT_RULE_MATCH"
        assert coverage["families"][family]["kalshi_normalized"] == 1
        assert coverage["families"][family]["polymarket_normalized"] == 1


def test_state_election_winner_is_not_misclassified_as_primary():
    rules = (
        "If the Republican Party nominee wins the 2026 election for Governor of Kansas, "
        "this market will settle to Yes. Outcome verified from relevant government authorities."
        + EXPLICIT_NO
    )
    kalshi = KalshiVenue._normalize_market(
        {
            "ticker": "KXGOVKS-26-REP",
            "title": "Will the Republican Party win the 2026 Kansas Governor election?",
            "rules_primary": rules,
            "occurrence_datetime": "2026-11-03T17:00:00Z",
            "status": "open",
        }
    )
    polymarket = PolymarketUSVenue._normalize_market(
        {
            "slug": "ewc-usgub-ks-2026-11-03-rep",
            "title": "Republican Party",
            "question": "Kansas Governor Election Winner",
            "description": rules,
            "gameStartTime": "2026-11-03T17:00:00Z",
            "status": "MARKET_STATUS_OPEN",
        }
    )
    fingerprint = build_fingerprint(polymarket)
    assert fingerprint.event_action == "election_winner"
    assert fingerprint.event_subject == "us_election|election_winner|governor:ks|2026-11-03"
    assert fingerprint.affirmative_outcome == "republican_party"
    assert verify_equivalence(kalshi, polymarket).status.value == "APPROVED_EQUIVALENT"


def test_economic_normalization_preserves_enriched_trading_economics_source():
    market = KalshiVenue._normalize_market(
        {
            "ticker": "KXUE-CAN26AUG-B6.9",
            "title": "Canada unemployment rate in August 2026?",
            "rules_primary": (
                "If the Canada unemployment rate is above 6.9% for August 2026, "
                "then the market resolves to Yes. Otherwise, this market resolves to No."
            ),
            "floor_strike": 6.9,
            "strike_type": "greater",
            "status": "open",
        }
    )
    market.resolution_source = "Trading Economics"
    fingerprint = build_fingerprint(market)
    assert fingerprint.event_subject == "ca_unemployment_rate|2026-08"
    assert fingerprint.resolution_source == "trading_economics"


def test_primary_vote_share_is_not_flattened_into_nomination_winner():
    market = KalshiVenue._normalize_market(
        {
            "ticker": "KXVOTEPRIMARY-GOVFLNOMR26JFIS-14",
            "title": (
                "Will James Fishback receive at least 14% of the popular vote in the "
                "2026 Florida Republican Governor primary?"
            ),
            "rules_primary": (
                "If the certified percentage of the popular vote received by James Fishback "
                "in the 2026 Florida Republican gubernatorial primary on Aug 18, 2026 is "
                "14% to 100%, inclusive of both endpoints, then the market resolves to Yes."
            ),
            "floor_strike": 14,
            "cap_strike": 100,
            "strike_type": "between",
            "status": "open",
        }
    )
    market.resolution_source = "Florida Secretary of State"
    fingerprint = build_fingerprint(market)
    assert fingerprint.event_action == "primary_vote_share"
    assert fingerprint.contract_scope == "governor:fl|popular_valid_vote_share"
    assert fingerprint.affirmative_outcome == "james fishback"
    assert fingerprint.threshold == 14
    assert fingerprint.threshold_upper == 100
    assert fingerprint.resolution_source == "state_election_authority:fl"


def test_candidate_withdrawal_is_not_flattened_into_nomination_winner():
    market = KalshiVenue._normalize_market(
        {
            "ticker": "KXMILLERDROPOUT-26",
            "title": "Will Max Miller drop out of the OH-07 race before Sep 1, 2026?",
            "rules_primary": (
                "If Max Miller drops out of the OH-07 race before Sep 1, 2026, then the "
                "market resolves to Yes. Otherwise, this market resolves to No."
            ),
            "status": "open",
        }
    )
    market.resolution_source = "Reuters | Associated Press"
    fingerprint = build_fingerprint(market)
    assert fingerprint.event_action == "candidate_withdrawal"
    assert fingerprint.event_subject == ("us_election|candidate_withdrawal|house:oh-07|2026-09-01")
    assert fingerprint.affirmative_outcome == "max miller"


def test_named_state_authority_and_generic_authority_do_not_collapse():
    rules = (
        "If the Republican Party nominee wins the 2026 election for Governor of Kansas, "
        "this market will settle to Yes. Otherwise, this market resolves to No."
    )
    kalshi = KalshiVenue._normalize_market(
        {
            "ticker": "KXGOVKS-26-REP",
            "title": "Will the Republican Party win the 2026 Kansas Governor election?",
            "rules_primary": rules,
            "occurrence_datetime": "2026-11-03T17:00:00Z",
            "status": "open",
        }
    )
    kalshi.resolution_source = "Kansas Secretary of State"
    polymarket = PolymarketUSVenue._normalize_market(
        {
            "slug": "ewc-usgub-ks-2026-11-03-rep",
            "title": "Republican Party",
            "question": "Kansas Governor Election Winner",
            "description": rules + " Outcome verified from relevant government authorities.",
            "gameStartTime": "2026-11-03T17:00:00Z",
            "status": "MARKET_STATUS_OPEN",
        }
    )
    pair = verify_equivalence(kalshi, polymarket)
    assert "RESOLUTION_SOURCE_MISMATCH" in pair.differences


def test_sports_team_named_sol_is_not_classified_as_crypto():
    market = KalshiVenue._normalize_market(
        {
            "ticker": "KXLNBPGAME-26AUG122300ABESOL-SOL",
            "title": ("Abejas de Leon vs Soles de Mexicali game: Soles de Mexicali wins"),
            "rules_primary": (
                "If Soles de Mexicali wins the basketball game, the market resolves to Yes. "
                "If cancelled, the market resolves to a fair price."
            ),
            "status": "open",
        }
    )
    assert build_fingerprint(market).market_type != "crypto"


def test_runoff_qualification_is_not_nomination_winner():
    market = KalshiVenue._normalize_market(
        {
            "ticker": "KXSCRSENSADVANCE-26AUG11-RFRY",
            "title": (
                "Will Russell Fry qualify for the runoff in the 2026 South Carolina "
                "Senate Republican special primary?"
            ),
            "rules_primary": (
                "If Russell Fry is announced to qualify for the runoff in the 2026 South "
                "Carolina Senate Republican special primary on Aug 11, 2026, then the "
                "market resolves to Yes."
            ),
            "status": "open",
        }
    )
    fingerprint = build_fingerprint(market)
    assert fingerprint.event_action == "runoff_qualification"
    assert fingerprint.affirmative_outcome == "russell fry"
