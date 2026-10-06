"""Scheduled-job health, read from each job's own records.

Three nightly jobs failed silently for 5 to 12 nights in September 2026, and on
2026-10-06 none ran at all: macOS had installed an update and left the Mac at
the login window, where launch agents do not run. Nothing said so. This module
reads what each job wrote (its timestamped log lines, or for the weekly reports
the report files themselves) and states, per job: when it last succeeded, when
it last failed, and whether it is late.

A job is LATE once it has not succeeded within ``GRACE`` of the first scheduled
run after its last success. A failure newer than the last success is FAILING.
Only the tail of each log is read, so a large log never slows the dashboard.

Read-only and local: no network, no database, nothing executed.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

GRACE = timedelta(hours=3)
TAIL_BYTES = 256 * 1024
DAYS = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")
_STAMPED = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) (.*)$")
# "ERROR ..." from every job's own log() helper, or a step's non-zero return code.
_FAILURE = re.compile(r"^(ERROR\b|\S+ rc=[1-9])")
_MESSAGE_CHARS = 200


@dataclass(frozen=True)
class Job:
    name: str
    log: Path
    hour: int
    minute: int
    weekday: int | None = None  # None = daily; 0 = Monday, as in datetime.weekday()
    success: re.Pattern | None = None  # a success line in the log
    reports: tuple[Path, str] | None = None  # or: (directory, glob) of report files

    @property
    def schedule(self) -> str:
        days = "daily" if self.weekday is None else DAYS[self.weekday]
        return f"{days} {self.hour:02d}:{self.minute:02d}"


def _tail(path: Path) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - TAIL_BYTES))
            data = handle.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > TAIL_BYTES else lines  # the first line may be cut


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return None


def _from_log(job: Job, lines: list[str]):
    success = failure = None
    message = None
    for line in lines:
        match = _STAMPED.match(line)
        if not match:
            continue
        at = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        text = match.group(2)
        if job.success is not None and job.success.search(text):
            success = at
        elif _FAILURE.search(text):
            failure, message = at, text
    return success, failure, message


def _from_reports(job: Job, lines: list[str]):
    directory, pattern = job.reports
    stamps = [stamp for path in directory.glob(pattern) if (stamp := _mtime(path))]
    success = max(stamps, default=None)
    # The weekly jobs' logs are bare stdout with no timestamps: a traceback is
    # dated by the log's own modification time.
    failure = message = None
    written = _mtime(job.log)
    if written and (success is None or written > success) and any(
        line.startswith("Traceback") for line in lines
    ):
        failure = written
        message = next((line.strip() for line in reversed(lines) if line.strip()), None)
    return success, failure, message


def _next_run_after(job: Job, moment: datetime, tz: tzinfo) -> datetime:
    local = moment.astimezone(tz)
    candidate = local.replace(hour=job.hour, minute=job.minute, second=0, microsecond=0)
    while candidate <= local or (job.weekday is not None and candidate.weekday() != job.weekday):
        candidate += timedelta(days=1)
    return candidate


def _iso(at: datetime | None) -> str | None:
    return at.astimezone(UTC).isoformat() if at else None


def job_health(job: Job, *, now: datetime, tz: tzinfo) -> dict:
    lines = _tail(job.log)
    if job.reports is not None:
        success, failure, message = _from_reports(job, lines)
    else:
        success, failure, message = _from_log(job, lines)

    expected_by = _next_run_after(job, success, tz) + GRACE if success else None
    if failure and (success is None or failure > success):
        state = "FAILING"
    elif success is None:
        state = "NO_RECORD"
    elif now >= expected_by:
        state = "LATE"
    else:
        state = "OK"
    return {
        "name": job.name,
        "schedule": job.schedule,
        "state": state,
        "last_success_at": _iso(success),
        "last_failure_at": _iso(failure),
        "last_failure": message[:_MESSAGE_CHARS] if message else None,
        "expected_by": _iso(expected_by),
    }


_LOGS = Path.home() / "Library" / "Logs"
_DATA = Path(__file__).resolve().parent.parent / "data"

# Schedules mirror deploy/com.atlas.<name>.plist (pinned by a test).
JOBS: tuple[Job, ...] = (
    Job("backup", _LOGS / "atlas-backup.log", 3, 30, success=re.compile(r"^backup ok")),
    Job("site", _LOGS / "atlas-site.log", 4, 0, success=re.compile(r"^(published|publish skipped)")),
    Job("fees", _LOGS / "atlas-fees.log", 4, 20, success=re.compile(r"^published")),
    Job("gsc", _LOGS / "atlas-gsc.log", 6, 15, success=re.compile(r"^report rc=0")),
    Job("study", _LOGS / "atlas-study.log", 7, 0, weekday=0,
        reports=(_DATA / "study", "study-report-*.json")),
    Job("intel", _LOGS / "atlas-intel.log", 7, 15, weekday=0,
        reports=(_DATA / "intel", "divergence-report-*.md")),
)


def all_job_health(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(UTC)
    tz = now.astimezone().tzinfo  # launchd schedules run on the Mac's local clock
    return [job_health(job, now=now, tz=tz) for job in JOBS]
