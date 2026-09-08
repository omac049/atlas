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
- `feeverified/verify.py` — nightly check. Renders each source page in headless
  Chromium (Playwright; several platforms serve fee schedules only through
  JavaScript), falls back to a browser-impersonating fetch, then checks that
  every sentence the schedule quotes is still on the page. A missing quote
  means **changed**; a fingerprint of all fee-bearing sentences is kept as
  evidence. Results in `docs/fees/verification.json`. A full run takes
  ~10–20 minutes because pages are rendered, not just fetched.
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

## Hosting (live 2026-09-08)

- Domain: `verifiedfees.com`, DNS on Cloudflare (owner's account).
- Cloudflare Pages project `verifiedfees`, production branch `main`, production
  alias `verifiedfees.pages.dev`. Created with
  `npx wrangler pages project create verifiedfees --production-branch main --force`
  (wrangler 4.130 otherwise delegates Pages to Workers and fails on static
  sites; `--force` is only needed at creation). Deploys:
  `npx wrangler pages deploy dist/fees --project-name verifiedfees --branch main --commit-dirty=true`.
- Custom domain: added in the Cloudflare dashboard (Pages → verifiedfees →
  Custom domains → verifiedfees.com and www); with DNS on Cloudflare the
  records are created automatically. Wrangler has no command for this.
- Nightly: `com.atlas.fees` at 04:20 runs verify → build → publish → IndexNow;
  log at `~/Library/Logs/atlas-fees.log`. The baseline in
  `docs/fees/verification.json` is committed; nightly results live in
  `data/fees/check.json` (ignored) and the site merges them.

## Owner steps (not automatable)

1. Custom domain in the Cloudflare dashboard (above), once.
2. Search Console for `verifiedfees.com` in the personal Google account; submit
   `/sitemap.xml`. That day the six-week clock starts; log it in the charter.
3. Affiliate applications: Shopify (Impact), QuickBooks (CJ), Wise
   (Partnerize), Printful, Printify, Square (Impact), Vendoo (Awin). Etsy's
   program bars price-comparison sites and is not used.
