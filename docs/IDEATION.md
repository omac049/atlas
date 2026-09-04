# Ideation, round one — where to point the method next

*2026-09-04. The prediction-market edge search is complete (four hypotheses,
four negatives — see [FINDINGS.md](FINDINGS.md)). This is the search for what
comes after, run with the same discipline: criteria first, data before
opinion, kill criteria before anyone falls in love.*

## Criteria (fixed before ideating)

An idea reaches a test only if it has all five:

1. **A nameable edge** — something we hold, know, or can do that the market
   does not.
2. **A demand test under two weeks and $200**, provable before building.
3. **An automation path** — recurring or repeatable revenue that mostly runs
   itself once proven.
4. **Clean lines** — no gambling-edge hunting (closed), no personalized
   financial advice, nothing touching the owner's employer or its data.
5. **Leverage** — the method, the archive/verifier, the monitoring machinery,
   or the owner's marketing-analytics craft.

## Candidates, with the data that decided them

Search volumes are US monthly (Google Keyword Planner, pulled 2026-09-04),
with cost-per-click as the market's own estimate of what a visitor is worth.

| Idea | Edge | Demand signal | Verdict |
|---|---|---|---|
| **A. Prediction-market data API (B2B)** — sell the archive/verifier outputs | Irreplaceable rules-text archive | "prediction market api" 90/mo. Incumbents: Dome (YC-backed), Tatum, Propheseer, Predexon, PredictionData — all shipping unified Kalshi+Polymarket APIs today | **Kill.** Crowded, funded, and the search demand is ~90 people a month. |
| **B. Pre-registered validation as a service** ("kill your idea in a week") | The method, demonstrated four times | "validate business idea" 140/mo at $2.62 CPC; "idea validation service" 10/mo | **Deprioritize.** Real but tiny, consulting-shaped, not automatable. Keep as a fallback income line, not a product. |
| **C. Fine-print / ToS change monitoring** — point the hash-and-diff engine at contracts people must watch | Monitoring machinery already built | "terms of service monitoring" **0**/mo; "website change monitoring" 720/mo, commodity ($12/mo, self-hostable). Compliance tier starts ~$10k/yr (Vanta) — enterprise sales | **Kill.** No search demand at the niche, commodity at the generic, enterprise at the valuable end. |
| **D. Marketing-analytics automation** (GA4 anomaly alerts, GTM audit, content decay) | Owner's domain craft | "ga4 anomaly detection" 10/mo; "gtm audit tool" 0; "content decay tool" 0 | **Kill** as search-driven products. (Craft stays useful — see G.) |
| **E. "Tested" newsletter** — pre-registered tests of popular claims, honesty as the brand | The method + four public negatives | No search demand by nature; audience-built, slow | **Deprioritize** as a standalone; becomes the editorial layer of G. |
| **F. Consumer "same bet or not?" tool** | The cross-venue verifier | See G — it is the wedge, not the product | **Fold into G.** |
| **G. A factual Kalshi-vs-Polymarket resource, generated from Atlas data** | Data nobody else has, plus the owner's SEO craft | See below | **The survivor.** |

### G, in detail — the only candidate with demand proven by someone else's data

The comparison-intent cluster around the two venues is large, expensive, and
climbing fast — every number below is real search demand, not a hypothesis:

| Query | Monthly searches | CPC | 12-month trend |
|---|---|---|---|
| kalshi vs polymarket | 5,400 | $23.19 | 880 → 9,900 |
| polymarket vs kalshi | 4,400 | $18.94 | 480 → 6,600 |
| kalshi referral code | 2,900 | $25.73 | 720 → 8,100 |
| is polymarket legal in the us | 2,900 | — | 720 → 5,400 |
| kalshi fees | 1,900 | $6.40 | 590 → 2,400 |
| polymarket referral code | 1,600 | $10.99 | 10 → **14,800** |
| polymarket fees | 1,000 | $20.78 | 390 → 1,000 |
| is kalshi legal | 880 | $19.38 | 720 → 1,900 |
| kalshi taxes | 880 | $9.16 | 210 → 1,600 |
| kalshi review | 720 | — | 260 → 480 |
| best prediction market | 590 | $9.71 | 170 → 880 |
| kalshi vs polymarket reddit | 320 | **$51.87** | 70 → 720 |
| kalshi or polymarket | 260 | $7.21 | 20 → 480 |

Roughly **25,000 searches a month** in the cluster, with advertisers paying
$7–$52 per click. Category interest is at its highest ever (Google Trends: Kalshi
peaked at 100 in July 2026). And Coinbase now routes millions of retail users
into these exact contracts — every one of them a future searcher of these
terms.

**Who owns the head term today:** SI.com, Covers, RotoWire, RotoGrinders,
SportsbookReview, DefiRate, NEXTPredict — the sports-betting affiliate
industry, with high-authority domains and generic "fees, promos, legal status"
comparisons. Ranking a new site for the head term against them is a long,
uncertain fight. That is the honest competitive picture.

