# Ideation round two — find the gap, not the project (2026-09-08)

**Owner's brief:** "We need to achieve our goal even if it's not Atlas. You can
do the research to find gaps." The goal, restated: put ≤ $2,000 and build
skills into a digital product whose output exceeds the investment, that runs
without the owner's daily attention or social accounts, and that can be
replicated or sold as a membership. Not financial advice, not a trading
strategy, nothing that needs the owner's employer or personal socials.

**What we bring that most sites don't** (proven on samebetornot.com): pages
generated nightly from public data with a source on every line; deterministic
verification ("same thing or not"); exact calculators from published
schedules; 50-jurisdiction legal tables; "documented record" pages for
"is X legit". These are the archetypes the search is limited to, because they
are what we can build fast, honestly, and at scale without a writer.

## Criteria — fixed before any number is pulled

A candidate passes screening only if ALL hold:

1. **Demand:** ≥ 20,000 US searches/month across its keyword cluster (Google
   Keyword Planner via Keywords Everywhere), and the 12-month trend is flat or
   rising (last 3 months ≥ first 3 months).
2. **Weak incumbents:** in the top 10 organic results for the cluster's head
   terms, **at least 3 results** come from domains with estimated organic
   traffic under 100,000 visits/month, or from forums/UGC (Reddit, Quora).
   A SERP owned entirely by NerdWallet-class domains fails.
3. **Money on the page:** a cash-paying program relevant to the page's
   reader — affiliate ≥ $20 per conversion, or lead-gen with a known buyer,
   or a tool/membership with demonstrated willingness to pay — verified from
   the program's own terms. Display ads alone do not pass.
4. **Buildable by our method:** the core pages can be generated from public,
   citable data (schedules, statutes, filings, registries, APIs) — no
   opinion, no advice, no writer in the loop. Not medical, not legal advice,
   not "which investment".
5. **Passive:** no social distribution required to test; search-led demand
   test in ≤ 6 weeks with the same pass line as round one (≥ 2,000
   impressions / 30 days with a top-20 page).

Scoring after screening: demand × (share of weak SERP slots) × payout,
reported with the raw numbers so the ranking can be checked.

## Method

1. Seed 8 archetype spaces × ~40 keywords each from the archetypes above;
   pull volume, CPC, competition, 12-month trend (this file records the
   totals; raw pulls in `docs/ideation2/keywords.json`).
2. For every cluster clearing criterion 1, fetch the live top-10 for its two
   head terms and score each ranking domain's estimated traffic
   (`docs/ideation2/serps.json`).
3. For clusters clearing 1–2, verify monetization from program pages
   (`docs/ideation2/monetization.json`).
4. Rank; pick ≤ 3; write a demand-test design for the winner. No build until
   the owner picks.

## Results (2026-09-08; raw pulls in `docs/ideation2/`)

Four archetype spaces were seeded with 190 keywords (Keywords Everywhere,
US, Google Keyword Planner), 21 head-term SERPs were scored against domain
traffic estimates (Google blocks scripted result pages, so top results come
from a search-API proxy — see `sources.json`), and the public data each space
would be built from was probed live.

### Screening table

