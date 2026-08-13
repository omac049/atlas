"""Targeted Polymarket US event-slug harvest path.

Settled US-venue macro ladders (e.g. the resolved July 2026 CPI event) sit
under ~400k closed rows, so the recent-id closed sweep never reaches them.
The event-slug door fetches one event's nested markets explicitly; harvested
markets join the same final-binary evidence pool as swept markets.
"""

import httpx
import pytest

from atlas.backfill import backfill_historical_validation
from atlas.cli import _parse_polymarket_us_event_slugs
from atlas.models import MarketStatus
from atlas.storage import AtlasStore
from atlas.venues.fixtures import fixture_markets
from atlas.venues.polymarket_us import PolymarketUSVenue


@pytest.mark.asyncio
async def test_event_slug_lookup_normalizes_nested_markets(monkeypatch):
    venue = PolymarketUSVenue(fixture=False)
    requests = []

    async def fake_get(path, params=None):
        requests.append((path, params))
        return {
            "event": {
                "slug": "uscpi-july-yoy-2026-08-12",
                "markets": [
                    {
                        "slug": "cpic-uscpi-july-yoy-2026-08-12-gt2pt9pct",
                        "question": "Will inflation be above 2.9%?",
                        "status": "MARKET_STATUS_RESOLVED",
                    },
                    {
                        "slug": "cpic-uscpi-july-yoy-2026-08-12-gt3pt1pct",
                        "question": "Will inflation be above 3.1%?",
                        "closed": True,
                    },
                ],
            }
        }

    monkeypatch.setattr(venue, "_get", fake_get)
    markets = await venue.list_event_markets("uscpi-july-yoy-2026-08-12")

    assert requests == [("/v1/events/slug/uscpi-july-yoy-2026-08-12", None)]
    assert [market.venue_market_id for market in markets] == [
        "cpic-uscpi-july-yoy-2026-08-12-gt2pt9pct",
        "cpic-uscpi-july-yoy-2026-08-12-gt3pt1pct",
    ]
    assert markets[0].status is MarketStatus.SETTLED
    assert markets[1].status is MarketStatus.CLOSED
    assert all(market.market_id.startswith("polymarket_us:") for market in markets)


@pytest.mark.asyncio
async def test_event_slug_lookup_handles_bare_event_payload(monkeypatch):
    venue = PolymarketUSVenue(fixture=False)

    async def fake_get(path, params=None):
        return {"slug": "uscpi-august-yoy-2026-09-11", "markets": [{"slug": "m-1", "title": "M1"}]}

    monkeypatch.setattr(venue, "_get", fake_get)
    markets = await venue.list_event_markets("uscpi-august-yoy-2026-09-11")

    assert [market.venue_market_id for market in markets] == ["m-1"]


@pytest.mark.asyncio
async def test_event_slug_lookup_returns_empty_on_404(monkeypatch):
    venue = PolymarketUSVenue(fixture=False)

    async def fake_get(path, params=None):
        raise httpx.HTTPStatusError(
            "missing",
            request=httpx.Request("GET", "https://example.test"),
            response=httpx.Response(404),
        )

    monkeypatch.setattr(venue, "_get", fake_get)

    assert await venue.list_event_markets("uscpi-never-existed") == []


@pytest.mark.asyncio
async def test_event_slug_lookup_propagates_non_404_errors(monkeypatch):
    venue = PolymarketUSVenue(fixture=False)

    async def fake_get(path, params=None):
        raise httpx.HTTPStatusError(
            "server error",
            request=httpx.Request("GET", "https://example.test"),
            response=httpx.Response(500),
        )

    monkeypatch.setattr(venue, "_get", fake_get)

    with pytest.raises(httpx.HTTPStatusError):
        await venue.list_event_markets("uscpi-july-yoy-2026-08-12")


