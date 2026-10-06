"""Scheduled-job health, read from each job's own records.

Three nightly jobs failed silently for 5 to 12 nights in September, and on
2026-10-06 none ran at all (the Mac sat at the login window). Each job's state
is derived from what it wrote: a success line or report file, an error line, and
when it was next due after its last success.
"""

import os
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from atlas.job_health import Job, job_health

MST = timezone(timedelta(hours=-7))


def _daily(tmp_path: Path, **overrides) -> Job:
    fields = {
        "name": "backup",
        "log": tmp_path / "backup.log",
        "hour": 3,
        "minute": 30,
        "success": re.compile(r"^backup ok"),
    }
    return Job(**{**fields, **overrides})


def _write(path: Path, *lines: str) -> None:
    path.write_text("".join(f"{line}\n" for line in lines))


def test_a_job_that_succeeded_since_it_was_last_due_is_ok(tmp_path):
    job = _daily(tmp_path)
    _write(job.log, "2026-10-05T10:30:13Z backup ok: snapshot", "2026-10-05T10:30:21Z vacuum ok")
    health = job_health(job, now=datetime(2026, 10, 5, 12, 0, tzinfo=UTC), tz=MST)
    assert health["state"] == "OK"
    assert health["last_success_at"] == "2026-10-05T10:30:13+00:00"
    assert health["schedule"] == "daily 03:30"


def test_a_job_is_late_three_hours_after_a_missed_run(tmp_path):
    job = _daily(tmp_path)
    _write(job.log, "2026-10-04T10:30:13Z backup ok: snapshot")
    # Due 2026-10-05 03:30 MST = 10:30Z. Not late at 13:00Z, late after 13:30Z.
    assert job_health(job, now=datetime(2026, 10, 5, 13, 0, tzinfo=UTC), tz=MST)["state"] == "OK"
    late = job_health(job, now=datetime(2026, 10, 6, 15, 37, tzinfo=UTC), tz=MST)
    assert late["state"] == "LATE"
    assert late["expected_by"] == "2026-10-05T13:30:00+00:00"


def test_an_error_after_the_last_success_is_failing(tmp_path):
    job = _daily(tmp_path, name="fees", success=re.compile(r"^published"))
    _write(
        job.log,
        "2026-10-03T11:44:59Z published",
        "2026-10-04T12:50:18Z ERROR verify did not run rc=124 timed out after 5400s",
        "  File \"x.py\", line 1, in <module>",
    )
    health = job_health(job, now=datetime(2026, 10, 4, 13, 0, tzinfo=UTC), tz=MST)
    assert health["state"] == "FAILING"
    assert health["last_failure_at"] == "2026-10-04T12:50:18+00:00"
    assert health["last_failure"].startswith("ERROR verify did not run rc=124")


def test_a_nonzero_return_code_counts_as_a_failure(tmp_path):
    job = _daily(tmp_path, name="gsc", success=re.compile(r"^report rc=0"))
    _write(job.log, "2026-10-04T13:17:53Z report rc=0 ok", "2026-10-05T13:17:16Z pull rc=1 b.py")
    health = job_health(job, now=datetime(2026, 10, 5, 14, 0, tzinfo=UTC), tz=MST)
    assert health["state"] == "FAILING"


def test_a_job_with_no_record_at_all_says_so(tmp_path):
    health = job_health(_daily(tmp_path), now=datetime(2026, 10, 5, tzinfo=UTC), tz=MST)
    assert health["state"] == "NO_RECORD"
    assert health["last_success_at"] is None


def test_a_weekly_job_reads_success_from_its_report_files(tmp_path):
    reports = tmp_path / "study"
    reports.mkdir()
    report = reports / "study-report-20260928.json"
    report.write_text("{}")
    stamp = datetime(2026, 9, 28, 14, 0, 30, tzinfo=UTC).timestamp()
    os.utime(report, (stamp, stamp))
    job = Job(
        name="study",
        log=tmp_path / "study.log",
        hour=7,
        minute=0,
        weekday=0,
        reports=(reports, "study-report-*.json"),
    )
    # Next due Monday 2026-10-05 07:00 MST = 14:00Z; late from 17:00Z.
    ok = job_health(job, now=datetime(2026, 10, 5, 16, 0, tzinfo=UTC), tz=MST)
    assert ok["state"] == "OK"
    assert ok["schedule"] == "Mondays 07:00"
    late = job_health(job, now=datetime(2026, 10, 5, 18, 0, tzinfo=UTC), tz=MST)
    assert late["state"] == "LATE"


def test_a_traceback_in_an_untimestamped_log_after_the_last_report_is_failing(tmp_path):
    reports = tmp_path / "study"
    reports.mkdir()
    report = reports / "study-report-20260928.json"
    report.write_text("{}")
    old = datetime(2026, 9, 28, 14, 0, tzinfo=UTC).timestamp()
    os.utime(report, (old, old))
    log = tmp_path / "study.log"
    _write(log, "Traceback (most recent call last):", "sqlite3.OperationalError: database is locked")
    crashed = datetime(2026, 10, 5, 14, 0, 5, tzinfo=UTC).timestamp()
    os.utime(log, (crashed, crashed))
    job = Job(
        name="study", log=log, hour=7, minute=0, weekday=0, reports=(reports, "study-report-*.json")
    )
    health = job_health(job, now=datetime(2026, 10, 5, 15, 0, tzinfo=UTC), tz=MST)
    assert health["state"] == "FAILING"
    assert health["last_failure"] == "sqlite3.OperationalError: database is locked"
    assert health["last_failure_at"] == "2026-10-05T14:00:05+00:00"


def test_only_the_tail_of_a_large_log_is_read(tmp_path):
    job = _daily(tmp_path)
    filler = "x" * 1000
    _write(job.log, *([f"2026-09-01T10:30:00Z noise {filler}"] * 2000), "2026-10-05T10:30:13Z backup ok")
    health = job_health(job, now=datetime(2026, 10, 5, 12, 0, tzinfo=UTC), tz=MST)
    assert health["last_success_at"] == "2026-10-05T10:30:13+00:00"


def test_every_schedule_matches_its_launchd_plist():
    import plistlib

    from atlas.job_health import JOBS

    root = Path(__file__).resolve().parent.parent
    for job in JOBS:
        with (root / "deploy" / f"com.atlas.{job.name}.plist").open("rb") as handle:
            when = plistlib.load(handle)["StartCalendarInterval"]
        assert (when["Hour"], when["Minute"]) == (job.hour, job.minute), job.name
        # launchd counts Sunday as 0 and Monday as 1; datetime counts Monday as 0.
        weekday = None if "Weekday" not in when else (when["Weekday"] - 1) % 7
        assert weekday == job.weekday, job.name


def test_the_api_serves_every_job(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import atlas.job_health
    from apps.api.main import app

    monkeypatch.setattr(atlas.job_health, "JOBS", (_daily(tmp_path),))
    payload = TestClient(app).get("/api/jobs").json()
    assert payload["paper_only"] is True
    assert [job["name"] for job in payload["jobs"]] == ["backup"]
    assert payload["jobs"][0]["state"] == "NO_RECORD"
