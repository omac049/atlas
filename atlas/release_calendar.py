"""US macro release calendar for gap-radar burst pacing.

PAPER-ONLY pacing aid. This module decides nothing about markets, evidence,
or labels — it only tells the continuous monitor when to run its read-only
gap-radar scan more often, because cross-venue dislocations concentrate in
the minutes around scheduled data releases. A wrong or stale entry changes
scan cadence and nothing else.

Entries are hardcoded UTC instants from published agency schedules:

- FOMC decisions: the Federal Reserve's published meeting calendar
  (statement at 14:00 ET). The Sep 16 and Dec 9 dates are corroborated by
  venue-published meeting anchors already captured in this repo.
- Jobs report (BLS Employment Situation): first Friday, 08:30 ET.
- CPI (BLS): repo-confirmed Sep 11 release; later months must be added from
  the published BLS schedule — an absent entry only means base cadence.
- ISM Report On Business: manufacturing on the first business day,
  services on the third business day, 10:00 ET.

UTC offsets account for the US DST transition on 2026-11-01 (ET is UTC-4
before, UTC-5 after). Keep this table short and forward-looking; prune past
entries when adding new ones.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

# Scan every BURST_INTERVAL_SECONDS inside a window; the monitor's normal
# interval applies outside. The window opens before the print so the radar
# baselines pre-release quotes, and stays open while books re-price.
BURST_INTERVAL_SECONDS = 30
WINDOW_BEFORE = timedelta(minutes=10)
WINDOW_AFTER = timedelta(minutes=50)

_UTC = UTC

SCHEDULED_RELEASES: tuple[tuple[str, datetime], ...] = (
    ("ism_manufacturing_aug", datetime(2026, 9, 1, 14, 0, tzinfo=_UTC)),
    ("ism_services_aug", datetime(2026, 9, 3, 14, 0, tzinfo=_UTC)),
    ("jobs_report_aug", datetime(2026, 9, 4, 12, 30, tzinfo=_UTC)),
    ("cpi_aug", datetime(2026, 9, 11, 12, 30, tzinfo=_UTC)),
    ("fomc_decision_sep", datetime(2026, 9, 16, 18, 0, tzinfo=_UTC)),
    ("ism_manufacturing_sep", datetime(2026, 10, 1, 14, 0, tzinfo=_UTC)),
    ("jobs_report_sep", datetime(2026, 10, 2, 12, 30, tzinfo=_UTC)),
    ("ism_services_sep", datetime(2026, 10, 5, 14, 0, tzinfo=_UTC)),
    ("fomc_decision_oct", datetime(2026, 10, 28, 18, 0, tzinfo=_UTC)),
    ("ism_manufacturing_oct", datetime(2026, 11, 2, 15, 0, tzinfo=_UTC)),
    ("ism_services_oct", datetime(2026, 11, 4, 15, 0, tzinfo=_UTC)),
    ("jobs_report_oct", datetime(2026, 11, 6, 13, 30, tzinfo=_UTC)),
    ("ism_manufacturing_nov", datetime(2026, 12, 1, 15, 0, tzinfo=_UTC)),
    ("ism_services_nov", datetime(2026, 12, 3, 15, 0, tzinfo=_UTC)),
    ("jobs_report_nov", datetime(2026, 12, 4, 13, 30, tzinfo=_UTC)),
    ("fomc_decision_dec", datetime(2026, 12, 9, 19, 0, tzinfo=_UTC)),
)


def active_release_window(now: datetime) -> str | None:
    """The name of the release whose burst window contains ``now``, or None."""
    for name, release_at in SCHEDULED_RELEASES:
        if release_at - WINDOW_BEFORE <= now <= release_at + WINDOW_AFTER:
            return name
    return None


# How long before a release the machine must be held awake. macOS user agents
# cannot WAKE a sleeping Mac (that needs a root `pmset` schedule) — they can
# only stop an awake one from sleeping. Ten hours reaches back to the previous
# evening for an 05:30 local jobs/CPI print, when the machine is realistically
# still awake for the assertion to latch onto. The margin past WINDOW_AFTER
# keeps the hold through the last burst scan.
KEEP_AWAKE_BEFORE = timedelta(hours=10)
KEEP_AWAKE_MARGIN = timedelta(minutes=10)


def keep_awake_window(now: datetime) -> tuple[str, int] | None:
    """(release name, seconds the machine must stay awake) or None.

    Pure calendar arithmetic for deploy/atlas_awake.py, kept here so the burst
    scans and the keep-awake hold can never disagree about what a release is.
    The seconds count always runs to the END of that release's hold, so a
    caffeinate assertion started mid-window still covers the remainder.
    """
    for name, release_at in SCHEDULED_RELEASES:
        start = release_at - KEEP_AWAKE_BEFORE
        end = release_at + WINDOW_AFTER + KEEP_AWAKE_MARGIN
        if start <= now <= end:
            return name, max(1, int((end - now).total_seconds()))
    return None


def next_keep_awake_start(now: datetime) -> datetime | None:
    """When the next hold begins, or None when the calendar has run out."""
    starts = sorted(
        release_at - KEEP_AWAKE_BEFORE
        for _, release_at in SCHEDULED_RELEASES
        if release_at - KEEP_AWAKE_BEFORE > now
    )
    return starts[0] if starts else None


def radar_delay_seconds(now: datetime, base_interval: int) -> tuple[int, str | None]:
    """(sleep seconds, active release name) for the monitor's radar pacing."""
    name = active_release_window(now)
    if name is not None:
        return BURST_INTERVAL_SECONDS, name
    return base_interval, None
