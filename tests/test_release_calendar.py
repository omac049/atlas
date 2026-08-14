"""Release-calendar burst pacing for the paper-only gap radar.

The calendar only changes how often the read-only radar scan runs; these
tests pin window membership math, calendar hygiene (UTC-aware, chronological),
and that the monitor's burst-aware sleep runs extra radar scans inside a
window while leaving the base cadence alone outside one.
"""

from datetime import UTC, datetime, timedelta

from atlas import cli, release_calendar
from atlas.release_calendar import (
    BURST_INTERVAL_SECONDS,
    SCHEDULED_RELEASES,
    WINDOW_AFTER,
    WINDOW_BEFORE,
    active_release_window,
    radar_delay_seconds,
)


def test_calendar_entries_are_utc_aware_and_chronological():
    times = [release_at for _, release_at in SCHEDULED_RELEASES]
    assert times, "calendar must not be empty while burst mode is wired in"
    for release_at in times:
        assert release_at.tzinfo is not None
        assert release_at.utcoffset() == timedelta(0)
    assert times == sorted(times)


def test_window_membership_and_boundaries():
    name, release_at = SCHEDULED_RELEASES[0]
    assert active_release_window(release_at) == name
    assert active_release_window(release_at - WINDOW_BEFORE) == name
    assert active_release_window(release_at + WINDOW_AFTER) == name
    assert active_release_window(release_at - WINDOW_BEFORE - timedelta(seconds=1)) is None
    assert active_release_window(release_at + WINDOW_AFTER + timedelta(seconds=1)) is None


def test_radar_delay_bursts_only_inside_windows():
    quiet = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)
    assert radar_delay_seconds(quiet, 300) == (300, None)
    name, release_at = SCHEDULED_RELEASES[-1]
    delay, active = radar_delay_seconds(release_at, 300)
    assert delay == BURST_INTERVAL_SECONDS
    assert active == name
    assert BURST_INTERVAL_SECONDS < 300


async def test_burst_aware_sleep_runs_extra_radar_scans(monkeypatch):
    sleeps: list[int] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(cli.asyncio, "sleep", fake_sleep)

    scans: list[bool] = []

    async def fake_scan(live):
        scans.append(live)

    monkeypatch.setattr(cli, "gaps_scan", fake_scan)

    calls: list[datetime] = []

    def fake_delay(now, base_interval):
        calls.append(now)
        if len(calls) == 1:
            return (30, "test_release")
        return (base_interval, None)

    monkeypatch.setattr(release_calendar, "radar_delay_seconds", fake_delay)

    await cli._burst_aware_sleep(90)
    # one 30s burst slice with a radar scan, then a quiet 60s chunk to finish
    assert sleeps == [30, 60]
    assert scans == [True]


async def test_burst_aware_sleep_stays_quiet_outside_windows(monkeypatch):
    sleeps: list[int] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(cli.asyncio, "sleep", fake_sleep)

    async def fail_scan(live):  # pragma: no cover - must not run
        raise AssertionError("radar must not burst outside a release window")

    monkeypatch.setattr(cli, "gaps_scan", fail_scan)
    monkeypatch.setattr(
        release_calendar, "radar_delay_seconds", lambda now, base: (base, None)
    )

    await cli._burst_aware_sleep(150)
    # quiet interval sleeps out in bounded slices with no extra scans
    assert sum(sleeps) == 150
    assert max(sleeps) <= 60
