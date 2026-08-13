"""Explicit event-ticker filter for targeted series harvests.

Kalshi lists 24 hourly KXBTCD/KXETHD events per day; only the noon-ET event
(ticker hour code 12, e.g. KXBTCD-26AUG1312 — verified live 2026-08-13)
overlaps Polymarket's daily crypto markets. The filter narrows ONLY the
explicitly requested series events before they are prepended to the candidate
pool so the bounded candidate budget is not consumed by the 23 non-overlapping
hours. It must never touch the general recent scan or its lexical candidates.
"""

import inspect
import sys

import pytest
from test_backfill import HistoricalPolymarketVenue

from atlas import cli as atlas_cli
from atlas.backfill import backfill_historical_validation
from atlas.cli import (
    MAX_KALSHI_EVENT_TICKER_FILTER_LENGTH,
    _parse_kalshi_event_ticker_filter,
    learning_backfill,
    learning_backfill_batch,
)
from atlas.models import MarketStatus
from atlas.storage import AtlasStore
from atlas.venues.fixtures import fixture_markets

NOON_EVENT = {
    "event_ticker": "KXBTCD-26AUG1312",
    "title": "BTC price on Aug 13, 2026 at 12pm EDT?",
    "sub_title": "On Aug 13, 2026 at 12pm EDT",
}
AFTERNOON_EVENT = {
    "event_ticker": "KXBTCD-26AUG1314",
    "title": "BTC price on Aug 13, 2026 at 2pm EDT?",
    "sub_title": "On Aug 13, 2026 at 2pm EDT",
}
FED_EVENT = {
    "event_ticker": "KXFED-SEP26",
    "title": "Federal Reserve raises federal funds target September 2026",
    "sub_title": "25 basis points",
}


class RecordingKalshiVenue:
    def __init__(self, events):
        self.events = events
        self.fetched_event_tickers = []

    async def list_settled_events(self, max_pages=100, series_tickers=None):
        return self.events

    async def list_settled_event_markets(self, event_ticker):
        self.fetched_event_tickers.append(event_ticker)
        return []


def _final_polymarket_market():
    market = fixture_markets()["polymarket_us"][0].model_copy(deep=True)
    market.status = MarketStatus.CLOSED
    market.raw_market_json["question"] = market.title
    return market


@pytest.mark.asyncio
async def test_event_ticker_filter_narrows_requested_series_events(tmp_path):
    venue = RecordingKalshiVenue([NOON_EVENT, AFTERNOON_EVENT])
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    report = await backfill_historical_validation(
        store,
        venue,
        HistoricalPolymarketVenue([_final_polymarket_market()]),
        target_labels=1,
        kalshi_series_tickers=("KXBTCD",),
        kalshi_event_ticker_filter="12$",
    )

    assert report["cross_venue_event_candidates"] == 1
    assert venue.fetched_event_tickers == ["KXBTCD-26AUG1312"]
    assert report["kalshi_event_ticker_filter"] == "12$"
    # Scan diagnostics still describe the unfiltered series scan.
    assert report["kalshi_series_event_counts"] == {"KXBTCD": 2}


@pytest.mark.asyncio
async def test_no_filter_leaves_series_candidates_unchanged(tmp_path):
    venue = RecordingKalshiVenue([NOON_EVENT, AFTERNOON_EVENT])
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    report = await backfill_historical_validation(
        store,
        venue,
        HistoricalPolymarketVenue([_final_polymarket_market()]),
        target_labels=1,
        kalshi_series_tickers=("KXBTCD",),
    )

    assert report["cross_venue_event_candidates"] == 2
    assert sorted(venue.fetched_event_tickers) == [
        "KXBTCD-26AUG1312",
        "KXBTCD-26AUG1314",
    ]
    assert report["kalshi_event_ticker_filter"] is None


