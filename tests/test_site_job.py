"""The nightly site job: every run ends with a log line, including one that overruns."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def job(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("atlas_site", ROOT / "deploy" / "atlas_site.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "LOG", tmp_path / "site.log")
    monkeypatch.setenv("ATLAS_SITE_PUBLISH_CMD", "true")
    return module


def _overrun_on(job, monkeypatch, step: str) -> None:
    def run(args, **kwargs):
        is_build = isinstance(args, list)
        if (step == "build") == is_build:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return subprocess.CompletedProcess(args, 0, stdout="site_pages=1\n", stderr="")

    monkeypatch.setattr(job.subprocess, "run", run)


def test_a_build_that_overruns_is_logged_and_nothing_is_published(job, tmp_path, monkeypatch):
    _overrun_on(job, monkeypatch, "build")
    job.main()
    text = (tmp_path / "site.log").read_text()
    assert "ERROR build timed out after 900s" in text
    assert "published" not in [line.split(" ", 1)[1] for line in text.splitlines()]


def test_a_publish_that_overruns_is_logged(job, tmp_path, monkeypatch):
    _overrun_on(job, monkeypatch, "publish")
    job.main()
    text = (tmp_path / "site.log").read_text()
    assert "built site_pages=1" in text
    assert "ERROR publish timed out after 600s" in text