| Space | Demand (sum of cluster, /mo) | Trend | Weak-incumbent SERPs | Money on the page | Buildable from public data | Verdict |
|---|---|---|---|---|---|---|
| **A. Platform fee calculators** (eBay, PayPal, Etsy, Square, Stripe, Shopify, Venmo, Cash App, GoFundMe, Depop, Poshmark, Mercari, Whatnot, Amazon…) | **184,820** (46 kw) | flat/rising (eBay 33k→49.5k) | **8 of 8 PASS** — every top slot is a site under 100k visits/mo, most under 35k | Shopify $150/referral and Coinbase 50%-of-fees verified; rest pending | Yes — eBay, PayPal, Etsy fee pages loaded and parsed; PayPal stamps "Last Updated Sept 1 2026" | **PASS (leader)** |
| **B. Fintech "is X legit / safe / FDIC / what bank"** (~35 apps) | **177,010** (47 kw, excl. Temu/Shein) | flat; Rocket Money and Chime rising | **3 of 8 PASS** — weak for Chime, Rocket Money, Brigit; NerdWallet/WalletHub/Trustpilot own SoFi, Upstart, Klarna, MoneyLion | High CPCs ($3–$10); program payouts pending | Yes — CFPB complaint API (12,308 Chime complaints/12 mo with issue breakdown) and FDIC BankFind (partner-bank certificates) both answer without keys | **PARTIAL** — viable for the long tail of apps, not the head brands |
| C. State legality (cannabis, THCa, kratom, tint, carry) | 618,080 (34 kw) | flat; NY/NJ spikes on news | 1 of 3 PASS — state queries owned by .gov, Wikipedia, news | CPC $0 outside legal states; affiliates pending | Yes, but statute research per state is heavy | **FAIL on criterion 2** |
| D. Government fees & rates (passport, sales tax, notary, LLC) | 219,280 (15 kw; 135k of it is two zero-money terms) | seasonal | 1 of 2 PASS (passport slots are county/edu pages) | CPC ≤ $0.12; expediter affiliates pending | Yes | **FAIL on criterion 3** |
| E. Gambling legality by state | not sized | — | not scored | CPC $20+ but affiliates need state licensing in many states | — | Excluded (licensing) |

### Why A wins on the numbers

- **Demand** is a third of C's but it is *commercial* demand: people pricing a
  sale, not settling an argument. The head term alone ("ebay fee calculator",
  40,500/mo, 49,500 in August) is larger than the whole prediction-market
  cluster we are testing now.
- **The incumbents are the weakest measured anywhere in this study.** The
  sites holding the top slots for eBay, PayPal, Etsy, Stripe and Square
  calculators get 0–60k visits a month; several rank with a single page. This
  is a field where a better, verified tool can plausibly take the slot within
  the six-week window — the opposite of the "is Kalshi legit" SERP.
- **It is exactly our method.** Each platform publishes a fee schedule; we
  already archive published text nightly with a hash and diff it. A calculator
  that says *"verified against eBay's fee page on 2026-09-08 — last changed
  on …"* with the change log is something none of the incumbents do, and it
  is the honest differentiator: correctness you can check, not prettier math.
- **Money on the page** is seller tools, not the platforms themselves: a
  seller pricing an eBay sale is the customer Shopify pays $150 for, that
  Wise/Payoneer pay for (international payouts), that QuickBooks and the
  crosslisting tools (Vendoo, List Perfectly, Crosslist) pay for. Final
  payout table below when verification completes.
- **Search-volume note against ourselves:** "fee change" queries are near
  zero. The change-tracking feature is a trust and email hook, not a traffic
  source, and the plan treats it that way.

### What B is, and is not

B passes demand and buildability outright and has the richest public data
(CFPB complaints by product and issue, FDIC certificates, FTC/CFPB actions).
It fails competition on the biggest brands. The honest reading: B is a
long-tail play — "is Brigit legit", "is Rocket Money safe", "is Cleo legit" —
and a natural *second* site or a second section once A has a domain with
authority. It is also YMYL for Google; the documented-record format is the
right defence, but it is a slower climb than A. Not first.

### The other two archetypes, briefly

C is enormous and mostly worthless commercially outside legal states, and the
state pages are owned by governments and Wikipedia. D is government
information with no buyer on the page except passport expediters. Both are
recorded so the numbers are not re-pulled next time someone suggests them.

### Data-source verification (criterion 4)

| Source | Verified | Notes |
|---|---|---|
| eBay selling fees page | loaded 2026-09-08 | full category table, per-order fee, penalties, international fee |
| PayPal US merchant fees page | loaded 2026-09-08 | "Last Updated: September 1, 2026" stamped on page |
| Etsy Fees & Payments Policy | loaded 2026-09-08 | "Last updated on Feb 13, 2026"; processing fee lives in the Etsy Payments Policy |
| CFPB complaint API | queried 2026-09-08 | no key; company/date filters; aggregations |
| FDIC BankFind API | queried 2026-09-08 | no key; name filter → certificate, active flag |

### Recommendation

