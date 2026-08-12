from decimal import Decimal

import httpx
import pytest

from atlas.cli import _catalog_health
from atlas.discovery import (
    _bounded_event_pairs,
    compatibility_report,
    markets_requiring_family_source_enrichment,
    markets_requiring_source_enrichment,
    propose_market_pairs,
    review_market_pairs,
    scan_market_pairs,
)
from atlas.fingerprints import build_fingerprint, deterministic_settlement_blockers
from atlas.models import VenueName
from atlas.venues.fixtures import fixture_markets
from atlas.venues.kalshi import SETTLED_SERIES_MAX_PAGES, KalshiVenue
from atlas.venues.polymarket_global import PolymarketGlobalHistoricalVenue
from atlas.venues.polymarket_us import PolymarketUSVenue, _levels, _market_items


def test_kalshi_normalization_converts_bid_books_to_asks():
    market = KalshiVenue._normalize_market({"ticker": "KX-1", "title": "Test", "status": "open"})
    assert market.venue is VenueName.KALSHI


def test_kalshi_structured_player_prop_uses_public_rule_threshold():
    market = KalshiVenue._normalize_market(
        {
            "ticker": "KX-RBI-1",
            "title": "Yainer Diaz: 2+ RBIs?",
            "rules_primary": "If Yainer Diaz records 2+ RBIs in the game, then Yes.",
            "floor_strike": 1.5,
            "strike_type": "structured",
            "status": "open",
        }
    )
    assert market.threshold == Decimal(2)
    assert market.threshold_operator == ">="
    assert market.threshold_unit == "rbis"


def test_kalshi_normalization_preserves_secondary_settlement_rules():
    market = KalshiVenue._normalize_market(
        {
            "ticker": "KX-RULES-1",
            "title": "Test",
            "rules_primary": "Primary resolution rule.",
            "rules_secondary": "Cancellation resolves to fair market price.",
            "status": "open",
        }
    )
    assert "Primary resolution rule" in market.raw_rules_text
    assert "Cancellation resolves" in market.raw_rules_text


def test_polymarket_book_levels_use_decimal_prices():
    levels = _levels([{"px": "0.41", "qty": "12.5"}], "px", "qty")
    assert levels[0].price == Decimal("0.41")
    assert levels[0].quantity == Decimal("12.5")


def test_polymarket_book_levels_support_nested_price_values():
    levels = _levels(
        [{"px": {"value": "0.61", "currency": "USD"}, "qty": "18"}],
        "px",
        "qty",
    )
    assert levels[0].price == Decimal("0.61")
    assert levels[0].quantity == Decimal(18)


def test_polymarket_normalization_maps_long_and_short_outcomes():
    market = PolymarketUSVenue._normalize_market(
        {
            "slug": "team-a-v-team-b",
            "title": "Team A vs Team B",
            "marketType": "moneyline",
            "status": "MARKET_STATUS_OPEN",
            "marketSides": [
                {"long": True, "description": "Team A"},
                {"long": False, "description": "Team B"},
            ],
        }
    )
    assert market.outcome_yes_label == "Team A"
    assert market.outcome_no_label == "Team B"


def test_polymarket_normalization_extracts_resolution_source():
    market = PolymarketUSVenue._normalize_market(
        {
            "slug": "atp-market",
            "title": "Player A vs Player B",
            "description": "This market settles to Yes if Player A wins. Outcome sourced from ATP.",
            "status": "MARKET_STATUS_OPEN",
        }
    )
    assert market.resolution_source == "ATP"
    assert market.raw_rules_text == market.description


def test_polymarket_normalization_preserves_explicit_rule_fields():
    rules = (
        "If the official value is above 90F, this market settles to Yes. "
        "Otherwise, this market settles to No."
    )
    market = PolymarketUSVenue._normalize_market(
        {
            "slug": "weather-rules",
            "title": "Weather",
            "description": "Short market description",
            "resolutionRules": rules,
            "settlementSource": "NWS",
            "status": "MARKET_STATUS_OPEN",
        }
    )
    assert market.raw_rules_text == rules
    assert market.resolution_text == rules
    assert market.resolution_source == "NWS"


def test_resolved_polymarket_market_is_not_live():
    market = PolymarketUSVenue._normalize_market(
        {"slug": "old", "status": "MARKET_STATUS_RESOLVED"}
    )
    assert market.status.value == "settled"


def test_finalized_kalshi_market_maps_to_settled():
    market = KalshiVenue._normalize_market(
        {"ticker": "KX-FINAL", "status": "finalized", "result": "yes"}
    )
    assert market.status.value == "settled"


