"""Hold the Mac awake through scheduled macro-release burst windows.

The gap radar's whole reason to exist is the minutes around a data print, and
the jobs/CPI prints land at 05:30 local — an hour this laptop is normally
asleep. A user-level agent cannot WAKE a sleeping Mac (that requires a root
``pmset repeat`` schedule) and cannot override lid-close sleep; what it CAN do
is stop an awake machine from drifting off. So this agent latches a
``caffeinate`` sleep-prevention assertion the evening before each release
(``KEEP_AWAKE_BEFORE`` in ``atlas/release_calendar.py``) and holds it through
the end of the burst window.

Operating requirements, stated plainly rather than assumed:

- the lid must be OPEN (or an external display attached) — caffeinate cannot
  stop lid-close sleep;
- AC power is strongly recommended: ``-s`` only holds on AC, and while ``-i``
  still blocks idle sleep on battery, a battery running dry ends everything;
- if the machine is already asleep when the hold would begin, nothing here can
  recover the window — that failure mode needs the root alternative, a
  one-time ``sudo pmset repeat wakeorpoweron`` documented in deploy/README.md.

Between releases the agent sleeps in bounded slices, so an edited calendar is
picked up within an hour rather than after a stale multi-day sleep.
"""

import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from atlas.release_calendar import (
    keep_awake_window,
    next_keep_awake_start,
)

LOG = Path.home() / "Library" / "Logs" / "atlas-awake.log"
IDLE_CHECK_SECONDS = 3600  # upper bound between calendar checks


def log(message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def hold_awake(release: str, seconds: int) -> None:
    """Block inside one caffeinate assertion until the window ends.

    ``-s`` prevents system sleep on AC power; ``-i`` prevents idle sleep and
    also works on battery. caffeinate exits when ``-t`` expires, releasing the
    assertion — the machine may sleep again the moment the window closes.
    """
    log(f"hold start: release={release} seconds={seconds}")
    result = subprocess.run(
        ["/usr/bin/caffeinate", "-si", "-t", str(seconds)], check=False
    )
    log(f"hold end: release={release} exit={result.returncode}")


def main() -> None:
    log("atlas-awake started; holds are read from atlas/release_calendar.py")
    while True:
        now = datetime.now(UTC)
        active = keep_awake_window(now)
        if active is not None:
            hold_awake(*active)
            continue
        upcoming = next_keep_awake_start(now)
        if upcoming is None:
            # Calendar exhausted — someone must add next quarter's releases.
            log("no future releases in the calendar; sleeping one hour")
            time.sleep(IDLE_CHECK_SECONDS)
            continue
        wait = min(IDLE_CHECK_SECONDS, max(1, (upcoming - now).total_seconds()))
        time.sleep(wait)


if __name__ == "__main__":
    main()
