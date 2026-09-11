# Loop 1: Search Console, pulled nightly

The two sites measure themselves. Every night at 06:15 the launchd agent
`com.atlas.gsc` pulls Google Search Console data for samebetornot.com and
verifiedfees.com and writes:

- `data/gsc/<site>/<date>.json`: that day's totals, plus every page-and-query
  row Search Console reports. Gitignored.
- `data/gsc/report.md` and `data/gsc/report.json`: last 7 and 30 days, top
  queries, queries in striking distance (positions 8 to 30, the input to loop
  2), each charter's pass-line numbers, and a ready-to-paste scoreboard row.

Each run re-pulls the trailing 35 days, because Search Console revises recent
days. Its data lags two to three days.

It reads Search Console through a service account that belongs to the owner's
**personal** Google account. The Search Console connector available to Claude
in this workspace belongs to an employer account and is never used for these
sites.

## One-time owner setup (about 10 minutes)

1. Open <https://console.cloud.google.com>, signed in as the personal Google
   account that owns both Search Console properties.
2. Create a project, for example `atlas-gsc`.
3. **APIs & Services → Library**, search "Google Search Console API", click
   **Enable**.
4. **IAM & Admin → Service Accounts → Create service account**. Name it, for
   example `atlas-gsc-reader`. Skip the optional role and user steps. Click
   **Done**.
5. Open the new service account, go to **Keys → Add key → Create new key →
   JSON**. A file downloads.
6. Move the file into place and make it readable only by you:

   ```bash
   mkdir -p ~/.config/atlas && mv ~/Downloads/atlas-gsc-*.json ~/.config/atlas/gsc-service-account.json && chmod 600 ~/.config/atlas/gsc-service-account.json
   ```

7. Copy the service account's email address from its page. It ends in
   `iam.gserviceaccount.com`.
8. In Search Console, for **each** property (samebetornot.com and
   verifiedfees.com): **Settings → Users and permissions → Add user**, paste the
   email, choose **Restricted**, click **Add**.

Then check it:

```bash
.venv/bin/python -m atlas.gsc status
```

## Commands

```bash
.venv/bin/python -m atlas.gsc status   # key present, file mode, days of data per site
.venv/bin/python -m atlas.gsc pull     # pull the trailing 35 days now
.venv/bin/python -m atlas.gsc report   # write data/gsc/report.md and report.json
.venv/bin/python -m atlas.gsc sitemaps sc-domain:verifiedfees.com        # what Google holds
.venv/bin/python -m atlas.gsc sitemap-submit sc-domain:verifiedfees.com  # ask for a re-fetch
```

The nightly log is `~/Library/Logs/atlas-gsc.log`. Until step 6 is done, each
run logs `skipped: not configured` and exits cleanly.

## Reads and writes

The nightly job only reads: its token asks for `webmasters.readonly`. A write
has to ask for the `webmasters` scope on purpose, and no scheduled job does.

The only write implemented is sitemap resubmission, which tells Google to
re-fetch a sitemap it already has. That needs the service account to be a Full
user or an Owner on the property; a Restricted user gets HTTP 403.

Requesting indexing for a page is not available through any API. Google's
Indexing API accepts only job postings and livestream pages, so that stays a
manual step in Search Console: paste the address into the inspection box at the
top, then click Request indexing.

## How the report maps to the charters

- **samebetornot.com** (docs/IDEATION.md): at least 2,000 impressions in the
  trailing 30 days and a top-20 page for a cluster term. The report's term
  filter is "queries naming Kalshi or Polymarket".
- **verifiedfees.com** (docs/decisions/2026-09-08-fee-calculator-demand-test.md):
  at least 2,000 impressions in the trailing 30 days and a top-20 page for a
  head term. The report's filter is "fee queries naming a covered platform",
  with platform names taken from `docs/fees/*.json`.

The report gives the numbers. The verdict is still written by a person, who
applies each charter's exclusions, such as brand-only queries and terms under
500 searches a month.

## Guardrails

- Read-only scope (`webmasters.readonly`), and the account is only a
  Restricted user on each property.
- The key file lives outside the repository, mode 600. `atlas/gsc.py` reads it
  only to sign one token request per run. Nothing prints its contents: errors
  name the path, and `status` shows only presence and file mode.
- Nothing here edits a page, a charter or a pass line. The report proposes;
  people decide.