**The wedge they cannot copy:** none of them can answer the questions Atlas
answers automatically for *every market*:

- *Is this the same bet on both venues?* (the verifier — Cardi B's cameo
  settled to opposite outcomes; CPI's missing-data fallbacks diverge)
- *What does it actually cost me at this price on each venue?* (the exact
  published fee formulas, not a "7% vs 0%" headline — at 70¢ Kalshi charges
  1.5¢, and the difference flips with price)
- *Which venue's rules say what happens if the data never arrives?* (the
  archive)
- *Did either venue quietly change the rules on this market?* (the hash
  history)

Programmatic pages — one per event family, one per fee scenario, regenerated
nightly from data the site already collects — are content the big affiliates
will never hand-write. The closest existing thing is DefiRate's per-event
"Fed decision odds" page. The long tail is open.

**How it makes money (verified 2026-09-04, with gaps named):**
- Polymarket via the Dub partner program: **$10 per referral's first deposit,
  20% revenue share on their perpetuals trading fees, $0.01 per click**,
  bounties at revenue milestones. Application required; payout mechanics not
  shown on the public page.
- Polymarket US runs a separate approved-affiliate program; terms disclosed
  only by email (affiliate@polymarket.com).
- Polymarket's trader referral program pays 30% of referred traders' fees for
  180 days — but requires $10,000 of lifetime trading volume to qualify. Not
  a fit for a paper-only owner; noted, not pursued.
- Kalshi's refer-a-friend pays **$25 in trading credits, not cash**. Whether a
  cash affiliate program exists could not be verified (the affiliate-hub page
  refused the fetch). Treat Kalshi revenue as zero until proven.
- Display advertising on informational pages in a $10–50 CPC niche.

**Honest revenue math.** A page ranking fifth for the head cluster might see
500–1,500 clicks a month. At a 2% first-deposit rate and $10 CPA, that is
$100–$300 a month before revenue share and ads. Reaching $1,500 a month means
ranking on many terms across the long tail — plausible over six to twelve
months *if the wedge ranks at all*, and not guaranteed. This is an SEO asset
that compounds if it works and returns nothing if it does not.

**Risks, named:** new-domain authority against SI.com-class competitors;
Google's treatment of gambling-adjacent affiliate content; live regulatory
turbulence (New York and Massachusetts sued Kalshi in August 2026); referral
terms that venues can change at will; and months to any signal. Clean lines
hold: factual comparison with affiliate relationships disclosed, no picks, no
personalized advice, nothing involving the owner's employer.

## The pre-registered demand test (proposed — owner signs by merging)

**Claim:** search engines will surface Atlas-generated factual comparison pages
for cluster terms, and at least one venue program will accept the site as an
affiliate.

**Build (≤ 2 weeks, ≤ $200):** a domain, a static site generated nightly from
Atlas: ~50 programmatic per-event comparison pages from the twin-shape pairs,
plus five pillars — a fee calculator at any price using the exact published
formulas; legal status by state, sourced; taxes, sourced and advice-free;
"same bet or not?" with the Cardi B and CPI cases; referral terms compared
honestly, credits vs cash. Google Search Console connected. Three social posts
of the same-bet tool. **No paid traffic.**

**Kill criteria at week 6 (fixed now):** the test PASSES only if **both**
hold —
1. Search Console shows **≥ 2,000 impressions in the trailing 30 days** and
   **at least one page in the top 20** for any cluster term, **or** the social
   posts drive ≥ 300 clicks; **and**
2. at least one affiliate program (Dub/Polymarket or Polymarket US) has
   **approved** the site.

Fail either → kill, written here with the numbers. Pass → a second charter
sets revenue gates (first $100, then $500/month) before any further build.

**What only the owner can do:** choose and buy the domain; create the hosting,
Search Console, and affiliate accounts (accounts and payments are the owner's,
never the assistant's); post from their own handles. Everything else —
generation, pages, calculators, the disclosure language — is build work.

## Build status

- 2026-09-04: site generator merged (#18) — 71 pages from a live build, 61
  pairs, 4 verified same-bet, every page carrying the disclosure and the
  not-advice notice by build-time guardrail. Nightly regeneration agent
  `com.atlas.site` added. The demand-test clock has **not** started: it starts
  the day the site is live on a domain and the sitemap is submitted, which are
  the owner's steps in `docs/SITE.md`.
- 2026-09-04: domain purchased — `samebetornot.com`.
- 2026-09-04: **live** at https://samebetornot.com on GitHub Pages (gh-pages
  branch, HTTPS enforced, www → apex, clean URLs, nightly publish proven).
  Clock still not started: Search Console property + sitemap submission are
  the owner's step in their personal Google account (the work-account
  connector must not be used).

## Deprioritized, not forgotten

B (validation as a service) is the highest-probability *income* line if the
goal ever shifts from passive to active; it needs no search demand because it
sells by conversation. E (the "Tested" brand) is the natural editorial voice
of G. Both wait behind G's six-week verdict.
