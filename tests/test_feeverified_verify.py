"""A check without a browser must never read as "the fee page changed"."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from feeverified import verify

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def no_browser(monkeypatch, tmp_path):
    """The state of the machine from 2026-09-16 to 2026-09-28: the browser
    build is gone, and every plain fetch would still succeed."""

    def cannot_start():
        raise RuntimeError(
            "BrowserType.launch: Executable doesn't exist at /cache/chromium_headless_shell-1234\n"
            "Looks like Playwright was just installed or updated."
        )

    fetched = []
    monkeypatch.setattr(verify, "_context", cannot_start)
    monkeypatch.setattr(
        verify, "fetch_with_method", lambda url: fetched.append(url) or ("<html></html>", "plain")
    )
    monkeypatch.setattr(verify, "CHECK_PATH", tmp_path / "check.json")
    monkeypatch.setattr(verify, "STATE_PATH", tmp_path / "verification.json")
    return fetched


def test_a_check_without_a_browser_fetches_nothing_and_writes_nothing(no_browser, tmp_path):
    with pytest.raises(verify.BrowserUnavailable) as raised:
        verify.check_all()
    assert "Executable doesn't exist" in str(raised.value)
    assert "\n" not in str(raised.value)  # one line: it goes into a log
    assert no_browser == [] and not (tmp_path / "check.json").exists()


def test_a_review_without_a_browser_records_no_baseline(no_browser, tmp_path):
    with pytest.raises(verify.BrowserUnavailable):
        verify.mark_reviewed("etsy", "re-read")
    assert no_browser == [] and not (tmp_path / "verification.json").exists()


def test_the_browser_lives_in_the_projects_own_directory():
    """Playwright's shared cache is cleaned by every other install on the machine."""
    assert verify.BROWSERS_DIR == ROOT / "data" / "playwright-browsers"
    env = {k: v for k, v in os.environ.items() if k != "PLAYWRIGHT_BROWSERS_PATH"}
    out = subprocess.run(
        [sys.executable, "-c",
         "import os, feeverified.verify; print(os.environ['PLAYWRIGHT_BROWSERS_PATH'])"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == str(ROOT / "data" / "playwright-browsers")


def test_the_command_line_gives_a_missing_browser_its_own_exit_code(tmp_path):
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(tmp_path / "empty")}
    out = subprocess.run(
        [sys.executable, "-m", "feeverified", "verify"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False, timeout=120,
    )
    assert out.returncode == verify.BROWSER_UNAVAILABLE_EXIT == 3
    assert json.loads(out.stdout)["error"] == "browser_unavailable"


QUOTE = "A 2.6% + $0.15 processing fee on each payment you receive"
PAGE = f"<html><body><p>{QUOTE} from a customer.</p><p>{'Fees explained. ' * 60}</p></body></html>"
SHELL = f"<html><body><div id='app'></div><p>{'Loading. ' * 80}</p></body></html>"
REWORDED = PAGE.replace("2.6% + $0.15", "2.75% + $0.15")


@pytest.fixture
def one_platform(monkeypatch, tmp_path):
    """One schedule with one source, reviewed from a rendered page."""
    fees = tmp_path / "fees"
    fees.mkdir()
    url = "https://example.test/fees"
    (fees / "cashapp.json").write_text(json.dumps(
        {"platform": "cashapp", "sources": [{"url": url}], "quotes": [QUOTE]}
    ))
    state = {"platforms": {"cashapp": {
        "status": "verified", "history": [], "quotes_missing_at_review": [],
        "sources": {url: {"hash": verify.text_hash(PAGE), "status": "verified", "via": "rendered"}},
    }}}
    (fees / "verification.json").write_text(json.dumps(state))
    monkeypatch.setattr(verify, "FEES_DIR", fees)
    monkeypatch.setattr(verify, "STATE_PATH", fees / "verification.json")
    monkeypatch.setattr(verify, "CHECK_PATH", tmp_path / "check.json")
    monkeypatch.setattr(verify, "require_browser", lambda: None)
    monkeypatch.setattr(verify, "close_browser", lambda: None)

    def answer(html: str, method: str) -> None:
        monkeypatch.setattr(verify, "fetch_with_method", lambda _url: (html, method))

    return answer, tmp_path / "check.json"


def checked(path: Path) -> dict:
    return json.loads(path.read_text())["platforms"]["cashapp"]


def test_a_page_that_could_only_be_read_as_a_shell_is_not_a_changed_page(one_platform):
    """The twelve nights: the browser was gone, the plain fetch returned the
    JavaScript shell, and its missing quotes were published as a fee change."""
    answer, path = one_platform
    answer(SHELL, "plain")
    assert verify.check_all() == {"verified": 0, "changed": 0, "unreachable": 1, "unreviewed": 0}
    entry = checked(path)
    assert entry["status"] == "unreachable" and entry["history"] == []
    assert entry["not_evidence"]["quotes"] == [QUOTE]


def test_a_rendered_page_without_the_quote_is_a_changed_page(one_platform):
    answer, path = one_platform
    answer(REWORDED, "rendered")
    assert verify.check_all()["changed"] == 1
    entry = checked(path)
    assert entry["status"] == "changed" and entry["history"][0]["quotes"] == [QUOTE]
    assert "not_evidence" not in entry


def test_weaker_means_that_still_find_every_quote_verify_the_page(one_platform):
    answer, path = one_platform
    answer(PAGE, "impersonated")
    assert verify.check_all()["verified"] == 1
    assert checked(path)["sources"]["https://example.test/fees"]["read_via"] == "impersonated"


def test_evidence_is_compared_with_the_evidence_at_review():
    assert verify.weaker("plain", "rendered") and verify.weaker("impersonated", "rendered")
    assert not verify.weaker("rendered", "rendered") and not verify.weaker("plain", "plain")
    assert not verify.weaker("rendered", "plain")
    assert verify.weaker("plain", "unknown")  # an unrecorded method counts as rendered


class _HungPage:
    """A page whose navigation never returns until its browser is stopped."""

    def __init__(self, stopped) -> None:
        self.stopped = stopped

    def goto(self, *_args, **_kwargs) -> None:
        assert self.stopped.wait(timeout=10), "the watchdog never fired"
        raise RuntimeError("Target page, context or browser has been closed")

    def close(self) -> None:
        raise AssertionError("a page whose browser was stopped is not closed")


def test_a_page_that_never_returns_costs_its_limit_and_no_more(monkeypatch):
    import threading
    import time
    import types

    stopped = threading.Event()
    forgotten = []
    monkeypatch.setattr(verify, "_context",
                        lambda: types.SimpleNamespace(new_page=lambda: _HungPage(stopped)))
    monkeypatch.setattr(verify, "_stop_browser_processes", stopped.set)
    monkeypatch.setattr(verify, "_forget_browser", lambda: forgotten.append(True))
    began = time.monotonic()
    with pytest.raises(RuntimeError, match="has been closed"):
        verify._rendered("https://example.test/hangs", limit=0.2)
    assert time.monotonic() - began < 5 and forgotten == [True]


def test_the_watchdog_ends_only_this_process_own_browser():
    """The browser is the driver's child and the driver is ours. A stand-in
    tree: a shell (the driver) that starts a sleeper (the browser)."""
    import time

    driver = subprocess.Popen(["/bin/sh", "-c", "sleep 60 & wait"])
    bystander = subprocess.Popen(["sleep", "60"])  # our child, but nobody's driver
    try:
        deadline = time.monotonic() + 5
        while not verify._children(driver.pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        (browser,) = verify._children(driver.pid)
        verify._stop_browser_processes()
        driver.wait(timeout=5)  # its only child is gone, so `wait` returns
        assert verify._children(driver.pid) == []
        assert bystander.poll() is None
        with pytest.raises(ProcessLookupError):
            os.kill(browser, 0)
    finally:
        for process in (driver, bystander):
            process.kill()
            process.wait(timeout=5)


@pytest.fixture
def fees_job(monkeypatch):
    spec = importlib.util.spec_from_file_location("atlas_fees", ROOT / "deploy" / "atlas_fees.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.lines = []
    monkeypatch.setattr(module, "log", module.lines.append)
    monkeypatch.delenv("FEES_SITE_PUBLISH_CMD", raising=False)
    return module


def script(module, monkeypatch, answers):
    """Answer each command the job runs from a list of (return code, stdout)."""
    calls = []

    def run(args, timeout):
        calls.append(args[3])  # the feeverified subcommand
        code, out = answers.pop(0)
        return subprocess.CompletedProcess(args, code, stdout=out, stderr="")

    monkeypatch.setattr(module, "run", run)
    return calls


def test_the_job_reinstalls_a_missing_browser_once_and_checks_again(fees_job, monkeypatch):
    calls = script(fees_job, monkeypatch, [
        (3, '{"error": "browser_unavailable", "detail": "Executable doesn\'t exist"}'),
        (0, "install-browser rc=0 downloaded"),
        (0, '{"verified": 22, "changed": 0, "unreachable": 0, "unreviewed": 0}'),
        (0, "fees_pages=51 platforms=22 out=dist/fees"),
    ])
    fees_job.main()
    assert calls == ["verify", "install-browser", "verify", "build"]
    assert any(line.startswith("verify rc=0 ") and '"verified": 22' in line for line in fees_job.lines)
    assert not any(line.startswith("ERROR") for line in fees_job.lines)


def test_a_browser_that_cannot_be_restored_is_an_error_and_the_pages_still_build(
    fees_job, monkeypatch
):
    calls = script(fees_job, monkeypatch, [
        (3, '{"error": "browser_unavailable", "detail": "no network"}'),
        (1, "install-browser rc=1 download failed"),
        (3, '{"error": "browser_unavailable", "detail": "no network"}'),
        (0, "fees_pages=51 platforms=22 out=dist/fees"),
    ])
    fees_job.main()
    assert calls == ["verify", "install-browser", "verify", "build"]
    errors = [line for line in fees_job.lines if line.startswith("ERROR")]
    assert len(errors) == 1 and "pages keep the last completed check" in errors[0]


def test_a_working_check_is_logged_exactly_as_before(fees_job, monkeypatch):
    calls = script(fees_job, monkeypatch, [
        (0, '{"verified": 21, "changed": 1, "unreachable": 0, "unreviewed": 0}'),
        (0, "fees_pages=51 platforms=22 out=dist/fees"),
    ])
    fees_job.main()
    assert calls == ["verify", "build"]
    assert fees_job.lines[0] == (
        'verify rc=0 {"verified": 21, "changed": 1, "unreachable": 0, "unreviewed": 0}'
    )
