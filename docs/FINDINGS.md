# What Atlas found

*Six weeks, four hypotheses, four pre-registered negatives, zero dollars of
principal. This page is the whole story on one screen; every claim links to
the charter and the evidence that produced it.*

## The question

Can a cross-market prediction-market research system — watching Kalshi and
Polymarket, normalizing their contracts, verifying equivalence with
deterministic rules — find a way to turn $2,000 into $20,000 in twelve months?
Failing that, can what it learns be sold?

## The answers

| # | Hypothesis | Verdict | The number that decided it | Record |
|---|---|---|---|---|
| 1 | Cross-venue arbitrage: the same event is priced differently on two venues, and the gap can be locked | **Dead** | Against the only venue a US account can trade, 27 twin pairs and 0 executable gaps; every recorded gap was against the offshore venue. Best case 3.4% annualized on capital locked 5–8 months, on $33 books. | [90-day study](NINETY_DAY_STUDY.md) |
| 2 | Settlement-machinery gaps (missing fallbacks, absent sources) predict disputed resolutions | **FAIL** | 15 famous disputes vs 45 rule-selected controls: median clarity score **65 vs 65**. No separation. | [proof charter](decisions/2026-08-31-fine-print-proof-charter.md) · [corpus](proof/test-a-corpus.json) · [result](proof/test-a-result.json) |
| 3 | Predicate ambiguity ("what counts as a suit?") predicts disputes | **Abandoned at gate** | Instrument flagged **4 of 15** disputes against 60% required. Reading the misses showed disputes are three different things, and the theory covered one. | [charter](decisions/2026-09-02-predicate-ambiguity-charter.md) · [gate](proof/ambiguity-gate-result.json) |
| 4 | The in-game price lags the game — a human with the public feed could take stale prices after a lead change | **DISPROVEN** | 676 lead changes across 262 games: the market repriced a median **10.6 seconds *before*** MLB's official play timestamp, in 91% of plays. The move is real (+11.2¢ net) and already gone. | [charter](decisions/2026-09-04-repricing-lag-charter.md) · [result](proof/repricing-result.json) |

These four cover the space an outsider with public data can reach: whether the
price is right across venues, whether the contract text predicts trouble (two
different ways), and whether the price is slow. The fourth charter said in
advance it would be the last prediction-market hypothesis. It was.

## What each failure taught

- **Two US venues are arbitrage-linked.** Kalshi and Polymarket US agree on
  price to within fees. The persistent "gaps" lived only against Polymarket's
  offshore venue — which cannot be traded from the US, and whose prices
  nobody can therefore close.
- **Disputes are not one thing.** The famous ones split into predicate
  ambiguity (what counts as a "performance"), oracle governance (a whale vote
  overriding facts), and precise rules nobody read (the shutdown market spelled
  out exactly what would happen, and traders were surprised anyway). No text
  score predicts all three, and a score built for one was tested on a corpus
  where the other two were the majority.
- **The profit is real, and it belongs to the bots.** When a team pulls
  ahead the price really does jump — eleven cents. Market makers on licensed
  sub-second feeds capture it before the public play-by-play finalizes the
  play. A person watching the game is structurally ten to twenty-five seconds
  behind the price, always.

## What the discipline caught

The method mattered more than any single result:

- The original study's GO threshold measured frequency only and **passed on
  day 2**; it could not fail. Adding return on locked capital flipped it to
  NO-GO on the same data. ([amendments](NINETY_DAY_STUDY.md))
- The radar had recorded 18,650 gaps, **100% against a venue the codebase
  itself declared non-executable.** Pointing it at the tradeable venue was one
  line of scope and changed the answer.
- A Kalshi series probe admitted a **2027-season market** into a disputed arm;
  the fix that removed it hurt the theory, which is what made it trustworthy.
- The repricing instrument silently absorbed fast-maker pre-play moves so a
  negative lag could never register; caught by its own test, fixed before the
  freeze, against the theory's interest.
- A watchdog **killed the monitor 170 times in a row** after an 18-hour laptop
  sleep, because a freshly started monitor could not write a log line within
  the watchdog's 60-second patience. Silence measured from
  max(log, last-restart) ended it.
- A known instrument bug (literal `"involved"` missing `"involves"`) was
  **deliberately left unfixed** in hypothesis 3, because every fix available
  after seeing a failing development-set result moved the numbers toward the
  theory. The asymmetry is the tell.

## What remains true and useful

- A **daily-growing archive of venue rules text**, hashed, that Kalshi prunes
  from its own API after ~6 weeks and nobody can rebuild retroactively.
- A **cross-venue verifier** that catches contracts which look identical and
  settle to opposite outcomes — Cardi B's Super Bowl cameo did exactly that on
  the two venues.
- A **Settlement Clarity Score** that truthfully describes what a venue's
  text publishes (it just doesn't predict disputes).
- A **release-window radar** that wakes the machine, bursts to 30-second
  sampling around scheduled macro prints, and runs unattended.
- A **method**: state the claim so it can fail, fix the thresholds before the
  data, test in days, publish the negative as loudly as a positive. It killed
  four plausible ideas for the cost of the electricity.

## Where things stand

- Atlas runs itself: monitor, watchdog, keep-awake, weekly study and intel
  reports, nightly backup with VACUUM. Paper-only, permanently.
- The 90-day study decides 2026-11-17 and already reads NO-GO.
- The remaining scheduled events (Sep 11 CPI, Sep 16 FOMC — including the
  first end-to-end test of the revived label loop) run without intervention.
- No product funnel was built, by the owner's rule: no theory survived to
  justify one.
- The $2,000 is intact.
