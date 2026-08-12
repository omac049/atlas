from decimal import Decimal

import pytest

from atlas.backfill import (
    _historical_label,
    backfill_historical_validation,
    historical_event_candidates,
)
from atlas.cli import _historical_backfill_due
from atlas.models import MarketStatus, MatchStatus
from atlas.storage import AtlasStore
from atlas.venues.fixtures import fixture_markets
from atlas.verification import verify_equivalence


class HistoricalKalshiVenue:
    def __init__(self, markets):
        self.markets = markets

    async def list_settled_events(self, max_pages=100):
        return [
            {
                "event_ticker": "KXFED-SEP26",
                "title": "Federal Reserve raises federal funds target September 2026",
                "sub_title": "25 basis points",
            }
        ]

    async def list_settled_event_markets(self, event_ticker):
        return self.markets


class HistoricalPolymarketVenue:
    def __init__(self, markets):
        self.markets = markets

    async def list_closed_markets(self, max_pages=20):
        return self.markets

    async def get_terminal_settlement_evidence(self, market_id):
        return {
            "source": "terminal_market_book",
            "settlement": "1",
            "state": "MARKET_STATE_EXPIRED",
        }


@pytest.mark.asyncio
async def test_historical_backfill_creates_positive_and_negative_labels(tmp_path):
    markets = fixture_markets()
    exact = markets["kalshi"][0].model_copy(deep=True)
    exact.status = MarketStatus.SETTLED
    exact.raw_market_json["result"] = "yes"
    exact.raw_market_json["event_ticker"] = "KXFED-SEP26"

    mismatch = exact.model_copy(deep=True)
    mismatch.market_id = "kalshi:KXFED-SEP26-T50"
    mismatch.venue_market_id = "KXFED-SEP26-T50"
    mismatch.raw_market_json["result"] = "no"

    polymarket = markets["polymarket_us"][0].model_copy(deep=True)
    polymarket.status = MarketStatus.CLOSED
    polymarket.raw_market_json["question"] = polymarket.title

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    report = await backfill_historical_validation(
        store,
        HistoricalKalshiVenue([exact, mismatch]),
        HistoricalPolymarketVenue([polymarket]),
        target_labels=2,
    )

    assert report["status"] == "MILESTONE_COMPLETE"
    assert report["new_labels"] == 2
    assert report["approved_labels"] == 1
    assert report["rejected_labels"] == 1
    assert report["labels_remaining"] == 0
    assert report["venue_coverage"]["polymarket_us"] == {
        "closed_markets": 1,
        "final_binary_markets": 1,
        "catalog_scope": "all",
    }
    assert (await store.validation_summary())["trusted_labels"] == 2
    assert (await store.latest_historical_backfill())["status"] == "MILESTONE_COMPLETE"


@pytest.mark.asyncio
async def test_historical_backfill_scans_requested_kalshi_series(tmp_path):
    class SeriesKalshiVenue:
        def __init__(self):
            self.captured_series = None

        async def list_settled_events(self, max_pages=100, series_tickers=None):
            self.captured_series = series_tickers
            return [
                {
                    "event_ticker": "KXFEDDECISION-26JUL",
                    "title": "Fed decision in July?",
                    "sub_title": "On Jul 29, 2026",
                }
            ]

        async def list_settled_event_markets(self, event_ticker):
            return []

    venue = SeriesKalshiVenue()
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    report = await backfill_historical_validation(
        store,
        venue,
        HistoricalPolymarketVenue([]),
        target_labels=1,
        kalshi_series_tickers=("KXFEDDECISION", "KXFED"),
    )

    assert venue.captured_series == ("KXFEDDECISION", "KXFED")
    assert report["kalshi_series_tickers"] == ["KXFEDDECISION", "KXFED"]
    assert report["kalshi_series_event_counts"] == {"KXFEDDECISION": 1, "KXFED": 0}
    assert report["blockers"]["KALSHI_SERIES_EVENT_SCAN_EMPTY"] == 1
    assert report["paper_only"] is True


