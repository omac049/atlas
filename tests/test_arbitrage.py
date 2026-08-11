from datetime import UTC, datetime
from decimal import Decimal

from atlas.arbitrage import calculate_opportunity
from atlas.discovery import compatibility_report, scan_market_pairs
from atlas.fingerprints import build_fingerprint
from atlas.models import MatchStatus
from atlas.outcomes import OutcomeStatus, reconcile_pair
from atlas.replay import read_market_bundle, replay_opportunities, replay_scan, write_market_bundle
from atlas.venues.fixtures import fixture_books, fixture_markets
from atlas.verification import verify_equivalence


def test_fixture_pair_is_deterministically_equivalent():
    markets = fixture_markets()
    pair = verify_equivalence(markets["kalshi"][0], markets["polymarket_us"][0])
    assert pair.status is MatchStatus.APPROVED_EQUIVALENT
    assert pair.match_confidence == Decimal(1)


def test_sports_fingerprint_extracts_family_and_date():
    market = fixture_markets()["kalshi"][0]
    market.title = "Will Pachuca score over 2.5 goals?"
    market.raw_rules_text = "Pachuca in the Charlotte vs Pachuca game originally scheduled for Aug 11, 2026."
    fingerprint = build_fingerprint(market)
    assert fingerprint.market_type == "team_total"
    assert fingerprint.event_date == "2026-08-11"


def test_same_date_sports_start_estimates_share_measurement_period():
    markets = fixture_markets()
    kalshi = markets["kalshi"][0]
    polymarket = markets["polymarket_us"][0]
    kalshi.market_type = "spread"
    kalshi.measurement_period = "2026-08-10T19:30:00Z"
    kalshi.raw_rules_text = (
        "The full Arthur Fils vs Rafael Jodar ATP Montreal Quarterfinal match."
    )
    polymarket.market_type = "spreads"
    polymarket.measurement_period = "2026-08-10T22:00:00Z"
    polymarket.raw_rules_text = (
        "The full Arthur Fils vs Rafael Jodar ATP match scheduled for August 10, 2026."
    )

    assert build_fingerprint(kalshi).measurement_period == "2026-08-10"
    assert build_fingerprint(polymarket).measurement_period == "2026-08-10"


def test_win_by_margin_contract_is_classified_as_spread():
    market = fixture_markets()["kalshi"][0]
    market.title = "Will Rafael Jodar win at least 1.5 more games than Arthur Fils?"
    assert build_fingerprint(market).market_type == "spread"


def test_map_contract_does_not_match_full_match_moneyline():
    markets = fixture_markets()
    kalshi = markets["kalshi"][0]
    polymarket = markets["polymarket_us"][0]
    kalshi.title = "Will Team A win map 1 in the Team A vs Team B match?"
    kalshi.raw_rules_text = "Team A vs Team B match scheduled for Aug 11, 2026."
    kalshi.market_type = "moneyline"
    kalshi.outcome_yes_label = "Team A"
    kalshi.participants = ["Team A", "Team B"]
    polymarket.title = "Team A vs Team B"
    polymarket.raw_rules_text = "Team A vs Team B match scheduled for Aug 11, 2026."
    polymarket.market_type = "moneyline"
    polymarket.outcome_yes_label = "Team A"
    polymarket.participants = ["Team A", "Team B"]
    pair = verify_equivalence(kalshi, polymarket)
    assert "CONTRACT_SCOPE_MISMATCH" in pair.differences


def test_different_affirmative_team_does_not_match():
    markets = fixture_markets()
    kalshi = markets["kalshi"][0]
    polymarket = markets["polymarket_us"][0]
    for market in (kalshi, polymarket):
        market.title = "Team A vs Team B"
        market.raw_rules_text = "Team A vs Team B match scheduled for Aug 11, 2026."
        market.market_type = "moneyline"
        market.participants = ["Team A", "Team B"]
    kalshi.outcome_yes_label = "Team A"
    polymarket.outcome_yes_label = "Team B"
    pair = verify_equivalence(kalshi, polymarket)
    assert "AFFIRMATIVE_OUTCOME_MISMATCH" in pair.differences


