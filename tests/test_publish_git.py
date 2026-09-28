"""The nightly publish must use a git that runs, not the first one on PATH."""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "working_git.sh"


def broken_git(directory: Path) -> Path:
    """A git that exists and is executable but cannot run, the way an Intel-only
    binary fails on an OS without Rosetta."""
    path = directory / "git"
    path.write_text("#!/bin/sh\necho 'Bad CPU type in executable' >&2\nexit 126\n")
    path.chmod(0o755)
    return path


def run(candidates: str, path: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "ATLAS_GIT_CANDIDATES": candidates, "PATH": path}
    return subprocess.run(
        ["/bin/sh", str(SCRIPT)], env=env, capture_output=True, text=True, check=False
    )


def test_a_git_that_cannot_run_is_skipped(tmp_path):
    real = shutil.which("git")
    assert real, "the test machine needs a git"
    broken = broken_git(tmp_path)
    out = run(f"{broken}:{tmp_path / 'missing'}:{real}", os.environ["PATH"])
    assert out.returncode == 0 and out.stdout.strip() == real


def test_the_path_is_the_last_resort(tmp_path):
    real = shutil.which("git")
    out = run(str(tmp_path / "missing"), os.environ["PATH"])
    assert out.returncode == 0 and Path(out.stdout.strip()).name == "git"
    version = subprocess.run([out.stdout.strip(), "--version"], capture_output=True, check=False)
    assert version.returncode == 0 and real


def test_nothing_that_runs_is_a_loud_failure(tmp_path):
    broken = broken_git(tmp_path)
    out = run(str(broken), str(tmp_path))  # PATH holds only the broken one
    assert out.returncode == 1 and out.stdout == ""
    assert "no working git found" in out.stderr


def test_the_publish_script_and_the_agents_cannot_pick_the_intel_copy_first():
    script = (ROOT / "deploy" / "publish_gh_pages.sh").read_text()
    assert 'GIT="$(sh "$REPO_ROOT/deploy/working_git.sh")"' in script
    assert script.index("working_git.sh") < script.index("git fetch")
    for name in ("com.atlas.site.plist", "com.atlas.fees.plist"):
        path = (ROOT / "deploy" / name).read_text().split("<key>PATH</key>")[1]
        value = path.split("<string>")[1].split("</string>")[0].split(":")
        assert value.index("/usr/bin") < value.index("/usr/local/bin"), name
