import asyncio

import httpx
import pytest

from atlas.cli import (
    BATCH_DEFAULT_GLOBAL_TAG_IDS,
    BATCH_DEFAULT_KALSHI_SERIES_TICKERS,
    BATCH_MAX_CANDIDATE_EVENTS,
    BATCH_MAX_GLOBAL_PAGES,
    BATCH_MAX_MARKET_PAIRS,
    BATCH_MAX_RESOLVED_PAIRS,
    MAX_GLOBAL_TAG_IDS,
    MAX_KALSHI_SERIES_TICKERS,
    TARGETED_GLOBAL_TAG_IDS,
    _parse_batch_series_tickers,
    _parse_batch_tag_ids,
    _parse_global_tag_ids,
    _parse_kalshi_series_tickers,
    _run_scheduled_backfill,
    _safe_scan_pairs,
    learning_backfill,
    learning_backfill_batch,
)


def test_global_tag_id_override_defaults_to_targeted_catalog():
    assert _parse_global_tag_ids(None) == TARGETED_GLOBAL_TAG_IDS


def test_global_tag_id_override_accepts_repeated_and_comma_separated_values():
    assert _parse_global_tag_ids(["144, 100196", "144", "103840"]) == (
        "144",
        "100196",
        "103840",
    )


@pytest.mark.parametrize("raw_values", [["0"], ["abc"], ["144,,21"]])
def test_global_tag_id_override_rejects_invalid_values(raw_values):
    with pytest.raises(ValueError):
        _parse_global_tag_ids(raw_values)


def test_global_tag_id_override_is_bounded():
    raw_values = [",".join(str(value) for value in range(1, MAX_GLOBAL_TAG_IDS + 2))]

    with pytest.raises(ValueError, match="at most"):
        _parse_global_tag_ids(raw_values)


def test_batch_tag_ids_default_to_remaining_probe_set():
    assert _parse_batch_tag_ids(None) == BATCH_DEFAULT_GLOBAL_TAG_IDS


def test_batch_tag_ids_reuse_override_validation():
    assert _parse_batch_tag_ids(["21,84", "101031"]) == ("21", "84", "101031")


def test_kalshi_series_tickers_default_to_no_series_scan():
    assert _parse_kalshi_series_tickers(None) == ()


def test_kalshi_series_tickers_accept_repeated_comma_separated_lowercase_values():
    assert _parse_kalshi_series_tickers(["kxfeddecision, KXFED", "KXFEDDECISION"]) == (
        "KXFEDDECISION",
        "KXFED",
    )


@pytest.mark.parametrize("raw_values", [["KXFED,,KXCPI"], ["KX FED"], ["123FED"], ["KXFED-26JUL"]])
def test_kalshi_series_tickers_reject_invalid_values(raw_values):
    with pytest.raises(ValueError):
        _parse_kalshi_series_tickers(raw_values)


def test_kalshi_series_tickers_are_bounded():
    raw_values = [",".join(f"KX{index}" for index in range(MAX_KALSHI_SERIES_TICKERS + 1))]

    with pytest.raises(ValueError, match="at most"):
        _parse_kalshi_series_tickers(raw_values)


def test_batch_series_tickers_default_to_macro_series_beyond_recent_paging():
    """The scheduled default must reach the macro pairs that recent-first settled-event
    paging misses: the FOMC decision/level series and the CPI YoY series."""
    assert _parse_batch_series_tickers(None) == BATCH_DEFAULT_KALSHI_SERIES_TICKERS
    assert BATCH_DEFAULT_KALSHI_SERIES_TICKERS == ("KXFEDDECISION", "KXFED", "KXCPIYOY")


def test_batch_series_tickers_reuse_override_validation():
    assert _parse_batch_series_tickers(["KXCPIYOY"]) == ("KXCPIYOY",)


@pytest.mark.asyncio
async def test_learning_backfill_passes_global_tag_override(monkeypatch):
    captured = {}

    async def fake_backfill(*args, **kwargs):
        captured["venue"] = kwargs["additional_polymarket_venues"]["polymarket_global"]
        return {
            "status": "EXTERNAL_EVIDENCE_BLOCKED",
            "labels_after": 0,
            "target_labels": 1,
            "polymarket_final_binary_markets": 0,
            "kalshi_events_scanned": 0,
            "cross_venue_event_candidates": 0,
            "new_labels": 0,
            "venue_coverage": {},
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
        ("144", "100196"),
    )

    assert captured["venue"].tag_ids == ("144", "100196")
    assert captured["venue"].catalog_scope == "tagged:144,100196"
    assert not hasattr(captured["venue"], "get_orderbook")