@pytest.mark.asyncio
async def test_requested_series_events_bypass_the_lexical_candidate_gate(tmp_path):
    """'Fed decision in July?' shares too few tokens with 'increase interest rates by
    25 bps after the July 2026 meeting' to match lexically; an explicitly requested
    series must still reach deterministic verification."""
    kalshi_market = fixture_markets()["kalshi"][0].model_copy(deep=True)
    kalshi_market.status = MarketStatus.SETTLED
    kalshi_market.raw_market_json["result"] = "yes"
    kalshi_market.raw_market_json["event_ticker"] = "KXFEDDECISION-26JUL"

    class SeriesKalshiVenue:
        async def list_settled_events(self, max_pages=100, series_tickers=None):
            return [
                {
                    "event_ticker": "KXFEDDECISION-26JUL",
                    "title": "Fed decision in July?",
                    "sub_title": "On Jul 29, 2026",
                }
            ]

        async def list_settled_event_markets(self, event_ticker):
            return [kalshi_market]

    polymarket = fixture_markets()["polymarket_us"][0].model_copy(deep=True)
    polymarket.status = MarketStatus.CLOSED
    polymarket.title = "Will the Fed increase interest rates by 25 bps after the July 2026 meeting?"
    polymarket.raw_market_json["question"] = polymarket.title

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    report = await backfill_historical_validation(
        store,
        SeriesKalshiVenue(),
        HistoricalPolymarketVenue([polymarket]),
        target_labels=1,
        kalshi_series_tickers=("KXFEDDECISION",),
    )

    assert report["cross_venue_event_candidates"] == 1
    assert report["market_pairs_reviewed"] == 1
    assert report["kalshi_series_event_counts"] == {"KXFEDDECISION": 1}
    assert "NO_CROSS_VENUE_SETTLED_EVENT_OVERLAP" not in report["blockers"]


def test_historical_label_keeps_review_pairs_inconclusive():
    markets = fixture_markets()
    pair = verify_equivalence(
        markets["kalshi"][0].model_copy(update={"threshold": Decimal(50)}),
        markets["polymarket_us"][0],
    )

    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert _historical_label(pair, "yes", "no") == (None, "INCONCLUSIVE")


def test_historical_event_candidates_require_strong_identity_overlap():
    market = fixture_markets()["polymarket_us"][0]
    events = [
        {
            "event_ticker": "WEAK",
            "title": "Federal election result",
            "sub_title": "",
        },
        {
            "event_ticker": "STRONG",
            "title": "Federal Reserve raises federal funds target",
            "sub_title": "September 2026",
        },
    ]

    candidates = historical_event_candidates(events, [market])

    assert [item[0]["event_ticker"] for item in candidates] == ["STRONG"]


def test_historical_event_candidates_prioritize_distinctive_overlap():
    market = fixture_markets()["polymarket_us"][0]
    events = [
        {
            "event_ticker": "GENERIC",
            "title": "Federal Reserve event in September",
            "sub_title": "",
        },
        {
            "event_ticker": "DISTINCTIVE",
            "title": "Federal Reserve raises federal funds target",
            "sub_title": "September 2026",
        },
    ]

    candidates = historical_event_candidates(events, [market])

    assert [item[0]["event_ticker"] for item in candidates] == [
        "DISTINCTIVE",
        "GENERIC",
    ]


def test_historical_event_candidates_reject_generic_sports_overlap():
    market = fixture_markets()["polymarket_us"][0].model_copy(deep=True)
    market.title = "Crook Town AFC vs Pickering Town FC: Neither team to score first?"
    market.raw_market_json["question"] = market.title
    market.venue_market_id = "crook-pickering-2026-08-10-first-score"
    events = [
        {
            "event_ticker": "KXMLS-26AUG10DAL",
            "title": "Will FC Dallas record the first goal of the game?",
            "sub_title": "FC Dallas vs Austin FC",
        }
    ]

    assert historical_event_candidates(events, [market]) == []


def test_historical_event_candidates_reject_conflicting_event_dates():
    market = fixture_markets()["polymarket_us"][0].model_copy(deep=True)
    market.title = "Will WTI Crude Oil hit $80 Week of August 10 2026?"
    market.raw_market_json["question"] = market.title
    market.venue_market_id = "wti-high-2026-08-10"
    events = [
        {
            "event_ticker": "KXWTI-26AUG07",
            "title": "Will WTI Oil close above $78 on August 7 2026?",
            "sub_title": "WTI Oil",
        }
    ]

    assert historical_event_candidates(events, [market]) == []


def test_historical_event_candidates_reject_unrelated_elections_with_shared_generic_terms():
    market = fixture_markets()["polymarket_us"][0].model_copy(deep=True)
    market.title = "Will Marsha Blackburn win the 2026 Tennessee Governor Republican primary election?"
    market.raw_market_json["question"] = market.title
    market.venue_market_id = "tennessee-primary-marsha-blackburn"
    events = [
        {
            "event_ticker": "KXPRIMARY-GOVORNOMR26",
            "title": "Who will win the Republican Governor primary?",
            "sub_title": "",
        }
    ]

    assert historical_event_candidates(events, [market]) == []


@pytest.mark.asyncio
async def test_recent_historical_backfill_is_not_due(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_historical_backfill(
        {
            "status": "EXTERNAL_EVIDENCE_BLOCKED",
            "completed_at": "2999-01-01T00:00:00+00:00",
        }
    )

    assert await _historical_backfill_due(store, 86_400) is False
