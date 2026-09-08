"""Nightly job for Fee Verified: verify every source page, rebuild, publish.

Order matters: verification runs first so the rebuilt pages carry tonight's
status. Publishing is an owner-configured command, as with the other site;
unset means the build stays local. Nothing here edits a schedule file.

Environment (plist EnvironmentVariables):
  FEES_SITE_BASE_URL      canonical origin (the owner's domain once bought)
  FEES_SITE_PUBLISH_CMD   shell command run after a successful build, cwd = repo root
"""

import os
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
OUT = REPO_ROOT / "dist" / "fees"
LOG = Path.home() / "Library" / "Logs" / "atlas-fees.log"


def log(message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def run(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout, check=False)


def main() -> None:
    verify = run([str(PYTHON), "-m", "feeverified", "verify"], 600)
    log(f"verify rc={verify.returncode} {verify.stdout.strip()[-200:] or verify.stderr.strip()[-200:]}")
    base_url = os.environ.get("FEES_SITE_BASE_URL", "https://example.invalid")
    build = run([str(PYTHON), "-m", "feeverified", "build", "--out", str(OUT), "--base-url", base_url], 300)
    if build.returncode != 0:
        log(f"ERROR build failed rc={build.returncode} {build.stderr.strip()[-300:]}")
        return
    log(f"built {build.stdout.strip().splitlines()[-1] if build.stdout.strip() else ''}")
    publish_cmd = os.environ.get("FEES_SITE_PUBLISH_CMD", "").strip()
    if not publish_cmd:
        log("publish skipped: FEES_SITE_PUBLISH_CMD unset (build is local only)")
        return
    published = subprocess.run(publish_cmd, shell=True, cwd=REPO_ROOT, capture_output=True, text=True, timeout=600, check=False)
    if published.returncode != 0:
        log(f"ERROR publish failed rc={published.returncode} {published.stderr.strip()[-300:]}")
        return
    log("published")


if __name__ == "__main__":
    main()
