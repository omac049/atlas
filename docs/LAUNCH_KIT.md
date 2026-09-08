# Launch kit — the owner's steps, in order

Everything the assistant cannot do because it involves accounts, payments,
or posting from a personal handle. Each step is short; the whole list is an
evening. The six-week demand clock (`docs/IDEATION.md`) starts at step 4.

## 1. Domain — DONE 2026-09-04: `samebetornot.com`

Chosen over the venue-name domains because it contains neither trademark.
DNS is handled by the host in step 2.

## 2. Host — GitHub Pages (chosen 2026-09-04; the repo is public, so it's free)

The site lives on the `gh-pages` branch of `omac049/atlas`, built site only,
with a `CNAME` file naming the domain. `deploy/publish_gh_pages.sh` pushes a
fresh build there and commits only when something changed; the nightly agent
runs it after each build (`ATLAS_SITE_PUBLISH_CMD` in
`deploy/com.atlas.site.plist`). Pages was enabled through the GitHub API with
source `gh-pages` / root.

**DNS at Cloudflare (owner, one time).** DNS → Records, add five records, all
with the proxy OFF (grey cloud) so GitHub can issue the HTTPS certificate:

| Type | Name | Content |
|---|---|---|
| A | @ | 185.199.108.153 |
| A | @ | 185.199.109.153 |
| A | @ | 185.199.110.153 |
| A | @ | 185.199.111.153 |
| CNAME | www | omac049.github.io |

Then GitHub → repo Settings → Pages → Custom domain shows `samebetornot.com`
(set by the CNAME file); once the DNS check passes, tick "Enforce HTTPS".

The earlier Cloudflare Pages project (`samebetornot`, preview only) can be
deleted from the Cloudflare dashboard; nothing depends on it.

## 3. Measurement

- **Search Console** (required — it is the pass/fail instrument). Use your
  **personal** Google account, not the work one the MCP connector is on. Add a
  *Domain* property for samebetornot.com, verify by the DNS TXT record (Cloudflare
  DNS → add record), then Sitemaps → submit `https://samebetornot.com/sitemap.xml`.
- **GA4** (optional — needed only to count the ≥300-social-clicks criterion):
  create a property, copy the `G-…` id into the plist above. The tag is
  omitted entirely when the id is unset, and anonymizes IPs when set.

## 4. Start the clock — DONE 2026-09-04 (sitemap accepted, 68 URLs; verdict 2026-10-16)

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

**Polymarket (global) programs — decision 2026-09-08: not used on the site.**
The owner obtained a self-serve global referral code (`polymarket.com/?r=…`,
10% / 5% of referred fees). It is not published: the site's readers are US
searchers, the global venue bars US persons under its own terms and the 2022
CFTC order, the code pays nothing until the referrer has $10,000 of own
lifetime volume, and a self-serve code is not an "affiliate approval" under
the demand test. The Dub partner program is the same venue and is likewise
not used. The program that counts is Polymarket US's, by application.

**Kalshi** — no affiliate program exists (kalshi.com/affiliates is a 404;
the big "promo code" sites hold private deals). The owner's own refer-a-friend
code (trading credits) may go on the Kalshi referral page, labeled as credits.

**Kalshi links, 2026-09-08.** The owner's refer-a-friend link
(`kalshi.com/r/<id>`) is live on the Kalshi referral page as a sponsored link,
with the in-app terms shown that day ($25 each after the new user trades $25;
bonus funds expire after 7 days) labeled as the owner's own view, since Kalshi
says amounts vary by account. The owner also holds a **Kalshi perpetual-futures
invite** (both get $25 after the friend trades $50 in perps; friend gets 10% off
fees for 3 months; referrer earns 30% of their fees for a year, up to $1,000 per
referral). **Held, not used:** perps are a leveraged crypto product outside the
site's subject, and every perps search term measured 0/month (Keywords
Everywhere, US, 2026-09-08). Revisit only if a perps page is ever justified.

**Status 2026-09-08.** The owner applied to the Dub Polymarket program
(partners.dub.co/programs/polymarket), awaiting review. Its payout is tied to
"Perps trading fees", which only polymarket.com offers, so it is almost
certainly the global venue's program: when approved, check the landing URL in
the Dub dashboard — polymarket.us means usable, polymarket.com means not used
on this site (see the decision above). The Polymarket US email still needs
sending.

**Other programs to apply to, in order (all pay cash, all relevant to what the
site publishes; disclosed on every page, never affecting a verdict):**

| Program | Why relevant | Where | Terms found 2026-09-08 |
|---|---|---|---|
| Polymarket US affiliate | The site's subject | affiliate@polymarket.com | Not published; by approval |
| Coinbase Affiliate Program | Coinbase offers Kalshi's prediction markets to US residents | coinbase.com/affiliates (Impact) | 50% of referred users' trading fees for 3 months; PayPal/bank; $10 threshold |
| Robinhood Affiliate Program | Robinhood carries Kalshi event contracts | affiliates.robinhood.com | Publisher application; performance-based |
| CoinLedger affiliate | Taxes page already reaching "kalshi taxes" queries | coinledger.io/affiliate-program | 25% per report; PayPal; $30 threshold |
| Koinly affiliate | Same | koinly.io (affiliate portal in account) | Up to 40% + recurring |
| Webull affiliate | Webull offers prediction markets | act.webullapp.com/mktb/partners/individual | $20–$70 per funded account; requires a Webull account |

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
