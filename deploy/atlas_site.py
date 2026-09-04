"""Nightly regeneration of the demand-test site (docs/SITE.md).

Runs `atlas site build --live` into dist/site, then — only if the owner has
configured one — runs a publish command. Nothing here knows which host the
owner chose; the publish step is an opaque shell command supplied through
the launchd plist's EnvironmentVariables, so this script never touches a
hosting credential and never chooses where the site goes.

Environment (all optional; unset means "build locally, publish nothing"):
  ATLAS_SITE_BASE_URL     canonical origin for sitemap/links
  ATLAS_SITE_PUBLISH_CMD  shell command run after a successful build,
                          with the cwd at the repo root, e.g.
                          "npx wrangler pages deploy dist/site --project-name x"

Run daily by com.atlas.site at 04:00, after the 03:30 backup.
"""

import os
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
OUT = REPO_ROOT / "dist" / "site"
LOG = Path.home() / "Library" / "Logs" / "atlas-site.log"
BUILD_TIMEOUT_SECONDS = 900
PUBLISH_TIMEOUT_SECONDS = 600


def log(message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def main() -> None:
    base_url = os.environ.get("ATLAS_SITE_BASE_URL", "https://example.invalid")
    build = subprocess.run(
        [
            str(PYTHON), "-m", "atlas.cli", "site", "build",
            "--out", str(OUT), "--live", "--base-url", base_url,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT_SECONDS,
        check=False,
    )
    tail = (build.stdout.strip().splitlines() or [""])[-1]
    if build.returncode != 0:
        log(f"ERROR build failed rc={build.returncode} {build.stderr.strip()[-300:]}")
        return
    log(f"built {tail}")

    publish_cmd = os.environ.get("ATLAS_SITE_PUBLISH_CMD", "").strip()
    if not publish_cmd:
        log("publish skipped: ATLAS_SITE_PUBLISH_CMD unset (build is local only)")
        return
    published = subprocess.run(
        publish_cmd,
        shell=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=PUBLISH_TIMEOUT_SECONDS,
        check=False,
    )
    if published.returncode != 0:
        log(f"ERROR publish failed rc={published.returncode} {published.stderr.strip()[-300:]}")
        return
    log("published")


if __name__ == "__main__":
    main()
