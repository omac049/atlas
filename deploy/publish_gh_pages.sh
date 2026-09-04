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
KEY_FILE="$(ls ./*.txt 2>/dev/null | grep -v robots | head -1 || true)"
if [ -n "$KEY_FILE" ] && [ -f sitemap.xml ] && command -v python3 >/dev/null 2>&1; then
  KEY="$(basename "$KEY_FILE" .txt)"
  python3 - "$DOMAIN" "$KEY" <<'PY' || echo "indexnow submit skipped"
import json, re, sys, urllib.request
host, key = sys.argv[1], sys.argv[2]
urls = re.findall(r"<loc>([^<]+)</loc>", open("sitemap.xml", encoding="utf-8").read())[:10000]
body = json.dumps({"host": host, "key": key, "keyLocation": f"https://{host}/{key}.txt",
                   "urlList": urls}).encode()
req = urllib.request.Request("https://api.indexnow.org/indexnow", data=body,
                             headers={"Content-Type": "application/json; charset=utf-8"})
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        print(f"indexnow submitted {len(urls)} urls status={r.status}")
except Exception as exc:  # noqa: BLE001 - best effort, never fails the publish
    print(f"indexnow submit failed: {exc}")
PY
fi
