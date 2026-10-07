"""The dashboard's gap snapshot is refreshed off the request path.

On 2026-10-06 one /api/overview call in every 30 s took 2 to 7 s: the request that
found the snapshot stale reloaded 50,000 observations and re-ran a full-table
aggregate while the dashboard waited. A stale snapshot is now served at once and
replaced in the background.
"""

import asyncio
import sqlite3
import threading

import pytest

import apps.api.main as api
from atlas.gap_radar import paper_bankroll_summary

BANKROLL = paper_bankroll_summary([])


class FakeStore:
    def __init__(self) -> None:
        self.loads = 0
        self.release = threading.Event()
        self.release.set()
        self.fail = False

    async def all_gap_observations(self, limit: int | None = 50000) -> list[dict]:
        self.loads += 1
        # Blocks the loader's own thread, never the caller's event loop.
        assert self.release.wait(timeout=5)
        if self.fail:
            raise sqlite3.OperationalError("database is locked")
        return [{"load": self.loads}]

    async def gap_observation_count(self) -> int:
        return self.loads

    async def gap_subject_aggregates(self) -> dict:
        return {}

    async def executable_gap_observations(self) -> list[dict]:
        return []


@pytest.fixture(autouse=True)
def fresh_snapshot(monkeypatch):
    monkeypatch.setattr(api, "_gap_snapshot", None)
    monkeypatch.setattr(api, "_gap_refresh", None)


def _expire() -> None:
    api._gap_snapshot = (0.0, api._gap_snapshot[1])


async def _settle() -> None:
    if api._gap_refresh is not None:
        await asyncio.wait_for(asyncio.shield(api._gap_refresh), timeout=5)


async def test_the_first_load_waits_and_later_ones_reuse_it():
    store = FakeStore()
    first = await api._gap_observation_snapshot(store)
    again = await api._gap_observation_snapshot(store)
    assert first == again == ([{"load": 1}], 1, {}, BANKROLL)
    assert store.loads == 1


async def test_a_stale_snapshot_is_served_at_once_and_replaced_in_the_background():
    store = FakeStore()
    await api._gap_observation_snapshot(store)
    _expire()
    store.release.clear()  # the reload cannot finish until released

    served = await asyncio.wait_for(api._gap_observation_snapshot(store), timeout=1)

    assert served == ([{"load": 1}], 1, {}, BANKROLL)
    store.release.set()
    await _settle()
    assert await api._gap_observation_snapshot(store) == ([{"load": 2}], 2, {}, BANKROLL)


async def test_only_one_background_refresh_runs_at_a_time():
    store = FakeStore()
    await api._gap_observation_snapshot(store)
    _expire()
    store.release.clear()
    for _ in range(3):
        await api._gap_observation_snapshot(store)
    store.release.set()
    await _settle()
    assert store.loads == 2


async def test_a_failed_refresh_keeps_the_old_snapshot_and_retries():
    store = FakeStore()
    await api._gap_observation_snapshot(store)
    _expire()
    store.fail = True
    await api._gap_observation_snapshot(store)  # serves load 1; reload 2 fails
    await _settle()

    store.fail = False
    assert await api._gap_observation_snapshot(store) == ([{"load": 1}], 1, {}, BANKROLL)  # retry
    await _settle()
    assert await api._gap_observation_snapshot(store) == ([{"load": 3}], 3, {}, BANKROLL)
