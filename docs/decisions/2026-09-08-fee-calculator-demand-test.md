# The fee-calculator demand test (second product test)

**Status: SIGNED — merged to `main` by the owner on 2026-09-08 (#37).** Merging
this file constituted sign-off on the scope, the budget, and the pass/fail line below.
Nothing about the pass line may change after the site goes live.

**Lineage.** Ideation round two (`docs/IDEATION-2.md`, merged 2026-09-08)
fixed screening criteria before pulling data and found one space that passed
all of them: calculators for the fees platforms charge sellers — eBay, PayPal,
Etsy, Square, Stripe, Shopify, Venmo, Cash App, GoFundMe, Depop, Poshmark,
Mercari, Whatnot and their peers. 184,820 US searches a month across the
cluster; every top-ten slot for the head terms held by sites under 100,000
visits a month; seller-tool affiliate programs paying $25–$300 per conversion
verified from their own pages; every fee schedule published on a page we can
archive and diff nightly.

## 1. What is being tested

> Can a site that computes each platform's seller fees **exactly from the
> platform's own published schedule**, states the date it was last verified
> against that schedule, and shows the change history, earn a top-20 Google
> position and ≥ 2,000 impressions in 30 days within six weeks of going live —
> with no social distribution and no paid traffic?

The claim is about **demand reaching an honest tool**, not about revenue.
Revenue is the next test, gated on this one.

## 2. Scope of the build (fixed; ≤ 2 weeks of build, ≤ $200)

- **One calculator page per platform**, for the platforms with a machine-
  readable published schedule, in this order until the two weeks are up:
  eBay (with the category table and store tiers), PayPal (checkout, cards,
  goods & services, invoicing, micropayments, international), Etsy (listing,
  transaction, processing, Offsite Ads), Square (in-person, online, keyed,
  invoices), Stripe, Shopify (plans and processing), Venmo (goods & services,
  business, instant transfer), Cash App (business, instant), GoFundMe, Depop,
  Poshmark, Mercari, Whatnot, Amazon referral fees, StubHub, Reverb, Vinted.
- **Each page computes every published fee component** for a user-entered
  sale (price, shipping, category/plan where relevant, domestic/international)
  and shows the take-home amount and the effective rate. Formulas come from a
  curated schedule file per platform (`docs/fees/<platform>.json`) that cites
  the source URL and the date the numbers were read from it.
- **Verification, the differentiator.** A nightly job fetches every source
  page, hashes it, and compares it with the stored hash. Unchanged: the page
  shows "verified against <platform>'s fee page on <date>". Changed: the page
  shows "the platform's fee page changed on <date>; these numbers are under
  review" until a human re-reads the schedule and updates the file. The change
  log is published per platform. No incumbent does this; it is the same
  evidence-archive mechanism Atlas already runs.
- **Secondary pages from the same data:** "how much does X take" per platform
  (the plain-words version of the calculator), and "X vs Y fees on the same
  sale" for the pairs people actually compare (eBay vs Mercari vs Poshmark vs
  Depop; PayPal vs Stripe vs Square; Etsy vs Shopify).
- **Money on the page**, disclosed on every page, never affecting a number:
  seller tools whose programs were verified in round two — Shopify ($150),
  QuickBooks ($25–$300), Wise (£50 business), Printful and Printify (5–10%
  for 12 months), Square (Impact), Vendoo (15%). Etsy's program bars price-
  comparison sites and is not used. Applications are the owner's.
- **Not in scope:** buyer-side fees (Ticketmaster, Airbnb), tax advice, any
  "which platform is best" opinion, accounts or newsletters, any social posting.
- **Hosting:** the same pattern as samebetornot.com — static build, nightly
  regeneration, GitHub Pages or Cloudflare Pages, a domain the owner buys
  (`feeverified.com` and `verifiedfees.com` were free on 2026-09-08).

## 3. Pass line — fixed before launch, same as round one

At **six weeks** from the day the sitemap is accepted in Search Console:

