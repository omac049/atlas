"""Nightly job for loop 1: pull Search Console data for both sites, then report.

Runs `python -m atlas.gsc pull` then `report`. Until the owner has placed the
service-account key (docs/GSC.md), `pull` prints 'skipped: not configured' and
exits 0, so this agent is safe to install before the key exists.

One line per step goes to ~/Library/Logs/atlas-gsc.log; the full report is
data/gsc/report.md. No step prints key contents.
"""

import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
LOG = Path.home() / "Library" / "Logs" / "atlas-gsc.log"


def log(message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def step(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(PYTHON), "-m", "atlas.gsc", *args], cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=600, check=False)


def main() -> None:
    pulled = step("pull")
    out = (pulled.stdout or "").strip()
    log(f"pull rc={pulled.returncode} {out[-300:] or (pulled.stderr or '').strip()[-300:]}")
    if pulled.returncode != 0 or out.startswith("skipped"):
        return
    reported = step("report")
    log(f"report rc={reported.returncode} {(reported.stdout or reported.stderr).strip()[-400:]}")


if __name__ == "__main__":
    main()