def test_settlement_policy_is_part_of_equivalence_gate():
    markets = fixture_markets()
    kalshi = markets["kalshi"][0]
    polymarket = markets["polymarket_us"][0]
    kalshi.raw_rules_text += " Cancellation resolves to fair market price."
    pair = verify_equivalence(kalshi, polymarket)
    assert "SETTLEMENT_POLICY_MISMATCH" in pair.differences


def test_spread_fingerprint_uses_executable_side_and_signed_line():
    markets = fixture_markets()
    kalshi = markets["kalshi"][0]
    polymarket = markets["polymarket_us"][0]
    kalshi.title = "Will Arthur Fils win at least 1.5 more games than Rafael Jodar?"
    kalshi.outcome_yes_label = "Yes"
    kalshi.raw_market_json = {"yes_sub_title": "Arthur Fils -1.5 games"}
    polymarket.title = "Arthur Fils wins by over 1.5 games"
    polymarket.outcome_yes_label = "Rafael Jodar"
    polymarket.raw_market_json = {
        "marketSides": [
            {"long": True, "description": "+1.50"},
            {"long": False, "description": "-1.50"},
        ]
    }
    kalshi_fingerprint = build_fingerprint(kalshi)
    polymarket_fingerprint = build_fingerprint(polymarket)
    assert kalshi_fingerprint.affirmative_outcome == "arthur fils"
    assert kalshi_fingerprint.signed_line == Decimal("-1.5")
    assert polymarket_fingerprint.affirmative_outcome == "rafael jodar"
    assert polymarket_fingerprint.signed_line == Decimal("1.50")


def test_strict_inverse_spread_uses_yes_yes_hedge():
    markets, books = fixture_markets(), fixture_books()
    kalshi = markets["kalshi"][0]
    polymarket = markets["polymarket_us"][0]
    common_rules = (
        "Across the full match. If the event is canceled, both sides settle to $0.50."
    )
    for market in (kalshi, polymarket):
        market.market_type = "spread"
        market.threshold = Decimal("1.5")
        market.threshold_operator = ">"
        market.participants = ["Team A", "Team B"]
        market.measurement_period = "2026-08-10T20:00:00Z"
        market.raw_rules_text = common_rules
    kalshi.title = "Will Team A win at least 1.5 more points than Team B?"
    kalshi.raw_market_json = {"yes_sub_title": "Team A -1.5 points"}
    polymarket.title = "Team A wins by over 1.5 points"
    polymarket.outcome_yes_label = "Team B"
    polymarket.raw_market_json = {
        "marketSides": [{"long": True, "description": "+1.5"}]
    }
    pair = verify_equivalence(kalshi, polymarket)
    assert pair.status is MatchStatus.APPROVED_INVERSE
    assert pair.differences == []
    assert pair.decision.relationship_codes == [
        "AFFIRMATIVE_OUTCOME_COMPLEMENT",
        "SIGNED_LINE_COMPLEMENT",
    ]
    opportunity = calculate_opportunity(
        pair,
        books["kalshi:KALSHI-FED-SEP26"],
        books["polymarket_us:PM-FED-SEP26"],
    )
    assert opportunity.leg_b_side == "YES"


def test_fair_price_policy_blocks_inverse_approval():
    markets = fixture_markets()
    kalshi = markets["kalshi"][0]
    polymarket = markets["polymarket_us"][0]
    common_rules = "Across the full match. Cancellation resolves to fair market price."
    for market in (kalshi, polymarket):
        market.market_type = "spread"
        market.threshold = Decimal("1.5")
        market.threshold_operator = ">"
        market.participants = ["Team A", "Team B"]
        market.raw_rules_text = common_rules
    kalshi.title = "Will Team A win at least 1.5 more points than Team B?"
    kalshi.raw_market_json = {"yes_sub_title": "Team A -1.5 points"}
    polymarket.outcome_yes_label = "Team B"
    polymarket.raw_market_json = {
        "marketSides": [{"long": True, "description": "+1.5"}]
    }
    assert verify_equivalence(kalshi, polymarket).status is MatchStatus.REVIEW_REQUIRED


