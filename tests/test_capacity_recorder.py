from datetime import UTC, datetime, timedelta

import pytest

from atlas.release_calendar import (
    SCHEDULED_RELEASES,
    WINDOW_AFTER,
    WINDOW_BEFORE,
    active_release_window,
)
from atlas.storage import AtlasStore


def _sample(sample_id: str, window: str | None, profit: str, contracts: str = "10") -> dict:
    return {
        "sample_id": sample_id,
        "captured_at": "2026-09-01T14:00:00+00:00",
        "release_window": window,
        "kalshi_market_id": "kalshi:K1",
        "polymarket_market_id": "polymarket_us:P1",
        "event_subject": "us_cpi_yoy|2026-09",
        "profitable_contracts": contracts,
        "total_profit_usd": profit,
        "top_of_book_contracts": "0.05",
        "paper_only": True,
    }


@pytest.fixture
def store(tmp_path):
    return AtlasStore(path=tmp_path / "atlas.sqlite3")


async def test_release_samples_are_summarized_against_the_quiet_baseline(store):
    await store.save_capacity_sample(_sample("a", None, "0"))
    await store.save_capacity_sample(_sample("b", None, "0.002"))
    await store.save_capacity_sample(_sample("c", "cpi_aug", "4.50", contracts="120"))
    summary = await store.capacity_window_summary()
    by_window = {row["window"]: row for row in summary["windows"]}
    assert by_window["_quiet"]["samples"] == 2
    assert by_window["_quiet"]["samples_with_capacity"] == 1
    assert by_window["cpi_aug"]["max_profit_usd"] == pytest.approx(4.50)
    assert by_window["cpi_aug"]["max_profitable_contracts"] == pytest.approx(120)


async def test_zero_capacity_samples_are_recorded_not_dropped(store):
    # A release window that produced nothing is the finding, so the row must
    # exist — an absent row would read as "we never looked".
    await store.save_capacity_sample(_sample("a", "jobs_report_aug", "0", contracts="0"))
    summary = await store.capacity_window_summary()
    row = summary["windows"][0]
    assert row["window"] == "jobs_report_aug"
    assert row["samples"] == 1
    assert row["samples_with_capacity"] == 0


async def test_capacity_samples_never_touch_the_frozen_observation_stream(store):
    await store.save_capacity_sample(_sample("a", "cpi_aug", "1.25"))
    assert await store.recent_gap_observations(limit=10) == []


async def test_summary_is_empty_before_any_window_is_sampled(store):
    assert await store.capacity_window_summary() == {"windows": []}


def test_recorder_stamps_the_window_the_burst_loop_is_in():
    # The recorder is keyed off the same calendar the burst loop paces from,
    # so a sample taken during a burst can never be mislabeled quiet.
    name, release_at = min(SCHEDULED_RELEASES, key=lambda item: item[1])
    assert active_release_window(release_at) == name
    assert active_release_window(release_at - WINDOW_BEFORE + timedelta(seconds=1)) == name
    assert active_release_window(release_at + WINDOW_AFTER - timedelta(seconds=1)) == name


def test_quiet_market_outside_every_window_stamps_none():
    latest = max(release_at for _, release_at in SCHEDULED_RELEASES)
    assert active_release_window(latest + timedelta(days=2)) is None
    assert isinstance(datetime.now(UTC), datetime)