@pytest.mark.asyncio
async def test_kalshi_historical_event_query_uses_mutually_exclusive_filters(monkeypatch):
    venue = KalshiVenue(fixture=False)
    requests = []

    async def fake_get(path, params=None):
        requests.append((path, params))
        return {"markets": []}

    monkeypatch.setattr(venue, "_get", fake_get)

    await venue.list_settled_event_markets("KX-EVENT")

    historical_params = next(params for path, params in requests if path == "/historical/markets")
    assert historical_params == {"event_ticker": "KX-EVENT", "limit": 1000}


@pytest.mark.asyncio
async def test_kalshi_event_markets_preserve_current_results_when_archive_fails(
    monkeypatch,
):
    venue = KalshiVenue(fixture=False)

    async def fake_get(path, params=None):
        if path == "/historical/markets":
            raise httpx.HTTPStatusError(
                "archive unavailable",
                request=httpx.Request("GET", "https://example.test/historical/markets"),
                response=httpx.Response(429),
            )
        return {
            "markets": [
                {
                    "ticker": "KX-EVENT-YES",
                    "event_ticker": "KX-EVENT",
                    "title": "Event result",
                    "status": "finalized",
                    "result": "yes",
                }
            ]
        }

    monkeypatch.setattr(venue, "_get", fake_get)

    markets = await venue.list_settled_event_markets("KX-EVENT")

    assert [market.venue_market_id for market in markets] == ["KX-EVENT-YES"]


@pytest.mark.asyncio
async def test_kalshi_settled_series_scan_merges_series_events_before_recent_scan(monkeypatch):
    venue = KalshiVenue(fixture=False)
    requests = []

    async def fake_get(path, params=None):
        requests.append((path, params))
        series = params.get("series_ticker")
        if series == "KXFEDDECISION":
            return {
                "events": [
                    {"event_ticker": "KXFEDDECISION-26JUL", "title": "Fed decision in July?"}
                ],
                "cursor": "",
            }
        if series == "KXFED":
            return {"events": [], "cursor": ""}
        return {
            "events": [
                # The recent scan re-returns the July event; dedupe must keep one copy.
                {"event_ticker": "KXFEDDECISION-26JUL", "title": "Fed decision in July?"},
                {"event_ticker": "KXNFLGAME-26AUG10", "title": "Sunday night football"},
            ],
            "cursor": "",
        }

    monkeypatch.setattr(venue, "_get", fake_get)

    events = await venue.list_settled_events(
        max_pages=1, series_tickers=("KXFEDDECISION", "KXFED")
    )

    assert [event["event_ticker"] for event in events] == [
        "KXFEDDECISION-26JUL",
        "KXNFLGAME-26AUG10",
    ]
    series_params = [params for _, params in requests if params.get("series_ticker")]
    assert [params["series_ticker"] for params in series_params] == ["KXFEDDECISION", "KXFED"]
    assert all(params["status"] == "settled" for _, params in requests)


@pytest.mark.asyncio
async def test_kalshi_settled_series_scan_pages_are_bounded(monkeypatch):
    venue = KalshiVenue(fixture=False)
    calls = []

    async def endless_pages(path, params=None):
        calls.append(params)
        return {
            "events": [
                {"event_ticker": f"KXFED-{len(calls)}-{index}", "title": "page filler"}
                for index in range(200)
            ],
            "cursor": "next",
        }

    monkeypatch.setattr(venue, "_get", endless_pages)

    await venue.list_settled_events(max_pages=1, series_tickers=("KXFED",))

    series_calls = [params for params in calls if params.get("series_ticker")]
    assert len(series_calls) == SETTLED_SERIES_MAX_PAGES
    assert len(calls) == SETTLED_SERIES_MAX_PAGES + 1


def test_closed_polymarket_market_maps_to_closed_until_final_settlement():
    market = PolymarketUSVenue._normalize_market(
        {"slug": "closed-market", "status": "MARKET_STATUS_CLOSED", "closed": True}
    )
    assert market.status.value == "closed"


@pytest.mark.asyncio
async def test_global_polymarket_open_listing_is_tag_scoped_and_active(monkeypatch):
    venue = PolymarketGlobalHistoricalVenue(tag_ids=("100196",))
    requests = []

    async def fake_get(path, params=None):
        requests.append((path, params))
        return [
            {
                "id": "999001",
                "question": "Will the upper bound of the target federal funds rate be 3.5% at the end of 2026?",
                "closed": False,
                "description": "resolves per the Federal Reserve",
            }
        ]

    monkeypatch.setattr(venue, "_get", fake_get)

    markets = await venue.list_open_markets(max_pages=1)

    assert [market.status.value for market in markets] == ["active"]
    assert markets[0].market_id == "polymarket_global:999001"
    path, params = requests[0]
    assert path == "/markets"
    assert params["closed"] is False
    assert params["active"] is True
    assert params["tag_id"] == "100196"
    assert not hasattr(venue, "get_orderbook")