@pytest.mark.asyncio
async def test_filter_never_touches_non_series_candidates(tmp_path):
    """KXFED-SEP26 reaches the pool lexically, not via the requested series;
    the filter must drop the 2pm crypto event yet leave the Fed event alone
    even though its ticker does not match '12$'."""
    venue = RecordingKalshiVenue([FED_EVENT, NOON_EVENT, AFTERNOON_EVENT])
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    report = await backfill_historical_validation(
        store,
        venue,
        HistoricalPolymarketVenue([_final_polymarket_market()]),
        target_labels=1,
        kalshi_series_tickers=("KXBTCD",),
        kalshi_event_ticker_filter="12$",
    )

    assert report["cross_venue_event_candidates"] == 2
    assert sorted(venue.fetched_event_tickers) == ["KXBTCD-26AUG1312", "KXFED-SEP26"]


@pytest.mark.asyncio
async def test_filter_without_requested_series_never_touches_general_scan(tmp_path):
    venue = RecordingKalshiVenue([FED_EVENT])
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    report = await backfill_historical_validation(
        store,
        venue,
        HistoricalPolymarketVenue([_final_polymarket_market()]),
        target_labels=1,
        kalshi_event_ticker_filter="12$",
    )

    assert report["cross_venue_event_candidates"] == 1
    assert venue.fetched_event_tickers == ["KXFED-SEP26"]
    assert report["kalshi_event_ticker_filter"] == "12$"


def test_event_ticker_filter_parse_defaults_to_none():
    assert _parse_kalshi_event_ticker_filter(None) is None


def test_event_ticker_filter_parse_strips_whitespace():
    assert _parse_kalshi_event_ticker_filter(" 12$ ") == "12$"


@pytest.mark.parametrize("raw_value", ["", "   ", "[", "(?P<broken", "a" * 81])
def test_event_ticker_filter_parse_rejects_invalid_values(raw_value):
    with pytest.raises(ValueError):
        _parse_kalshi_event_ticker_filter(raw_value)


def test_event_ticker_filter_length_bound_is_explicit():
    assert MAX_KALSHI_EVENT_TICKER_FILTER_LENGTH == 80


@pytest.mark.asyncio
async def test_learning_backfill_passes_event_ticker_filter(monkeypatch):
    captured = {}

    async def fake_backfill(*args, **kwargs):
        captured.update(kwargs)
        return {
            "status": "EXTERNAL_EVIDENCE_BLOCKED",
            "labels_after": 0,
            "target_labels": 1,
            "polymarket_final_binary_markets": 0,
            "kalshi_events_scanned": 0,
            "cross_venue_event_candidates": 0,
            "new_labels": 0,
            "venue_coverage": {},
            "kalshi_event_ticker_filter": "12$",
            "blockers": {},
        }

    monkeypatch.setattr("atlas.cli.backfill_historical_validation", fake_backfill)

    await learning_backfill(
        True,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        ("21",),
        kalshi_series_tickers=("KXBTCD", "KXETHD"),
        kalshi_event_ticker_filter="12$",
    )

    assert captured["kalshi_series_tickers"] == ("KXBTCD", "KXETHD")
    assert captured["kalshi_event_ticker_filter"] == "12$"


def test_batch_backfill_has_no_event_ticker_filter_parameter():
    """The filter is an explicit-harvest tool only — batch and scheduled runs
    must not accept or default it."""
    assert "kalshi_event_ticker_filter" not in inspect.signature(
        learning_backfill_batch
    ).parameters


def test_cli_rejects_event_ticker_filter_on_batch(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["atlas", "learning", "backfill-batch", "--live", "--kalshi-event-ticker-filter", "12$"],
    )
    with pytest.raises(SystemExit) as excinfo:
        atlas_cli.main()
    assert excinfo.value.code == 2
    assert "--kalshi-event-ticker-filter" in capsys.readouterr().err


def test_cli_rejects_invalid_event_ticker_filter_regex(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["atlas", "learning", "backfill", "--live", "--kalshi-event-ticker-filter", "["],
    )
    with pytest.raises(SystemExit) as excinfo:
        atlas_cli.main()
    assert excinfo.value.code == 2
    assert "invalid --kalshi-event-ticker-filter regex" in capsys.readouterr().err
