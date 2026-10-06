"""Atlas liveness watchdog.

launchd's KeepAlive already restarts a process that EXITS. It cannot see a
process that is still alive but has stopped doing its job — a uvicorn worker
wedged on a stuck socket keeps its PID and its listening port while answering
nothing, and a monitor blocked on a venue call stays "running" forever. This
probe closes that gap by checking observable behavior instead of liveness: the
API must answer /health, and the monitor must keep writing to its log.

Run every 60s by com.atlas.healthcheck. Restarts go through
``launchctl kickstart -k`` so launchd stays the single owner of both services;
a bare kill plus a hand start would orphan them from launchd again.

Written in Python and launched through the venv's ``python`` binary rather than
as a shell script: that form is required if this checkout ever sits under
``~/Library/Group Containers`` (where launchd may exec a Mach-O binary but not
read a shell/console script — exit 126/78), and it costs nothing here at
``~/Atlas``. See ``deploy/README.md``.
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

HEALTH_URL = "http://127.0.0.1:8010/health"
REPO_ROOT = Path(__file__).resolve().parent.parent
# Must match com.atlas.monitor's StandardOutPath. Kept in ~/Library/Logs rather
# than the repo's data/ dir so the agents keep working unchanged if the checkout
# ever moves somewhere launchd cannot create files.
MONITOR_LOG = Path.home() / "Library" / "Logs" / "atlas-monitor.log"
WATCHDOG_LOG = Path.home() / "Library" / "Logs" / "atlas-healthcheck.log"
STATE_FILE = (
    Path.home() / "Library" / "Application Support" / "atlas-healthcheck" / "api-failures"
)
MONITOR_KICKSTART_FILE = STATE_FILE.parent / "monitor-last-kickstart"
JOB_STATES_FILE = STATE_FILE.parent / "job-states.json"
BAD_JOB_STATES = ("LATE", "FAILING", "NO_RECORD")
# The message travels as an argument, never spliced into the script text.
NOTIFY_SCRIPT = 'display notification (item 1 of argv) with title "Atlas" subtitle "Scheduled jobs"'

# The monitor sweeps every 300s. 1800s is six missed sweeps: long enough that a
# slow venue catalog or a bounded retry budget can never trip it, short enough
# that a wedged monitor is caught within the half hour.
MONITOR_STALE_SECONDS = 1800

# Two consecutive failures before acting: a single missed probe during a normal
# launchd-managed restart is expected and must not stack a second restart on
# top of one already in progress.
FAILURES_BEFORE_RESTART = 2


def log(message: str) -> None:
    WATCHDOG_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with WATCHDOG_LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def restart(label: str, reason: str) -> None:
    log(f"RESTARTING {label}: {reason}")
    result = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"],
        capture_output=True,
        text=True,
        # A failed kickstart is logged and retried on the next probe, never raised:
        # this watchdog must survive a transient launchd error, not die on it.
        check=False,
    )
    if result.returncode == 0:
        log(f"kickstart issued for {label}")
    else:
        log(f"ERROR kickstart failed for {label}: {result.stderr.strip()}")


def read_failures() -> int:
    try:
        return int(STATE_FILE.read_text().strip())
    except (OSError, ValueError):
        return 0


def write_failures(count: int) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(str(count))


def check_api() -> None:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=10) as response:
            body = response.read().decode("utf-8")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        failures = read_failures() + 1
        write_failures(failures)
        log(f"api health check failed ({failures}/{FAILURES_BEFORE_RESTART}): {exc}")
        if failures >= FAILURES_BEFORE_RESTART:
            restart("com.atlas.api", f"no healthy response from {HEALTH_URL}")
            write_failures(0)
        return

    if '"status":"ok"' not in body:
        failures = read_failures() + 1
        write_failures(failures)
        log(f"api answered without status=ok ({failures}/{FAILURES_BEFORE_RESTART}): {body[:200]}")
        if failures >= FAILURES_BEFORE_RESTART:
            restart("com.atlas.api", "health endpoint did not report status=ok")
            write_failures(0)
        return

    # A 200 carrying trading_enabled=true would mean the paper-only invariant
    # broke; that is a stop-and-report condition, never something to silently
    # restart into.
    if '"trading_enabled":false' not in body:
        log("CRITICAL api does not report trading_enabled=false — NOT restarting, review required")
        return

    write_failures(0)


def read_monitor_kickstart() -> float:
    try:
        return float(MONITOR_KICKSTART_FILE.read_text().strip())
    except (OSError, ValueError):
        return 0.0


def check_monitor() -> None:
    """Restart the monitor when its log stops advancing — measured honestly.

    Silence is measured from the LATER of the log's last write and our own last
    kickstart. Without the second term this loops fatally: after the Mac sleeps
    past the staleness limit, the log mtime is hours old, the watchdog restarts
    the monitor — and 60s later the mtime is unchanged (a fresh monitor needs
    minutes of venue sweeps before its first, block-buffered write), so the
    watchdog kills it again, forever. Observed live 2026-08-25: 170 consecutive
    kickstarts at 60s intervals after an 18-hour lid-closed gap, zero completed
    cycles. A kickstart therefore restarts the staleness clock; a monitor that
    is genuinely wedged still dies, one grace window later.
    """
    if not MONITOR_LOG.exists():
        log(f"monitor log missing at {MONITOR_LOG}")
        return
    last_signal = max(MONITOR_LOG.stat().st_mtime, read_monitor_kickstart())
    age = int(time.time() - last_signal)
    if age > MONITOR_STALE_SECONDS:
        restart("com.atlas.monitor", f"log silent for {age}s (limit {MONITOR_STALE_SECONDS}s)")
        MONITOR_KICKSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
        MONITOR_KICKSTART_FILE.write_text(str(time.time()))


def notify(message: str) -> None:
    """Post a macOS notification. Failing to show one is logged, never raised."""
    try:
        result = subprocess.run(
            [
                "osascript",
                "-e", "on run argv",
                "-e", NOTIFY_SCRIPT,
                "-e", "end run",
                message,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"ERROR notification failed: {exc}")
        return
    if result.returncode != 0:
        log(f"ERROR notification failed: {result.stderr.strip()[:200]}")


def read_job_states() -> dict:
    try:
        return json.loads(JOB_STATES_FILE.read_text())
    except (OSError, ValueError):
        return {}


def check_jobs() -> None:
    """Notify once when a scheduled job turns LATE, FAILING or NO_RECORD.

    The dashboard's jobs panel only helps someone who opens it; on 2026-10-06
    the dashboard was down with everything else. Every state change is logged
    here; only a change INTO a bad state is notified, one notification per run
    naming every job that changed. Runs last, so nothing here can stop the API
    and monitor checks above.
    """
    try:
        import atlas.job_health

        jobs = atlas.job_health.all_job_health()
    except (ImportError, OSError, ValueError) as exc:
        log(f"ERROR job health unavailable: {exc}")
        return
    previous = read_job_states()
    current = {job["name"]: job["state"] for job in jobs}
    newly_bad = []
    for name, state in current.items():
        before = previous.get(name, "OK")
        if state == before:
            continue
        log(f"job {name}: {before} -> {state}")
        if state in BAD_JOB_STATES:
            newly_bad.append(f"{name} {state.replace('_', ' ')}")
    if newly_bad:
        notify(f"{', '.join(newly_bad)}. Open the dashboard's Jobs panel.")
    JOB_STATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    JOB_STATES_FILE.write_text(json.dumps(current, sort_keys=True))


if __name__ == "__main__":
    check_api()
    check_monitor()
    check_jobs()