def test_global_polymarket_history_is_an_isolated_evidence_adapter():
    venue = PolymarketGlobalHistoricalVenue()
    market = venue._normalize_market(
        {
            "id": "42",
            "slug": "fed-cuts-rates",
            "question": "Will the Federal Reserve cut rates in September 2026?",
            "description": "Resolves Yes if the Federal Reserve cuts rates.",
            "closed": True,
            "closedTime": "2026-10-01T00:00:00Z",
            "resolutionSource": "Federal Reserve",
            "events": [{"ticker": "fed-sep-2026", "title": "Federal Reserve rates"}],
        }
    )

    assert market.venue is VenueName.POLYMARKET_GLOBAL
    assert market.market_id == "polymarket_global:42"
    assert market.status.value == "closed"
    assert not hasattr(venue, "get_orderbook")


def test_global_polymarket_preserves_explicit_resolution_rules():
    venue = PolymarketGlobalHistoricalVenue()
    rules = "If the official rate is above 5%, this market resolves to Yes."
    market = venue._normalize_market(
        {
            "id": "43",
            "question": "Rate",
            "description": "Short description",
            "resolutionRules": rules,
            "settlementSource": "Federal Reserve",
            "events": [],
        }
    )

    assert market.raw_rules_text == rules
    assert market.resolution_text == rules
    assert market.resolution_source == "Federal Reserve"


@pytest.mark.asyncio
async def test_global_polymarket_tag_filter_is_deduplicated(monkeypatch):
    venue = PolymarketGlobalHistoricalVenue(tag_ids=("2", "21"))
    requested = []

    async def fake_get(path, params=None):
        requested.append(params)
        return [
            {
                "id": "42",
                "question": "Will the Federal Reserve cut rates?",
                "closed": True,
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["1", "0"]',
                "umaResolutionStatus": "resolved",
            }
        ]

    monkeypatch.setattr(venue, "_get", fake_get)
    markets = await venue.list_closed_markets(max_pages=1)

    assert len(markets) == 1
    assert [params["tag_id"] for params in requested] == ["2", "21"]
    assert venue.catalog_scope == "tagged:2,21"


@pytest.mark.asyncio
async def test_global_polymarket_requires_resolved_yes_no_terminal_prices():
    venue = PolymarketGlobalHistoricalVenue()
    venue._closed_markets["42"] = {
        "id": "42",
        "closed": True,
        "closedTime": "2026-10-01T00:00:00Z",
        "umaResolutionStatus": "resolved",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["1", "0"]',
    }

    evidence = await venue.get_terminal_settlement_evidence("42")

    assert evidence["source"] == "polymarket_global_gamma_closed_market"
    assert evidence["settlement"] == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "outcomes", "prices"),
    [
        ("proposed", '["Yes", "No"]', '["1", "0"]'),
        ("resolved", '["Yes", "No"]', '["0.5", "0.5"]'),
        ("resolved", '["Over", "Under"]', '["1", "0"]'),
    ],
)
async def test_global_polymarket_rejects_ambiguous_terminal_evidence(status, outcomes, prices):
    venue = PolymarketGlobalHistoricalVenue()
    venue._closed_markets["42"] = {
        "id": "42",
        "closed": True,
        "umaResolutionStatus": status,
        "outcomes": outcomes,
        "outcomePrices": prices,
    }

    assert await venue.get_terminal_settlement_evidence("42") == {}


@pytest.mark.asyncio
async def test_polymarket_terminal_book_is_final_binary_settlement(monkeypatch):
    venue = PolymarketUSVenue(fixture=False)

    async def no_endpoint(market_id):
        raise httpx.HTTPStatusError(
            "missing",
            request=httpx.Request("GET", "https://example.test"),
            response=httpx.Response(404),
        )

    async def terminal_book(path, params=None):
        return {
            "marketData": {
                "state": "MARKET_STATE_EXPIRED",
                "stats": {
                    "settlementPx": {"value": "1.000", "currency": "USD"},
                    "settlementPreliminaryFlag": False,
                },
            }
        }

    monkeypatch.setattr(venue, "get_settlement", no_endpoint)
    monkeypatch.setattr(venue, "_get", terminal_book)

    evidence = await venue.get_terminal_settlement_evidence("settled-market")

    assert evidence["source"] == "terminal_market_book"
    assert evidence["settlement"] == "1.000"


