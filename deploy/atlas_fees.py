"""Nightly job for Fee Verified: verify every source page, rebuild, publish.

Order matters: verification runs first so the rebuilt pages carry tonight's
status. Publishing is an owner-configured command, as with the other site;
unset means the build stays local. Nothing here edits a schedule file.

Environment (plist EnvironmentVariables):
  FEES_SITE_BASE_URL      canonical origin (the owner's domain once bought)
  FEES_SITE_PUBLISH_CMD   shell command run after a successful build, cwd = repo root

The publish step prepends the newest nvm node to PATH: launchd's PATH has no
node, and `npx wrangler` should run on the same node the shell uses.
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


def publish_env() -> dict[str, str]:
    """PATH with the newest nvm node first (launchd's PATH has none)."""
    env = dict(os.environ)
    node_dir = Path.home() / ".nvm" / "versions" / "node"
    versions = [p for p in node_dir.glob("v*") if (p / "bin").is_dir()]
    if versions:

        def key(p: Path) -> tuple[int, ...]:
            return tuple(int(x) if x.isdigit() else 0 for x in p.name.lstrip("v").split("."))

        newest = max(versions, key=key)
        env["PATH"] = f"{newest / 'bin'}:{env.get('PATH', '')}"
    return env


def run(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout, check=False)


def main() -> None:
    verify = run([str(PYTHON), "-m", "feeverified", "verify"], 2400)
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
    published = subprocess.run(
        publish_cmd, shell=True, cwd=REPO_ROOT, env=publish_env(),
        capture_output=True, text=True, timeout=600, check=False,
    )
    if published.returncode != 0:
        log(f"ERROR publish failed rc={published.returncode} {published.stderr.strip()[-300:]}")
        return
    log("published")
    indexnow(base_url)


def indexnow(base_url: str) -> None:
    """Best effort: tell Bing which URLs changed. Google ignores IndexNow."""
    import re

    try:
        import httpx

        host = base_url.replace("https://", "").replace("http://", "").strip("/")
        key_file = next(OUT.glob("*.txt"))  # the generator emits exactly one key file besides robots.txt
        key = next(p for p in OUT.glob("*.txt") if p.name != "robots.txt").stem
        urls = re.findall(r"<loc>([^<]+)</loc>", (OUT / "sitemap.xml").read_text())
        response = httpx.post("https://api.indexnow.org/indexnow", json={"host": host, "key": key, "keyLocation": f"https://{host}/{key}.txt", "urlList": urls}, timeout=20)
        log(f"indexnow {len(urls)} urls status={response.status_code} ({key_file.name})")
    except Exception as exc:  # noqa: BLE001 - never fails the publish
        log(f"indexnow skipped: {str(exc)[:120]}")


if __name__ == "__main__":
    main()
