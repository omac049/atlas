# The fee-calculator demand test (second product test)

**Status: PROPOSED — awaiting owner sign-off.** Merging this file to `main`
constitutes sign-off on the scope, the budget, and the pass/fail line below.
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
- Owner signature: _pending — merging this file constitutes sign-off._
