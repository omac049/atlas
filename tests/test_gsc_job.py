"""The nightly Search Console job: every run ends with a log line, including one that overruns."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def job(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("atlas_gsc", ROOT / "deploy" / "atlas_gsc.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "LOG", tmp_path / "gsc.log")
    return module


def test_a_pull_that_overruns_is_logged_as_a_failure_and_skips_the_report(
    job, tmp_path, monkeypatch
):
    calls = []

    def run(args, **kwargs):
        calls.append(args[-1])
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(job.subprocess, "run", run)
    job.main()
    text = (tmp_path / "gsc.log").read_text()
    assert "pull rc=124 timed out after 600s" in text
    assert calls == ["pull"]


def test_a_report_that_overruns_is_logged_as_a_failure(job, tmp_path, monkeypatch):
    def run(args, **kwargs):
        if args[-1] == "report":
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return subprocess.CompletedProcess(args, 0, stdout='{"rows": 1}\n', stderr="")

    monkeypatch.setattr(job.subprocess, "run", run)
    job.main()
    text = (tmp_path / "gsc.log").read_text()
    assert "pull rc=0" in text
    assert "report rc=124 timed out after 600s" in text
