#!/bin/sh
# Print the first git that actually runs on this machine.
#
# macOS 27 runs no Intel-only binaries, and launchd's PATH found one first:
# /usr/local/bin/git answered "Bad CPU type in executable", so the nightly
# publish failed every night from 2026-09-24 to 2026-09-28 and samebetornot.com
# went a week without an update. A file existing is not enough; a candidate
# counts only if `--version` succeeds.
#
# ATLAS_GIT_CANDIDATES (colon-separated) replaces the default list, for tests.
set -eu
CANDIDATES="${ATLAS_GIT_CANDIDATES:-/usr/bin/git:/opt/homebrew/bin/git:/usr/local/bin/git}"
OLD_IFS="$IFS"
IFS=":"
for candidate in $CANDIDATES; do
  IFS="$OLD_IFS"
  if [ -n "$candidate" ] && "$candidate" --version >/dev/null 2>&1; then
    printf '%s\n' "$candidate"
    exit 0
  fi
done
IFS="$OLD_IFS"
fallback="$(command -v git 2>/dev/null || true)"
if [ -n "$fallback" ] && "$fallback" --version >/dev/null 2>&1; then
  printf '%s\n' "$fallback"
  exit 0
fi
echo "no working git found (tried $CANDIDATES, then PATH)" >&2
exit 1
