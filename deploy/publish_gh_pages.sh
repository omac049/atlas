#!/bin/sh
# Publish dist/site to the gh-pages branch of this repo (GitHub Pages).
# Idempotent: commits only when the built site changed. Uses a throwaway
# worktree so the main checkout is never touched. Run from the repo root by
# com.atlas.site after a successful build; safe to run by hand.
set -eu
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SITE="$REPO_ROOT/dist/site"
DOMAIN="${ATLAS_SITE_DOMAIN:-samebetornot.com}"
WT="$(mktemp -d /tmp/atlas-gh-pages.XXXXXX)"
cleanup() { cd "$REPO_ROOT" && git worktree remove --force "$WT" >/dev/null 2>&1 || true; rm -rf "$WT"; }
trap cleanup EXIT

[ -f "$SITE/index.html" ] || { echo "no built site at $SITE" >&2; exit 1; }
cd "$REPO_ROOT"
git fetch -q origin gh-pages 2>/dev/null || true
if git show-ref -q --verify refs/remotes/origin/gh-pages; then
  git worktree add -q "$WT" origin/gh-pages --detach
else
  git worktree add -q --detach "$WT"
  (cd "$WT" && git checkout -q --orphan gh-pages && git rm -rfq . >/dev/null 2>&1 || true)
fi
cd "$WT"
git checkout -q -B gh-pages
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -R "$SITE"/. .
printf '%s\n' "$DOMAIN" > CNAME
: > .nojekyll
git add -A
if git diff --cached --quiet; then
  echo "gh-pages unchanged"
  exit 0
fi
git -c user.name="atlas-site" -c user.email="atlas-site@users.noreply.github.com" \
  commit -q -m "site: $(date -u +%Y-%m-%dT%H:%MZ)"
git push -q origin gh-pages:gh-pages
echo "gh-pages published $(git rev-parse --short HEAD)"

# IndexNow: tell Bing (and engines sharing the protocol) which URLs changed.
# The key is public by design and served at /{key}.txt; Google ignores
# IndexNow, so this never substitutes for Search Console. Best effort only.
# Uses the repo's venv python: the system python3 on this Mac has no CA
# bundle and fails TLS verification; httpx ships certifi.
PY="$REPO_ROOT/.venv/bin/python"
KEY_FILE="$(ls ./*.txt 2>/dev/null | grep -v robots | head -1 || true)"
if [ -n "$KEY_FILE" ] && [ -f sitemap.xml ] && [ -x "$PY" ]; then
  KEY="$(basename "$KEY_FILE" .txt)"
  "$PY" - "$DOMAIN" "$KEY" <<'PYEOF2' || echo "indexnow submit skipped"
import re, sys
import httpx
host, key = sys.argv[1], sys.argv[2]
urls = re.findall(r"<loc>([^<]+)</loc>", open("sitemap.xml", encoding="utf-8").read())[:10000]
body = {"host": host, "key": key, "keyLocation": f"https://{host}/{key}.txt", "urlList": urls}
try:
    r = httpx.post("https://api.indexnow.org/indexnow", json=body, timeout=20)
    print(f"indexnow submitted {len(urls)} urls status={r.status_code}")
except httpx.HTTPError as exc:
    print(f"indexnow submit failed: {exc}")
PYEOF2
fi
