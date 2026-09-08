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

## Results

_Pending — written below as the data lands, at equal prominence for the
clusters that fail._
