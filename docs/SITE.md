# The demand-test site

The static site pre-registered in `docs/IDEATION.md` (survivor G). Generated
by `atlas/site.py` from Atlas's own data; nothing on it is written by hand.

## What it is

- One page per cross-venue twin pair: "same bet or not?" verdict straight from
  the deterministic verifier, the venue-published reasons when not, exact
  taker fees at the last observed price, capital lock-up, fine-print grades,
  and both venues' rules text quoted.
- Five pillars whose every claim carries a source and a date: fee calculator
  (published formulas), legal status tracker, taxes, "same bet?" explainer,
  referral terms compared (credits vs cash).
- Every page carries the affiliate disclosure and the not-advice notice. The
  build fails if either is missing (`atlas.site.verify_pages`).

## Build

```bash
.venv/bin/python -m atlas.cli site build --out dist/site --live --base-url https://YOUR-DOMAIN
```

`--live` hydrates rules text and grades from the two US venues (≈90 requests,
capped at 120; a failure degrades to fewer excerpts, never a stale verdict).
Without `--live` the build is offline and uses only stored data. Output is
plain HTML under `dist/site/` (gitignored) — host it anywhere that serves
static files. Preview locally with the `atlas-site` entry in
`.claude/launch.json` (port 8766).

## Owner steps (not automatable by the assistant)

Step-by-step, with commands, email drafts, and post drafts: `docs/LAUNCH_KIT.md`.

1. Register the domain; pass it as `--base-url` so canonicals and the sitemap
   are right.
2. Host: GitHub Pages from the `gh-pages` branch (`deploy/publish_gh_pages.sh`).
3. Add the property in Search Console and submit `/sitemap.xml`.
4. Apply to the affiliate programs listed on `referrals.html`; add links only
   once approved. The disclosure is already on every page.
5. Nightly regeneration: a `com.atlas.site` launchd agent running the build
   command above, once hosting exists to publish into.

## The test this site exists to run

From `docs/IDEATION.md`, fixed before the build: ≤2 weeks, ≤$200, no paid
traffic. Pass at week 6 only if ≥2,000 Search Console impressions over 30
days with a top-20 page for any cluster term, OR ≥300 social clicks — AND at
least one affiliate program approval. Fail closes this line; the site stays
up as a public record either way.