def test_identical_fair_price_contracts_are_not_approved():
    markets = fixture_markets()
    for market in (markets["kalshi"][0], markets["polymarket_us"][0]):
        market.raw_rules_text = (
            "Resolve YES if the event occurs. Cancellation resolves to fair market price."
        )
    pair = verify_equivalence(markets["kalshi"][0], markets["polymarket_us"][0])
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert "NON_GUARANTEED_SETTLEMENT" in pair.differences


def test_guarantee_reachability_helpers_flag_discretionary_dead_ends():
    from atlas.discovery import _guarantee_reachable, _reachability_rank

    assert _guarantee_reachable("GUARANTEED") is True
    assert _guarantee_reachable("UNKNOWN") is True
    assert _guarantee_reachable("NON_GUARANTEED") is False
    # Reachable candidates must sort ahead of structurally-unreachable ones.
    assert _reachability_rank("UNKNOWN") < _reachability_rank("NON_GUARANTEED")


def test_candidate_queue_state_marks_discretionary_pairs_unreachable():
    from atlas.discovery import _candidate_queue_state

    # A discretionary fair-price pair is a permanent dead end, not rule-cleanup work.
    assert _candidate_queue_state(
        "REVIEW_REQUIRED", "NON_GUARANTEED", "OPEN_AWAITING_SETTLEMENT"
    ) == ("BLOCKED", "STRUCTURALLY_UNREACHABLE_DISCRETIONARY_SETTLEMENT")
    # Even a rule-approved pair can never be trusted if settlement stays discretionary.
    assert _candidate_queue_state(
        "APPROVED_EQUIVALENT", "NON_GUARANTEED", "SETTLED"
    ) == ("BLOCKED", "STRUCTURALLY_UNREACHABLE_DISCRETIONARY_SETTLEMENT")
    # An UNKNOWN pair stays a reachable frontier awaiting complete evidence.
    assert _candidate_queue_state(
        "REVIEW_REQUIRED", "UNKNOWN", "OPEN_AWAITING_SETTLEMENT"
    ) == ("BLOCKED", "CLEAR_DETERMINISTIC_RULE_MISMATCHES")


def test_settlement_discovery_deprioritizes_discretionary_dead_ends():
    markets = fixture_markets()
    for market in (markets["kalshi"][0], markets["polymarket_us"][0]):
        market.raw_rules_text = (
            "Resolve YES if the event occurs. Cancellation resolves to fair market price."
        )
    settlement = compatibility_report(
        markets["kalshi"], markets["polymarket_us"]
    )["settlement_discovery"]
    top = settlement["rankings"][0]
    assert top["guarantee_status"] == "NON_GUARANTEED"
    assert top["guarantee_reachable"] is False
    assert top["queue_status"] == "BLOCKED"
    assert top["next_gate"] == "STRUCTURALLY_UNREACHABLE_DISCRETIONARY_SETTLEMENT"
    assert settlement["structurally_unreachable_events"] == 1
    assert settlement["reachable_blocked_events"] == 0


def test_official_governing_source_is_canonicalized():
    markets = fixture_markets()
    markets["kalshi"][0].resolution_source = "ESPN | Fox Sports | ATP"
    markets["polymarket_us"][0].resolution_source = "ATP"
    assert build_fingerprint(markets["kalshi"][0]).resolution_source == "atp"
    assert build_fingerprint(markets["polymarket_us"][0]).resolution_source == "atp"


def test_fingerprint_cache_invalidates_when_source_is_enriched():
    market = fixture_markets()["kalshi"][0]
    market.resolution_source = "unknown"
    assert build_fingerprint(market).resolution_source == "unknown"
    market.resolution_source = "Federal Reserve"
    assert build_fingerprint(market).resolution_source == "federal reserve"


def test_candidate_index_uses_canonical_meaning_not_venue_ids():
    markets = fixture_markets()
    markets["kalshi"][0].event_subject = "kalshi-event-123"
    markets["polymarket_us"][0].event_subject = "polymarket-event-456"
    pairs = scan_market_pairs(markets["kalshi"], markets["polymarket_us"])
    assert len(pairs) == 1
    assert pairs[0].status is MatchStatus.APPROVED_EQUIVALENT