- **PASS:** ≥ 2,000 impressions in the trailing 30 days **and** at least one
  page in the top 20 for a head term of the cluster ("<platform> fee
  calculator" or "<platform> fees").
- **FAIL:** anything less. The site stays up as a record; no further build.
- A PASS opens a **revenue charter** (first $100, then $500/month) before any
  further investment; a PASS does not itself justify more build.

Measurement is the owner's personal Search Console property, logged weekly in
this file's scoreboard, the way samebetornot.com is logged in
`docs/IDEATION.md`.

## 4. What only the owner does

Buy the domain; create the Search Console property and submit the sitemap;
apply to the affiliate programs; nothing else. No social accounts, by the
owner's standing decision.

## 5. What would NOT count as success

- Traffic from the owner's own checks or from Atlas's own crawls.
- Impressions on brand-name queries that are not fee queries.
- A top-20 position on a term with under 500 searches a month.
- Any number arrived at by changing the pass line after launch.

## 6. Honest odds, recorded before the work

Better than round one. The incumbents are measurably weak, the head term is
large and commercial, and the verification feature is a real reason to prefer
the page. Against: a new domain still needs weeks to be trusted, calculators
are a crowded *format* even when the specific sites are weak, and the fee
schedules are complex enough (eBay's category table alone) that a wrong number
is the fastest way to lose the trust the feature is built on. The mitigation
is the one Atlas already uses: tests that pin every published example the
platforms give on their own fee pages (eBay publishes worked examples; so does
PayPal), so the calculator is checked against the platform's own arithmetic
before it ships.

## 7. Sequence and sign-off

1. Owner merges this file (sign-off).
2. Build in this repository under `feeverified/` with its own tests; each
   platform's schedule file cites its source and date; each calculator is
   pinned to the platform's own published examples.
3. Owner buys the domain; site goes live; Search Console; clock starts.
4. Weekly scoreboard here; verdict at week six, written at equal prominence.

- Proposed: 2026-09-08 (Claude, on the data in `docs/ideation2/`).
- Owner signature: merged 2026-09-08 (#37).

## 8. Build status

- 2026-09-08: build merged (#38, #39) — 17 platforms, one JavaScript engine
  shared by the page and the tests, every calculator pinned to the platform's
  own published worked examples, 41 pages, nightly verification against a
  reviewed baseline (17/17 sources). Build time: one day of the two weeks
  allowed; spend: the domain (owner-paid) and $0 hosting.
- 2026-09-08: domain bought by the owner — `verifiedfees.com`, DNS on
  Cloudflare.
- 2026-09-08: production build on Cloudflare Pages (project `verifiedfees`,
  alias `verifiedfees.pages.dev`, clean URLs, canonical URLs already on the
  domain); nightly agent `com.atlas.fees` installed (04:20, `docs/FEES.md`).
- 2026-09-08: **live on the domain.** The owner attached `verifiedfees.com`
  to the Pages project; the apex serves every page over HTTPS with clean URLs,
  canonical tags, the sitemap (41 URLs) and the IndexNow key on the domain;
  the Search Console verification TXT record is in DNS. `www` was not yet
  attached (522) — owner step.
- 2026-09-08: first nightly run under launchd — 17/17 sources verified, build
  fine, publish failed: launchd's PATH found Node 20 and wrangler needs 22.
  Fixed in #42 (the publish step now runs on the shell's nvm node); the fixed
  step was re-run under launchd's PATH: new production deployment, IndexNow
  accepted 41 URLs. The full agent run was kicked off again as the end-to-end
  check; the 04:20 nightly is the standing proof (`~/Library/Logs/atlas-fees.log`).
- 2026-09-08, hours after the domain went live: Chrome showed a Safe Browsing
  "Dangerous site" warning on `/cashapp`. Google's public Safe Browsing status
  for the domain reads "No available data" (not listed), so the verdict came
  from Chrome's real-time or on-device protection judging a day-old domain of
  payment-brand pages with forms. Response: an independence line on every page
  (build guardrail), "an independent calculator, not X" under every platform
  heading, independence in the meta descriptions; owner to check Search
  Console → Security issues, request a review if anything is listed, and file
  a false-positive report. Recorded as a risk to the test: a warning suppresses
  clicks and can delay indexing, and the pass line does not move for it.
- **2026-09-08: the clock started.** The owner submitted the sitemap in the
  personal Search Console property (verification TXT confirmed in DNS) and
  filed the Safe Browsing false-positive report the same day. **Week-six
  verdict date: 2026-10-20.** The nightly agent completed its first full
  unattended cycle at 20:57 UTC: 17/17 sources verified, built, published,
  IndexNow accepted. Still open, owner: attach `www.verifiedfees.com`; report
  what Search Console → Security issues shows.
- 2026-09-08: side effect of the custom-domain step — `samebetornot.com` was
  attached to this Pages project too and served Fee Verified for about an
  hour; detached, and the other site's DNS restored (logged in
  `docs/IDEATION.md`). Dashboard steps stay the owner's: wrangler has no DNS or
  custom-domain commands and the owner's browser is employer-managed.
- 2026-09-08, later: Search Console → Security issues reported **"Deceptive
  pages"** for the whole site (sample URLs: N/A). Publisher identity markup
  added to every page (og:site_name, author, WebSite/Organization JSON-LD,
  #47); the owner submitted the review request the same day, stating that the
  site has no logins or data-collecting forms, computes fees from cited
  published schedules, and now declares its independence on every page.
  Outcome: **cleared 2026-09-09**, overnight. Google's public Safe Browsing
  status moved from "No available data" to "No unsafe content found". The
  warning showed for roughly one day of the six weeks.
- 2026-09-09: `www.verifiedfees.com` attached by the owner; it serves the site
  with every canonical tag on the apex. Nothing owner-side remains for the
  hosting.
- 2026-09-09: **first money on the page.** Shopify's affiliate program (Impact)
  approved the owner; the Impact site-verification tag went on every page (#54)
  and the referral link went live on the two Shopify pages (#55). Placement is
  governed by a rule enforced in `verify_pages()` and pinned by a test: a
  sponsored link appears only on that platform's own calculator and "how much
  does X take" pages, never on a comparison page or the index, so no
  comparison on this site has a paid side and an unpaid side. The box sits
  below the numbers, is labeled "Sponsored link", carries no price or offer
  claim, and uses `rel="sponsored nofollow noopener"`. The methodology page now
  states how the site makes money. No fee number is affected; the charter's
  "never affecting a number" condition holds by construction.
- 2026-09-09: first scheduled nightly runs on both sites completed on time
  (samebetornot 04:00, Fee Verified 04:20: 17/17 verified, published, IndexNow).

- 2026-09-10: **platform 18, QuickBooks Payments.** Prompted by the owner's
  Intuit Product Referrals approval that day, and admitted only on the same two
  tests the original 17 passed, measured before building. Demand (Keywords
  Everywhere, US, 2026-09-10): "quickbooks payments fees" 1,000 a month,
  "quickbooks payment fees" 880, "quickbooks credit card processing fees" 720,
  "quickbooks ach fee" 480, "quickbooks fees" 480, "quickbooks invoice fees" 210,
  about 3,800 a month for processing-fee queries. Schedule: Intuit's Standard
  Pricing Schedule of September 12, 2023, which agrees with the payment-rates
  page dated 04/30/2026. Every quote was taken from the text the nightly
  fetcher renders. A summarizing fetch tool had misread the same pages,
  reporting another provider's column and a stale date, so quotes are never
  taken from summaries. Built inside the two-week window. The referral link is
  not placed yet: it goes on the QuickBooks pages only, under the Shopify rules,
  once the owner copies it from the Intuit Partner Portal. That link carries
  Intuit's standard new-customer discount (program guide: up to 50% off for 3
  months, subject to change), so its sponsored box will say a discount applies
  instead of Shopify's "no discount" line.

- 2026-09-10: **platform 19, Grailed**, chosen from a fresh demand pull once
  the round-two seller-fee cluster was built out. Candidates were scored the way
  round two scored its winner: US search volume (Keywords Everywhere,
  2026-09-10) and the study's weak-slot test on the top results (weak means
  under 100,000 monthly US visits, or a spam slot; 3 or more weak slots pass).
  - Grailed: "grailed fees" 1,900 a month and rising; 6 of 7 slots weak. PASS,
    built. Grailed's own help center shows a tiered seller fee: 9% at $120 and
    above, 6% with a $1.99 minimum under $120, for sales on or after May 20,
    2026. Every quote was taken from the verifier's rendered text.
  - Facebook Marketplace: 890 a month; 7 of 7 weak. Deferred. Meta's fee answer
    sits in a collapsed section the nightly fetcher cannot read, and Meta ended
    checkout for Shops in September 2025, so the fee could not be verified.
  - TCGplayer: about 2,080 a month. "tcgplayer fee calculator" has 3 of 7 weak
    (pass) but "tcgplayer fees" only 1 of 7, because TCGplayer's help center
    holds the top slots. It publishes worked examples, but one does not add up
    on its own page (shipping raised from $1.31 to $1.49, total left at $1.08).
    Deferred.
  - Clover: 1,300 a month; 5 of 9 weak. Deferred: its rates depend on which bank
    or reseller sells it.
  - Eventbrite: excluded by section 2, since its ticketing fees fall on buyers
    by default.

- 2026-09-10: **platforms 20 to 22, TikTok Shop, Upwork and Patreon**, on the
  owner's instruction after the demand pull. Demand is about 780, 720 and 480 US
  searches a month. On the weak-slot test, "tiktok shop seller fees" had 3 of 8
  weak, "upwork fees" 4 of 9, and "patreon fees" 3 of 7. All three publish fee
  pages the nightly fetcher can read, and every figure was checked against
  those pages rather than the search summaries, which were wrong three times:
  - **TikTok Shop's** policy says the referral fee "encompasses all TikTok Shop
    fees, with the exception of shipping and tax fees." The per-order
    transaction fee and processing fee that summaries add are not modeled.
  - **Upwork's** page says it is "not offering any regular discounts", so a
    summary's "0% with Freelancer Plus" was left out. Upwork's two worked
    examples ($26.66 on $266.64; $33.34 on $333.36) are reproduced to the cent.
  - **Patreon's** Founders section mentions micropayment rates for tiers of $3
    or less without publishing them. The page says so.

## 9. Scoreboard

Weekly, from the owner's personal Search Console, trailing 7 days unless
noted. The 30-day impression count for the pass line is taken only from the
clock start.

| Week | Date | Impressions | Clicks | Avg. position | Best fee-term page (position) | Notes |
|---|---|---|---|---|---|---|
| 0 | 2026-09-08 | — | — | — | — | clock started; live on verifiedfees.com; Chrome Safe Browsing warning reported as a false positive |