class HarvestKalshiVenue:
    async def list_settled_events(self, max_pages=100):
        return [
            {
                "event_ticker": "KXFED-SEP26",
                "title": "Federal Reserve raises federal funds target September 2026",
                "sub_title": "25 basis points",
            }
        ]

    async def list_settled_event_markets(self, event_ticker):
        market = fixture_markets()["kalshi"][0].model_copy(deep=True)
        market.status = MarketStatus.SETTLED
        market.raw_market_json["result"] = "yes"
        market.raw_market_json["event_ticker"] = "KXFED-SEP26"
        return [market]


class HarvestPolymarketVenue:
    def __init__(self, closed, event_markets):
        self.closed = closed
        self.event_markets = event_markets

    async def list_closed_markets(self, max_pages=20):
        return self.closed

    async def list_event_markets(self, event_slug):
        return self.event_markets.get(event_slug, [])

    async def get_terminal_settlement_evidence(self, market_id):
        return {
            "source": "terminal_market_book",
            "settlement": "1",
            "state": "MARKET_STATE_EXPIRED",
        }


@pytest.mark.asyncio
async def test_backfill_records_slugs_and_dedups_harvested_pool(tmp_path):
    polymarket = fixture_markets()["polymarket_us"][0].model_copy(deep=True)
    polymarket.status = MarketStatus.CLOSED
    polymarket.raw_market_json["question"] = polymarket.title

    # The same market arrives through both doors — the pool must keep one copy.
    duplicate = polymarket.model_copy(deep=True)
    extra = polymarket.model_copy(deep=True)
    extra.market_id = "polymarket_us:fed-sep26-alt"
    extra.venue_market_id = "fed-sep26-alt"

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    report = await backfill_historical_validation(
        store,
        HarvestKalshiVenue(),
        HarvestPolymarketVenue(
            [polymarket],
            {"fed-sep26": [duplicate, extra], "fed-empty": []},
        ),
        target_labels=2,
        polymarket_us_event_slugs=("fed-sep26", "fed-empty"),
    )

    assert report["polymarket_us_event_slugs"] == ["fed-sep26", "fed-empty"]
    assert report["polymarket_us_event_slug_markets"] == {"fed-sep26": 2, "fed-empty": 0}
    # 1 sweep + 2 harvested with one duplicate market_id -> 2 unique markets.
    assert report["venue_coverage"]["polymarket_us"]["closed_markets"] == 2
    assert report["venue_coverage"]["polymarket_us"]["final_binary_markets"] == 2
    # 1 settled Kalshi market x 2 unique US markets; 3 would mean the dedup failed.
    assert report["market_pairs_reviewed"] == 2
    assert report["blockers"]["POLYMARKET_US_EVENT_SLUG_EMPTY"] == 1
    assert report["paper_only"] is True


@pytest.mark.asyncio
async def test_backfill_without_slugs_never_calls_event_door(tmp_path):
    class NoDoorVenue(HarvestPolymarketVenue):
        async def list_event_markets(self, event_slug):
            raise AssertionError("event-slug door used without explicit slugs")

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    report = await backfill_historical_validation(
        store,
        HarvestKalshiVenue(),
        NoDoorVenue([], {}),
        target_labels=1,
    )

    assert report["polymarket_us_event_slugs"] == []
    assert report["polymarket_us_event_slug_markets"] == {}


def test_parse_polymarket_us_event_slugs_bounds_and_normalizes():
    assert _parse_polymarket_us_event_slugs(None) == ()
    assert _parse_polymarket_us_event_slugs(
        ["USCPI-July-YoY-2026-08-12, uscpi-june-yoy-2026-07-15", "uscpi-july-yoy-2026-08-12"]
    ) == ("uscpi-july-yoy-2026-08-12", "uscpi-june-yoy-2026-07-15")
    with pytest.raises(ValueError):
        _parse_polymarket_us_event_slugs(["not a slug!"])
    with pytest.raises(ValueError):
        _parse_polymarket_us_event_slugs(["a,"])
    with pytest.raises(ValueError):
        _parse_polymarket_us_event_slugs([f"slug-{index}" for index in range(11)])