def test_catalog_report_exposes_family_overlap():
    markets = fixture_markets()
    report = compatibility_report(markets["kalshi"], markets["polymarket_us"])
    assert report["compatible"] is True
    assert "binary" in report["compatible_families"]
    assert report["event_compatible"] is True
    assert report["shared_event_count"] == 1
    settlement = report["settlement_discovery"]
    assert settlement["kalshi_counts"]["GUARANTEED"] == 1
    assert settlement["polymarket_counts"]["GUARANTEED"] == 1
    assert settlement["guaranteed_shared_events"] == 1
    assert settlement["execution_ready_events"] == 1
    assert settlement["structurally_unreachable_events"] == 0
    assert settlement["reachable_blocked_events"] == 0
    assert settlement["rankings"][0]["guarantee_reachable"] is True
    assert settlement["evidence_complete_shared_events"] == 1
    assert settlement["rankings"][0]["evidence_readiness"]["status"] == "COMPLETE"
    assert settlement["rankings"][0]["rule_distance"] == 0
    assert settlement["rankings"][0]["lifecycle_status"] == "OPEN_AWAITING_SETTLEMENT"
    assert settlement["rankings"][0]["queue_status"] == "AWAITING_SETTLEMENT"
    assert settlement["rankings"][0]["next_gate"] == "WAIT_FOR_BOTH_TERMINAL_OUTCOMES"
    assert settlement["rankings"][0]["kalshi_status"] == "active"
    assert settlement["rankings"][0]["polymarket_status"] == "active"
    assert settlement["rankings"][0]["kalshi_evidence"]["complete"] is True
    assert settlement["rankings"][0]["polymarket_evidence"]["complete"] is True
    assert settlement["rankings"][0]["kalshi_evidence"]["source_present"] is True
    assert settlement["rankings"][0]["polymarket_evidence"]["rules_hash"]


def test_settlement_ready_time_uses_later_venue_deadline():
    markets = fixture_markets()
    markets["kalshi"][0].resolution_time = datetime(2026, 9, 30, 14, tzinfo=UTC)
    markets["polymarket_us"][0].resolution_time = datetime(2026, 9, 30, 12, tzinfo=UTC)

    ranking = compatibility_report(
        markets["kalshi"], markets["polymarket_us"]
    )["settlement_discovery"]["rankings"][0]

    assert ranking["settlement_ready_at"] == "2026-09-30T14:00:00+00:00"


def test_market_replay_round_trip(tmp_path):
    markets = fixture_markets()
    from atlas.models import VenueName

    path = tmp_path / "markets.json"
    from atlas.venues.fixtures import fixture_books

    write_market_bundle(markets, str(path), fixture_books())
    assert read_market_bundle(str(path))[VenueName.KALSHI][0].market_id == markets[VenueName.KALSHI][0].market_id
    assert replay_scan(str(path))["approved"] == 1
    assert replay_opportunities(str(path)) == 1


def test_settlement_reconciler_does_not_label_pending_markets():
    markets = fixture_markets()
    pair = verify_equivalence(markets["kalshi"][0], markets["polymarket_us"][0])
    assert reconcile_pair(pair) is OutcomeStatus.PENDING


def test_depth_aware_calculation_caps_at_available_liquidity():
    markets, books = fixture_markets(), fixture_books()
    pair = verify_equivalence(markets["kalshi"][0], markets["polymarket_us"][0])
    opportunity = calculate_opportunity(
        pair, books["kalshi:KALSHI-FED-SEP26"], books["polymarket_us:PM-FED-SEP26"], Decimal(500)
    )
    assert opportunity is not None
    assert opportunity.contracts == Decimal(180)
    assert opportunity.expected_profit > 0


def test_rule_difference_blocks_arbitrage():
    markets, books = fixture_markets(), fixture_books()
    markets["polymarket_us"][0].threshold_operator = ">"
    pair = verify_equivalence(markets["kalshi"][0], markets["polymarket_us"][0])
    assert (
        calculate_opportunity(
            pair, books["kalshi:KALSHI-FED-SEP26"], books["polymarket_us:PM-FED-SEP26"]
        )
        is None
    )