@pytest.mark.asyncio
async def test_learning_backfill_batch_runs_each_tag_with_bounded_paper_only_config(monkeypatch, capsys):
    calls = []

    async def fake_backfill(*args, **kwargs):
        tag_id = kwargs["additional_polymarket_venues"]["polymarket_global"].tag_ids[0]
        calls.append(
            {
                "tag_id": tag_id,
                "pages": kwargs["additional_polymarket_pages"],
                "candidate_events": kwargs["max_candidate_events"],
                "market_pairs": kwargs["max_market_pairs"],
                "resolved_pairs": kwargs["max_resolved_pairs"],
            }
        )
        return {
            "status": "NO_NEW_TRUSTED_LABELS",
            "paper_only": True,
            "labels_before": 0,
            "labels_after": 0,
            "approved_labels": 0,
            "rejected_labels": 0,
            "new_labels": 0,
            "market_pairs_reviewed": 3,
            "resolved_pairs": 0,
            "inconclusive_pairs": 3,
            "blockers": {},
        }

    monkeypatch.setattr("atlas.cli.backfill_historical_validation", fake_backfill)

    report = await learning_backfill_batch(
        True,
        target=1,
        global_pages=1,
        candidate_events=BATCH_MAX_CANDIDATE_EVENTS,
        market_pairs=BATCH_MAX_MARKET_PAIRS,
        resolved_pairs=BATCH_MAX_RESOLVED_PAIRS,
        global_tag_ids=("21", "84"),
    )

    assert [call["tag_id"] for call in calls] == ["21", "84"]
    assert all(call["pages"] == 1 for call in calls)
    assert all(call["candidate_events"] == BATCH_MAX_CANDIDATE_EVENTS for call in calls)
    assert all(call["market_pairs"] == BATCH_MAX_MARKET_PAIRS for call in calls)
    assert all(call["resolved_pairs"] == BATCH_MAX_RESOLVED_PAIRS for call in calls)
    assert report["paper_only"] is True
    assert report["execution_enabled"] is False
    assert [result["tag_id"] for result in report["runs"]] == ["21", "84"]
    assert 'tag_batch_report=' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_learning_backfill_batch_rejects_unbounded_limits():
    with pytest.raises(ValueError, match="candidate_events"):
        await learning_backfill_batch(
            True,
            candidate_events=BATCH_MAX_CANDIDATE_EVENTS + 1,
            global_pages=BATCH_MAX_GLOBAL_PAGES,
        )


@pytest.mark.asyncio
async def test_global_open_catalog_failure_degrades_to_us_only(monkeypatch, capsys):
    async def failed_listing(self, max_pages=2):
        raise httpx.ReadTimeout("gamma stalled")

    monkeypatch.setattr(
        "atlas.cli.PolymarketGlobalHistoricalVenue.list_open_markets", failed_listing
    )

    from atlas.cli import _safe_global_open_markets

    assert await _safe_global_open_markets() == []
    assert "global_open_catalog_failed=ReadTimeout" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_safe_live_scan_failure_keeps_monitor_recoverable(monkeypatch, capsys):
    async def failed_scan(_live):
        raise httpx.ReadTimeout("catalog stalled")

    monkeypatch.setattr("atlas.cli.scan_pairs", failed_scan)

    assert await _safe_scan_pairs(True) == []
    assert "pairs_watch_scan_failed=ReadTimeout" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_learning_backfill_batch_records_tag_failure_and_continues(monkeypatch):
    calls = []

    async def fake_backfill(*args, **kwargs):
        tag_id = args[-1][0]
        calls.append(tag_id)
        if tag_id == "21":
            raise OSError("catalog unavailable")
        return {"status": "NO_NEW_TRUSTED_LABELS", "paper_only": True}

    monkeypatch.setattr("atlas.cli._run_learning_backfill", fake_backfill)

    report = await learning_backfill_batch(
        True,
        target=1,
        kalshi_event_pages=1,
        polymarket_pages=1,
        global_pages=1,
        candidate_events=1,
        market_pairs=1,
        resolved_pairs=1,
        global_tag_ids=("21", "84"),
    )

    assert calls == ["21", "84"]
    assert report["status"] == "BATCH_PARTIAL_FAILURE"
    assert report["failed_tags"] == ["21"]
    assert report["completed_tags"] == ["84"]
    assert report["runs"][0]["error_type"] == "OSError"