Build **A** as a demand test on the same rules as round one: ≤ 2 weeks of
build, ≤ $200, no social, search-led, verdict at week six on ≥ 2,000
impressions / 30 days with a top-20 page. The product: one calculator page
per platform (≈ 20 platforms), each computing every published fee component
from the archived schedule, each carrying the verification date and the
change log, plus "how much does X take" and "X vs Y fees on the same sale"
pages generated from the same data. Sellers' tools as the money on the page,
disclosed on every page. Domains available at time of writing:
`feeverified.com`, `feesverified.com`, `verifiedfees.com`, `takerates.com`.

Nothing is built until the owner says so. samebetornot.com keeps running
untouched; its six-week clock is the control.

**Update 2026-09-08:** the owner signed the charter
(`docs/decisions/2026-09-08-fee-calculator-demand-test.md`, #37) and the build
is live on Cloudflare Pages; build status and the weekly scoreboard are logged
in that file.

### Monetization (criterion 3) — verified from program pages, 2026-09-08

Seller and payment tools (the reader of a fee-calculator page):

| Program | Pays | Network | Source loaded |
|---|---|---|---|
| Shopify Affiliates | **$150** one-time per qualified full-price merchant (US, CA, AU, UK…) | Impact | shopify.com/affiliates |
| QuickBooks (Intuit) | **$25–$300** per eligible subscription, by plan | CJ / FlexOffers; business program in-house | official program page |
| Wise | £10 per new personal customer, **£50 per business** customer | Partnerize | wise.com affiliates |
| Printful | 10% of product price on referred stores' orders for 12 months | in-house | printful.com affiliates |
| Printify | 5% of catalog price on referred users' orders for 12 months | PartnerStack | printify.com affiliates |
| eBay Partner Network | % of gross merchandise bought, per-category rate card with caps (e.g., fashion 4%, $550 cap); 24-hour attribution | in-house | rate card page |
| Payoneer, Melio, Etsy, Avalara | programs exist; payouts not published (case-by-case) | various | official pages |

Criterion 3 **passes** for space A on Shopify and QuickBooks alone. The rest
of the seller-side sweep, verified: Square runs a publisher program on Impact
(amounts unpublished; $5–$200 per action is blog-reported only); Vendoo pays
15% of subscription (Awin, US sign-ups only); List Perfectly pays 20%
recurring in perpetuity but requires the publisher's own paid subscription.
Not usable: PayPal's publisher listing pays $0 (its $500–$2,500 referral
program is for developers only), Stripe, GoFundMe, Givebutter and Zelle have
no publisher program, and Mercari, Poshmark and Depop pay in credits or are
creator-only. The platforms whose fees the site explains are therefore
**not** its payers, which is the right separation: nobody can say the
numbers are shaded toward a sponsor.

Adjacent, for completeness (space D readers): LLC formation pays the most of
anything measured — ZenBusiness $75/$125/$175 per plan, Northwest $150 per
formation, LegalZoom from $125 CPA, Bizee $50–$175 — but sits on SERPs owned
by those same companies and by NerdWallet-class publishers, which is why D
did not pass. Tax software pays 15–16% (TaxAct, FreeTaxUSA, TurboTax).
Passport expediters: Swift $75 per order, RushMyPassport 10% of ~$350.
Online notary: 10–15%. Medical-card services: NuggMD $20 CPA.

Fintech (space B) program payouts, verified from loaded pages (all 76
programs with sources in `docs/ideation2/monetization.json`): MoneyLion $80
per funded loan; SoFi $80 per funded personal loan / $120 per student refi
(listing deactivated on that network — live network unconfirmed); Acorns $52
per first recurring deposit; Betterment $50 per funded account (welcomes
comparison and review sites); moomoo $40 per deposit; Robinhood $16 per
funded account; Chime $10.80 per enrollment on a 1-day cookie; Rocket Money
$4–$10; Credit Karma $7 per signup; Coinbase 50% of fees for 3 months;
Kraken up to 50% revenue share. Upstart pays 1.4% of the funded loan; Brigit
6.4% per sale. No public program: Cleo, Albert, Varo, Current, Empower/Tilt,
Affirm, Marcus; Klarna and Wealthfront are blog-reported only. Only M1 Finance
publishes compliance rules with teeth (mandatory "not financial advice" and
paid-content disclosures, no "bank" wording, crawler-enforced). Money exists
for B; the SERPs are what hold it back.