def test_pair_scan_only_returns_live_candidates():
    kalshi = KalshiVenue._normalize_market({"ticker": "KX-1", "title": "Test", "status": "open"})
    polymarket = PolymarketUSVenue._normalize_market(
        {"slug": "pm-1", "status": "MARKET_STATUS_RESOLVED"}
    )
    assert scan_market_pairs([kalshi], [polymarket]) == []


def test_polymarket_catalog_payload_supports_markets_envelope():
    assert _market_items({"markets": [{"slug": "one"}]}) == [{"slug": "one"}]
    assert _market_items([{"slug": "two"}]) == [{"slug": "two"}]


async def test_polymarket_market_lookup_uses_slug_endpoint(monkeypatch):
    venue = PolymarketUSVenue(fixture=False)
    requested = []

    async def fake_get(path, params=None):
        requested.append(path)
        return {"market": {"slug": "weather-contract", "status": "MARKET_STATUS_OPEN"}}

    monkeypatch.setattr(venue, "_get", fake_get)
    market = await venue.get_market("weather-contract")
    assert market.venue_market_id == "weather-contract"
    assert requested == ["/v1/market/slug/weather-contract"]


def test_candidate_proposals_are_review_only():
    kalshi = KalshiVenue._normalize_market(
        {"ticker": "KX-1", "title": "Phoenix team victory", "status": "open"}
    )
    polymarket = PolymarketUSVenue._normalize_market(
        {"slug": "pm-1", "title": "Phoenix team", "status": "open"}
    )
    proposals = propose_market_pairs([kalshi], [polymarket])
    assert proposals[0]["status"] == "REVIEW_REQUIRED"


def test_same_event_review_queue_explains_contract_mismatch():
    markets = fixture_markets()
    markets["polymarket_us"][0].threshold = Decimal(50)
    reviews = review_market_pairs(markets["kalshi"], markets["polymarket_us"])
    assert reviews[0]["review_kind"] == "SAME_EVENT_DIFFERENT_CONTRACT"
    assert "THRESHOLD_MISMATCH" in reviews[0]["mismatch_codes"]
    assert reviews[0]["kalshi_terms"]["threshold"] == "25"
    assert reviews[0]["polymarket_terms"]["threshold"] == "50"


HOUSE_RULES = """This market will settle to Yes if the Democratic Party wins control of the
United States House of Representatives in the 2026 United States midterm election. Outcome
sourced from relevant state electoral authorities, and the United States House of Representatives.
A party wins control if it wins a majority of the chamber's voting seats. If no party wins a
majority, control is determined by the party with which the first elected Speaker of the House
is affiliated. Runoff results are included. Seats are attributed to the party under which the
member was elected. Independents who formally caucus with a party are included. The deadline
for an announcement is January 4th, 11:59 PM ET."""


def _polymarket_house_control():
    return PolymarketUSVenue._normalize_market(
        {
            "slug": "paccc-usho-midterms-2026-11-03-dem",
            "title": "Democratic Party",
            "question": "U.S House Midterm Winner",
            "description": HOUSE_RULES,
            "resolutionSource": (
                "relevant state electoral authorities, and the United States House of Representatives"
            ),
            "gameStartTime": "2026-11-03T00:00:00Z",
            "marketType": "election",
            "status": "MARKET_STATUS_OPEN",
        }
    )


def _kalshi_house_control(rules: str = HOUSE_RULES):
    return KalshiVenue._normalize_market(
        {
            "ticker": "KXHOUSE-26-DEM",
            "event_ticker": "KXHOUSE-26",
            "title": "Will the Democratic Party control the U.S. House after the 2026 midterm election?",
            "rules_primary": rules,
            "settlement_sources": (
                "relevant state electoral authorities, and the United States House of Representatives"
            ),
            "occurrence_datetime": "2026-11-03T00:00:00Z",
            "status": "open",
        }
    )


def test_election_fingerprint_normalizes_chamber_cycle_party_and_policy():
    fingerprint = build_fingerprint(_polymarket_house_control())
    assert fingerprint.event_subject == "us_house_control|2026"
    assert fingerprint.event_action == "party_control"
    assert fingerprint.contract_scope == "us_house_control"
    assert fingerprint.affirmative_outcome == "democratic_party"
    assert fingerprint.market_type == "election"
    assert fingerprint.resolution_source == "state_electoral_authorities+us_house"
    assert "tie=first_elected_speaker_party" in fingerprint.settlement_policy
    assert deterministic_settlement_blockers(_polymarket_house_control()) == []


