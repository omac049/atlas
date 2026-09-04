# Launch kit — the owner's steps, in order

Everything the assistant cannot do because it involves accounts, payments,
or posting from a personal handle. Each step is short; the whole list is an
evening. The six-week demand clock (`docs/IDEATION.md`) starts at step 4.

## 1. Domain — DONE 2026-09-04: `samebetornot.com`

Chosen over the venue-name domains because it contains neither trademark.
DNS is handled by the host in step 2.

## 2. Host — Cloudflare Pages (free)

Wrangler on this Mac is already logged in to the owner's Cloudflare account
(checked 2026-09-04 with `npx wrangler whoami`), so no login step is needed.

```bash
npx wrangler pages project create atlas-site --production-branch main
```

```bash
npx wrangler pages deploy dist/site --project-name atlas-site
```

Then in the Cloudflare dashboard: Pages → atlas-site → Custom domains → add
the domain from step 1 (it walks you through pointing DNS at Cloudflare).

Now fill in the nightly agent so it publishes on its own. Edit
`deploy/com.atlas.site.plist`:

- `ATLAS_SITE_BASE_URL` → `https://samebetornot.com`
- `ATLAS_SITE_PUBLISH_CMD` → `npx wrangler pages deploy dist/site --project-name atlas-site`
- `ATLAS_SITE_GA4_ID` → the GA4 measurement id from step 3 (optional)

Then reinstall it:

```bash
cp deploy/com.atlas.site.plist ~/Library/LaunchAgents/ && launchctl bootout gui/$(id -u)/com.atlas.site; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.atlas.site.plist && launchctl kickstart gui/$(id -u)/com.atlas.site
```

Check `~/Library/Logs/atlas-site.log` for `built … ` followed by `published`.

## 3. Measurement

- **Search Console** (required — it is the pass/fail instrument): add a
  *Domain* property for samebetornot.com, verify by the DNS TXT record (Cloudflare
  DNS → add record), then Sitemaps → submit `https://samebetornot.com/sitemap.xml`.
- **GA4** (optional — needed only to count the ≥300-social-clicks criterion):
  create a property, copy the `G-…` id into the plist above. The tag is
  omitted entirely when the id is unset, and anonymizes IPs when set.

## 4. Start the clock

The day the sitemap is accepted, write the date at the top of
`docs/IDEATION.md` under "Build status". Week-6 verdict date = that + 42 days.

## 5. Affiliate applications (parallel; approval is a pass criterion)

Send these yourself; the site already discloses on every page.

**Polymarket US — email to affiliate@polymarket.com**

> Subject: Affiliate program application — samebetornot.com
>
> I run samebetornot.com, a factual comparison site for Kalshi and Polymarket:
> contract-by-contract rule comparisons generated from both venues' published
> terms, an exact fee calculator using your published fee schedule, and sourced
> legal/tax reference pages. No picks, no advice, affiliate relationships
> disclosed on every page. I'd like to apply for the Polymarket US affiliate
> program. Site: https://samebetornot.com. Thanks — YOUR NAME

**Polymarket (global) partner program** — apply at partners.dub.co/polymarket
with the same description. Note: this program pays on the global platform,
which US users cannot use; keep any such link clearly labeled as non-US.

**Kalshi** — no cash affiliate program was reachable (the affiliate hub
returned 403 on 2026-09-04). The refer-a-friend link from your own account
pays trading credits, not cash; the referrals page already says so.

## 6. Social posts (the second traffic route)

Post from your own handles with UTM tags so GA4 attributes the clicks, e.g.
`?utm_source=x&utm_medium=social&utm_campaign=launch`. Check each community's
self-promotion rules before posting; a factual, link-light post that answers
a question is usually fine where a "check out my site" post is not.

**X / Threads (thread, 3 posts)**

1. Cardi B danced in someone else's Super Bowl set. Polymarket paid YES at
   $1. Kalshi settled the "same" contract at 26¢. Same event, two rulebooks,
   opposite money. I built a site that checks this contract by contract.
2. It reads both venues' published rules, extracts the terms, and says
   "same bet" only when every field matches. Right now: 61 pairs tracked,
   4 verified identical. The rest differ in ways that can settle differently.
3. Also on it: an exact fee calculator (both venues' real formulas, not the
   "7% vs 6%" headline), a sourced legal tracker, and what tax forms each
   venue actually sends. No picks, no advice. LINK

**Reddit (r/Kalshi, r/Polymarket — read the rules first; answer, don't pitch)**

> Title: Kalshi vs Polymarket fees at any price — the actual formulas
>
> Both venues' fees are a curve peaking at 50¢, not a flat rate: Kalshi is
> 0.07·p·(1−p) per contract (rounded up per contract), Polymarket US is
> 0.06·p·(1−p) per share, effective July 1, 2026. At 50¢ that's 1.75¢ (rounds
> to 2¢) vs 1.5¢; at 90¢ it's 0.63¢ vs 0.54¢. I put a calculator up with
> sources linked: LINK. Corrections welcome — it reads straight from the
> published schedules.

**LinkedIn (your own voice — the method is the story)**

> I spent six weeks testing four ideas for making money in prediction
> markets, with thresholds fixed before seeing data. All four failed, and the
> failures are public. What survived is the instrument: a deterministic
> checker that tells you whether two "identical" contracts on Kalshi and
> Polymarket actually settle the same way. It usually says no. I turned it
> into a site. LINK

## 7. Weekly check (5 minutes, Mondays)

Search Console → Performance → last 28 days: impressions, top queries, top
pages. GA4 → Acquisition → sessions by source/medium. Paste the four numbers
into "Build status" in `docs/IDEATION.md`. Week 6 decides.
