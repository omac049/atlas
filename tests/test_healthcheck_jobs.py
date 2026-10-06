"""The watchdog tells the owner when a scheduled job turns LATE or FAILING.

The dashboard's jobs panel only helps someone who opens it, and on 2026-10-06
the dashboard was down along with everything else. The watchdog (run every
60 s) posts one macOS notification when a job enters a bad state, once per
change, and records each change in its own log.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def watchdog(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "atlas_healthcheck", ROOT / "deploy" / "atlas_healthcheck.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "WATCHDOG_LOG", tmp_path / "healthcheck.log")
    monkeypatch.setattr(module, "JOB_STATES_FILE", tmp_path / "state" / "job-states.json")
    module.sent = []
    monkeypatch.setattr(module, "notify", module.sent.append)
    return module


def _jobs(monkeypatch, **states):
    import atlas.job_health

    jobs = [{"name": name, "state": state} for name, state in states.items()]
    monkeypatch.setattr(atlas.job_health, "all_job_health", lambda: jobs)


def test_the_first_run_names_every_job_already_in_trouble(watchdog, monkeypatch):
    _jobs(monkeypatch, backup="LATE", fees="FAILING", study="OK")
    watchdog.check_jobs()
    assert watchdog.sent == ["backup LATE, fees FAILING. Open the dashboard's Jobs panel."]


def test_an_unchanged_state_is_not_repeated(watchdog, monkeypatch):
    _jobs(monkeypatch, fees="FAILING")
    watchdog.check_jobs()
    watchdog.check_jobs()
    assert len(watchdog.sent) == 1


def test_only_the_job_that_changed_is_named(watchdog, monkeypatch, tmp_path):
    _jobs(monkeypatch, backup="OK", fees="FAILING")
    watchdog.check_jobs()
    _jobs(monkeypatch, backup="LATE", fees="FAILING")
    watchdog.check_jobs()
    assert watchdog.sent[-1] == "backup LATE. Open the dashboard's Jobs panel."
    assert "job backup: OK -> LATE" in (tmp_path / "healthcheck.log").read_text()


def test_a_recovery_is_logged_but_not_notified(watchdog, monkeypatch, tmp_path):
    _jobs(monkeypatch, fees="FAILING")
    watchdog.check_jobs()
    _jobs(monkeypatch, fees="OK")
    watchdog.check_jobs()
    assert len(watchdog.sent) == 1
    assert "job fees: FAILING -> OK" in (tmp_path / "healthcheck.log").read_text()


def test_a_notification_that_cannot_be_shown_is_logged_not_raised(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "atlas_healthcheck", ROOT / "deploy" / "atlas_healthcheck.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "WATCHDOG_LOG", tmp_path / "healthcheck.log")

    def refuse(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="not authorized")

    monkeypatch.setattr(module.subprocess, "run", refuse)
    module.notify("fees FAILING")
    assert "ERROR notification failed: not authorized" in (tmp_path / "healthcheck.log").read_text()