def test_election_pair_requires_identical_deterministic_settlement_rules():
    polymarket = _polymarket_house_control()
    approved = scan_market_pairs([_kalshi_house_control()], [polymarket])
    assert len(approved) == 1
    assert approved[0].status.value == "APPROVED_EQUIVALENT"

    missing_tiebreak = HOUSE_RULES.replace(
        "If no party wins a\nmajority, control is determined by the party with which the first elected Speaker of the House\nis affiliated. ",
        "",
    )
    reviews = review_market_pairs([_kalshi_house_control(missing_tiebreak)], [polymarket])
    assert "SETTLEMENT_POLICY_MISMATCH" in reviews[0]["mismatch_codes"]


def test_catalog_report_exposes_unpaired_deterministic_election_contract():
    report = compatibility_report([], [_polymarket_house_control()])
    election = report["election_discovery"]
    assert election["status"] == "WAITING_FOR_KALSHI_COUNTERPART"
    assert election["kalshi_contracts"] == 0
    assert election["polymarket_contracts"] == 1
    assert election["deterministic_polymarket"] == 1
    assert election["polymarket_candidates"][0]["blockers"] == []


def test_catalog_health_rejects_truncated_live_snapshot():
    healthy, blockers = _catalog_health(
        1000,
        0,
        {"kalshi_active": 20000, "polymarket_active": 14450},
    )
    assert healthy is False
    assert "KALSHI_CATALOG_TRUNCATED" in blockers
    assert "POLYMARKET_CATALOG_TRUNCATED" in blockers


def test_catalog_health_accepts_full_live_snapshot():
    healthy, blockers = _catalog_health(
        20000,
        14450,
        {"kalshi_active": 20000, "polymarket_active": 14528},
    )
    assert healthy is True
    assert blockers == []


def test_only_same_event_unknown_source_markets_require_enrichment():
    markets = fixture_markets()
    kalshi = markets["kalshi"][0]
    polymarket = markets["polymarket_us"][0]
    kalshi.resolution_source = "unknown"
    unrelated = kalshi.model_copy(deep=True)
    unrelated.market_id = "kalshi:unrelated"
    unrelated.venue_market_id = "unrelated"
    unrelated.event_action = "A different event"
    unrelated.title = "A different event"

    candidates = markets_requiring_source_enrichment([kalshi, unrelated], [polymarket])
    assert [market.market_id for market in candidates] == [kalshi.market_id]


def test_family_source_enrichment_does_not_require_existing_counterpart():
    market = KalshiVenue._normalize_market(
        {
            "ticker": "KXUE-CAN26AUG-B6.9",
            "title": "Canada unemployment rate in August 2026?",
            "rules_primary": (
                "If the Canada unemployment rate is above 6.9% for August 2026, "
                "then the market resolves to Yes."
            ),
            "status": "open",
        }
    )
    assert markets_requiring_family_source_enrichment([market]) == [market]


def test_shared_event_ranking_bounds_same_shape_comparisons():
    markets = fixture_markets()
    left = []
    right = []
    for index in range(100):
        kalshi = markets["kalshi"][0].model_copy(deep=True)
        kalshi.market_id = f"kalshi:{index}"
        polymarket = markets["polymarket_us"][0].model_copy(deep=True)
        polymarket.market_id = f"polymarket:{index}"
        left.append((kalshi, build_fingerprint(kalshi)))
        right.append((polymarket, build_fingerprint(polymarket)))

    assert len(_bounded_event_pairs(left, right)) == 64


def test_shared_event_ranking_has_total_event_bound_across_shapes():
    markets = fixture_markets()
    left = []
    right = []
    for index in range(300):
        kalshi = markets["kalshi"][0].model_copy(deep=True)
        kalshi.market_id = f"kalshi:shape:{index}"
        kalshi.threshold = Decimal(index)
        polymarket = markets["polymarket_us"][0].model_copy(deep=True)
        polymarket.market_id = f"polymarket:shape:{index}"
        polymarket.threshold = Decimal(index)
        left.append((kalshi, build_fingerprint(kalshi)))
        right.append((polymarket, build_fingerprint(polymarket)))

    assert len(_bounded_event_pairs(left, right)) == 128


def test_compatibility_report_reuses_precomputed_exact_keys(monkeypatch):
    markets = fixture_markets()

    def duplicate_scan(*_args, **_kwargs):
        raise AssertionError("compatibility report repeated the full exact scan")

    monkeypatch.setattr("atlas.discovery.scan_market_pairs", duplicate_scan)
    report = compatibility_report(markets["kalshi"], markets["polymarket_us"])
    assert report["event_compatible"] is True