@pytest.mark.asyncio
async def test_learning_backfill_batch_times_out_one_tag_and_continues(monkeypatch):
    async def slow_backfill(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr("atlas.cli._run_learning_backfill", slow_backfill)
    monkeypatch.setattr("atlas.cli.BATCH_MAX_TAG_SECONDS", 0.01)

    report = await learning_backfill_batch(
        True,
        target=1,
        kalshi_event_pages=1,
        polymarket_pages=1,
        global_pages=1,
        candidate_events=1,
        market_pairs=1,
        resolved_pairs=1,
        global_tag_ids=("21",),
    )

    assert report["status"] == "BATCH_PARTIAL_FAILURE"
    assert report["failed_tags"] == ["21"]
    assert report["runs"][0]["status"] == "TIMED_OUT"


@pytest.mark.asyncio
async def test_scheduled_backfill_uses_bounded_batch_policy(monkeypatch):
    captured = {}

    async def fake_batch(**kwargs):
        captured.update(kwargs)
        return {"status": "BATCH_COMPLETE", "tag_ids": list(kwargs["global_tag_ids"])}

    monkeypatch.setattr("atlas.cli.learning_backfill_batch", fake_batch)

    report = await _run_scheduled_backfill()

    assert report["status"] == "BATCH_COMPLETE"
    assert captured["live"] is True
    assert captured["target"] == 1
    assert captured["candidate_events"] == BATCH_MAX_CANDIDATE_EVENTS
    assert captured["market_pairs"] == BATCH_MAX_MARKET_PAIRS
    assert captured["resolved_pairs"] == BATCH_MAX_RESOLVED_PAIRS
    assert captured["global_tag_ids"] == BATCH_DEFAULT_GLOBAL_TAG_IDS
    assert captured["kalshi_series_tickers"] == BATCH_DEFAULT_KALSHI_SERIES_TICKERS


@pytest.mark.asyncio
async def test_learning_backfill_batch_scans_default_series_on_every_tag_run(monkeypatch):
    captured_series = []

    async def fake_backfill(*args, **kwargs):
        captured_series.append(kwargs["kalshi_series_tickers"])
        return {"status": "NO_NEW_TRUSTED_LABELS", "paper_only": True}

    monkeypatch.setattr("atlas.cli._run_learning_backfill", fake_backfill)

    report = await learning_backfill_batch(
        True,
        target=1,
        kalshi_event_pages=1,
        polymarket_pages=1,
        global_pages=1,
        candidate_events=1,
        market_pairs=1,
        resolved_pairs=1,
        global_tag_ids=("21", "84"),
    )

    assert captured_series == [BATCH_DEFAULT_KALSHI_SERIES_TICKERS] * 2
    assert report["kalshi_series_tickers"] == list(BATCH_DEFAULT_KALSHI_SERIES_TICKERS)


@pytest.mark.asyncio
async def test_learning_backfill_passes_series_tickers_to_historical_backfill(monkeypatch):
    captured = {}

    async def fake_backfill(*args, **kwargs):
        captured["kalshi_series_tickers"] = kwargs["kalshi_series_tickers"]
        return {
            "status": "EXTERNAL_EVIDENCE_BLOCKED",
            "labels_after": 0,
            "target_labels": 1,
            "polymarket_final_binary_markets": 0,
            "kalshi_events_scanned": 0,
            "cross_venue_event_candidates": 0,
            "new_labels": 0,
            "venue_coverage": {},
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
        ("144",),
        ("KXFEDDECISION",),
    )

    assert captured["kalshi_series_tickers"] == ("KXFEDDECISION",)


def test_batch_default_tags_target_guarantee_reachable_families():
    """The scheduled default must aim bounded capacity at families with a coded
    GUARANTEED settlement path (chamber-control, CPI) — see atlas/settlement.py."""
    assert BATCH_DEFAULT_GLOBAL_TAG_IDS == ("144", "487", "100196", "101701")
    assert set(BATCH_DEFAULT_GLOBAL_TAG_IDS) <= set(TARGETED_GLOBAL_TAG_IDS)
