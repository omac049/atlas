# Fee Verified — runbook

The second demand test (`docs/decisions/2026-09-08-fee-calculator-demand-test.md`).
Seller-fee calculators computed from each platform's own published schedule,
pinned to the platform's worked examples, verified nightly against the page.

## Where things live

- `docs/fees/<platform>.json` — the schedule: every number, the sentences on the
  platform's page that state it, the source URL and the date it was read, the
  platform's own examples, the calculator's input fields, exclusions, notes.
  **Numbers change only by a human editing this file.**
- `feeverified/static/fees.js` — the one fee engine (browser and Node). One
  function per platform, reading rates from the schedule.
- `feeverified/verify.py` — nightly check. Fingerprints the fee-bearing
  sentences of each source page (whole-page hashes churn on session ids) and
  records verified / changed / unreachable in `docs/fees/verification.json`.
- `feeverified/site.py` — page generator: calculator page, "how much does X
  take", comparisons, methodology, about. Disclosure on every page by guardrail.
- `tests/test_feeverified.py` — every published example reproduced to the cent,
  every schedule cites a loaded source and quotes its numbers.

## Commands

```bash
.venv/bin/python -m pytest -q tests/test_feeverified.py
```

```bash
.venv/bin/python -m feeverified verify
```

```bash
.venv/bin/python -m feeverified build --out dist/fees --base-url https://YOUR-DOMAIN
```

```bash
.venv/bin/python -m feeverified reviewed ebay --note "re-read the fees page; final value fee unchanged"
```

Preview locally with the `fees-site` entry in `.claude/launch.json` (port 8767).

## The review loop (the whole point)

1. Nightly, `verify` fetches each source page. If its fee sentences differ from
   the baseline recorded at the last human review, the platform's pages show
   **under review** until step 2. If the page cannot be fetched, they show
   **could not check** with the last verified date. Nothing is assumed.
2. A human re-reads the page, edits `docs/fees/<platform>.json` if a number
   changed, runs the tests (the platform's examples must still reproduce), then
   runs `reviewed <platform>`, which records a new baseline and a history entry.
3. The next build shows **verified** with both dates.

## Adding a platform

1. Load the platform's official fee page(s). Write `docs/fees/<slug>.json` with
   `rates`, `quotes` (exact sentences), `sources` (URL, title, loaded date, the
   page's own "last updated" if shown), `examples` (the platform's own worked
   examples; if none, derived arithmetic marked `"derived": true`), `inputs`,
   `structure`, `summary`, `excluded`, `notes`.
2. Add the engine function in `fees.js` and register it in `engines`.
3. Run the tests; run `reviewed <slug>`; build.

## Owner steps (not automatable)

1. Buy the domain (`feeverified.com`, `verifiedfees.com`, `takerates.com`
   were free on 2026-09-08).
2. Hosting: a Cloudflare Pages project (wrangler is logged in on this Mac)
   or a second GitHub Pages repository; then set `FEES_SITE_BASE_URL` and
   `FEES_SITE_PUBLISH_CMD` in `deploy/com.atlas.fees.plist` and install it
   (`cp` + `launchctl bootstrap`, as in `deploy/README.md`).
3. Search Console in the personal Google account; submit `/sitemap.xml`. That
   day the six-week clock starts; log it in the charter.
4. Affiliate applications: Shopify (Impact), QuickBooks (CJ), Wise
   (Partnerize), Printful, Printify, Square (Impact), Vendoo (Awin). Etsy's
   program bars price-comparison sites and is not used.
