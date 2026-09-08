# The market-making charter (fifth hypothesis)

**Status: PROPOSED — awaiting owner sign-off.** Merging this file to `main`
constitutes sign-off on the theory, the instrument definition, and every
threshold below. Nothing may change after the freeze commit named in §7.

**Lineage.** Hypotheses one through four tested whether an outsider with public
data can be *right* about a price (cross-venue gaps, fine-print machinery,
predicate ambiguity) or *early* to it (in-game repricing). All four returned
pre-registered negatives. The owner's underlying question — "can software earn
money in these markets automatically" — has one honest form left that none of
those tests covered: not predicting anything, but **being the counterparty**.
A market maker rests orders on both sides, earns the spread when uninformed
traders cross it, and loses when informed traders do. Whether a *naive*,
rule-following maker with only public data nets positive is an empirical
question, and it is the last one in this direction worth a charter.

**Paper-only, without exception.** This is a retrospective replay over
published trade tapes and Atlas's own recorded order books. It places nothing,
reads no credentials, and a PROVEN result reopens nothing automatically (§9).
The paper-only invariant and the owner's 2026-08-20 decision to drop execution
both stand regardless of outcome.

## 1. The theory, stated so it can fail

> A naive symmetric market-making rule — quote a fixed distance either side of
> a public reference price, requote on a fixed clock, cap inventory, hold to
> settlement — earns more on Kalshi from spread capture than it loses to
> adverse selection and the venue's published maker fee.

Two things must both be true: the rule is **filled** often enough by traders
crossing the spread, and those fills are **not systematically wrong** — the
positions the rule is left holding must not settle against it more often than
the captured spread pays for. Adverse selection is not a confound here; it is
the thing being measured.

## 2. Why Kalshi only, and why that is the harder case

**Kalshi** publishes its full trade tape (`GET /trade-api/v2/markets/trades`,
cursor-paginated, microsecond `created_time`, `yes_price_dollars`, `count_fp`,
`taker_side`, `is_block_trade`; verified live 2026-09-08: 23,555 prints on
`KXFEDDECISION-26SEP-H0` back to 2026-07-03; 24,346 on one MLB game). Atlas
additionally holds **552,144 sequence-numbered order-book snapshots** on the
twelve watched Fed-decision markets since 2026-08-21, at a median cadence of
0.15 s (`orderbook_snapshots`, stream-driven), giving displayed size at every
level for the queue model in §4.

**Polymarket US** publishes no trade tape (probed 2026-09-08: `/trades`,
`/history`, `/price-history` all 404) and Atlas's book sampling there is
30-second polling in bursts — not enough to know when a resting order would
have filled. It is therefore **untestable retrospectively** and excluded. This
cuts against the theory: Polymarket US *pays* makers a rebate, Kalshi *charges*
them. If naive making cannot clear the fee on Kalshi, the venue where it is
cheapest to test is also the least favorable, and a PASS there would be the
stronger result.

## 3. Two arms, judged separately

**Arm A — slow markets.** Every Kalshi Fed-decision market
(`KXFEDDECISION-26SEP-*`) that settles on 2026-09-16, plus any other market
with both a full tape and Atlas book snapshots that settles by that date.
Dense books, thin trading, spreads measured in cents, multi-week duration.
Both the tape and the queue model apply.

**Arm B — fast markets.** Every settled `KXMLBGAME` home-team contract with
first pitch between **2026-08-15 and 2026-09-10** inclusive — the window
already fixed by the fourth charter, so no new selection is made. Deep tapes,
no book snapshots; the strict-crossing fill model applies (§4). The excluded
probe game (STL @ LAD, 2026-09-03) stays excluded.

The arms answer different questions — "does making pay where nothing happens
for days" versus "does making pay where the pros reprice in seconds" — and a
PASS in one says nothing about the other. Each is judged on its own sample
against the same criteria (§6). The multiplicity is acknowledged here, in
advance: two arms, two chances, and the consequence of a PASS (§9) is scoped
to the arm that passed.

## 4. The instrument — one naive rule, fixed here

For each market, replayed forward through its tape from the first print in the
window (Arm A: 2026-08-21, when book snapshots begin; Arm B: first pitch) to
settlement:

- **Reference price** `r(t)`: Arm A — the mid of best YES bid and best YES ask
  in the latest book snapshot at or before `t`; Arm B — the volume-weighted
  YES price of the last 20 non-block prints before `t` (the same reference the
  fourth charter used). If no reference exists, no quotes are placed.
- **Quotes:** a YES bid at `r − s` and a YES ask at `r + s`, rounded to the
  cent and clipped to [1¢, 99¢], each for `q` contracts, with half-spread
  **s = 2¢** and **q = 10** (small on purpose: 10 contracts is a rounding error
  on these books, so our own fills are assumed not to move the market — the
  one place the model is knowingly optimistic).
- **Clock:** quotes are placed at `t`, take effect at `t + L` with latency
  **L = 1 s**, and are cancelled and replaced every **Δ = 30 s**. Unfilled
  quantity does not carry over.
- **Inventory cap:** net position bounded at **±100 contracts**; while at the
  cap, the side that would increase exposure is not quoted.
- **Fill model, Arm A (book available):** at placement, `ahead` = displayed
  quantity at our price on our side in the latest snapshot (zero if our price
  improves the book). Cumulative non-block taker volume on the opposing side at
  prices at-or-through our price, from `t + L` onward, must exceed `ahead`
  before we fill; fill quantity = min(`q`, excess). Orders ahead of us that
  cancel are ignored — we never fill sooner than FIFO on the displayed size,
  which is conservative.
- **Fill model, Arm B (no book):** *strict crossing* — a resting ask at `p`
  fills only when a taker BUY prints at a price **strictly above** `p`; a
  resting bid at `p` only when a taker SELL prints **strictly below** `p`.
  A print at exactly our price never fills us (we may have been behind it).
  Quantity = min(`q`, print size). This under-fills; it cannot over-fill.
- **Fee:** Kalshi's published maker formula, applied per fill exactly as the
  July 7, 2026 schedule states it: `ceil(0.0175 × C × P × (1 − P))` cents for
  `C` contracts at price `P`, for every sampled series (Fed decisions and MLB
  both carry maker multiplier 1 in that schedule). This is the schedule's own
  reading, neither favorable nor punitive.
- **Marking:** every remaining position is settled at the market's published
  result ($1 or $0). Unsettled markets are excluded from the primary result.
- **Capital:** peak collateral = the maximum over time of (collateral locked by
  resting orders: `p × q` per bid, `(1 − p) × q` per ask) + (cost basis of
  open positions). Kalshi fully collateralizes resting orders, so this is
  what the rule would actually have tied up.

Per market the artifact records: fills (with the tape print that caused each),
fees, settlement cash flow, net P&L, contracts filled, peak collateral, and
market duration. Every number traces to prints and snapshots by id.

**Sensitivity (reported, never decisive):** s ∈ {1¢, 2¢, 3¢, 5¢},
Δ ∈ {5 s, 30 s, 300 s}, L ∈ {1 s, 5 s}, and the per-contract fee ceiling
`atlas/gap_radar.py` uses for takers. The primary parameters are the ones
above; the grid exists to show whether a result is a knife-edge.

## 5. What "adequate sample" means

- **Arm A:** at least **4 settled markets**, each with at least **500
  non-block prints** inside the window.
- **Arm B:** at least **100 settled games.**

Below these, the arm is INCONCLUSIVE, reported as such, never padded.

## 6. Pre-registered pass criteria — all four must hold, per arm

1. **It earns:** net P&L after fees, summed over the arm, is **> 0**.
2. **It is not one lucky market:** net P&L is positive in **≥ 60%** of the
   arm's markets.
3. **It clears noise:** net P&L is **≥ 0.5¢ per contract filled** — smaller
   than that is inside the fee's own rounding.
4. **It survives being slow:** criteria 1–3 also hold at **L = 5 s**. An edge
   that vanishes with four extra seconds of latency is the pros' edge, not a
   naive maker's.

**FAIL:** any criterion unmet on an adequate sample. **PASS:** all four.

## 7. Freeze and blindness

The instrument (`atlas/making.py` + tests + runner `docs/proof/run_making.py`)
is committed **before** any replay is run, and that commit's hash is recorded
here as the freeze commit. Every parameter in §4 and every threshold in §5–§6
is fixed by this file.

Blindness, stated honestly: the author has watched the Fed markets' prices and
books for weeks through the radar and the public site, and has read the
fourth charter's play-level results on MLB tapes. The author has **not**
computed a fill, a fee, or a P&L for any resting-order rule on any market.
Instrument validation before the freeze uses only the excluded MLB probe game
and synthetic tapes in the test suite.

## 8. What would NOT count as proof — and the confounds, named first

- **The queue model is a bound, not the truth.** Arm A never fills ahead of
  displayed FIFO size and ignores cancellations ahead of us (conservative);
  it also ignores hidden or iceberg size (optimistic). Arm B's strict-crossing
  rule cannot over-fill. Neither model can manufacture a fill the tape does
  not justify.
- **Impact is not modeled.** At q = 10 that is a stated approximation, and it
  is the reason q is 10.
- **The reference lags in Arm B.** A trade-VWAP reference trails the book,
  which widens the effective spread the rule quotes. The s-grid shows whether
  that matters; the primary s stays 2¢.
- **Winning on one Fed market is not a result.** Arm A's sample is small by
  construction; criterion 2 exists so a single lucky settlement cannot carry
  the arm, and INCONCLUSIVE is the honest label if fewer than four markets
  settle in the window.
- **A PASS proves a naive rule on a tape, not a bot in a market.** Real quotes
  change other participants' behavior; a replay cannot see that. This is why
  the consequence of a PASS (§9) is a *live paper shadow*, never an order path.
- **A FAIL is expected from what the fourth charter showed.** Makers there
  repriced a median 10.6 s before public information. A rule that requotes
  every 30 s is picked off on every move by definition. That expectation is
  recorded here so a FAIL cannot later be called surprising, and a PASS cannot
  be called predetermined.

## 9. Decision rule and consequence

- **PASS in an arm:** a **paper-only live shadow test** for that arm becomes
  the next charter — real-time quoting decisions logged against the live book
  with no orders sent, to see whether fills the replay counted would actually
  have been there. Nothing in this charter or in a PASS opens an order path;
  the paper-only invariant and the 2026-08-20 decision stand until a separate,
  explicitly signed execution charter says otherwise.
- **FAIL in both arms:** the automated-trading question is **closed**. Five
  hypotheses, five pre-registered negatives, covering right, early, and
  counterparty. No further prediction-market trading charter is drafted
  without genuinely new information — a new data source, a new venue, or a
  new fee schedule — and the public site remains the product.
- **INCONCLUSIVE:** Arm A widens once, to markets settling by 2026-10-28, and
  reruns on 2026-10-29; Arm B widens once, backward by 30 days. A second
  inconclusive counts as FAIL for product purposes.

The result is written into this file with the evidence, at equal prominence
either way, and summarized in `docs/FINDINGS.md`.

## 10. Honest odds, recorded before the work

**Arm B:** near-certain FAIL. The fourth charter measured the mechanism
directly: informed makers reprice before the public feed. A 30-second naive
quoter is their counterparty on every lead change, exactly when being the
counterparty costs the most.

**Arm A:** the only plausible PASS in five charters, and still the less likely
outcome. Fed-decision books are thin and slow, spreads are wide, and days pass
without news — the conditions under which a patient maker can collect a spread
from impatient takers. Against that: the fee is real, the takers who arrive on
a quiet day may be the ones who know something (a speech, a data print), and
the sample is four to six markets. If it passes, it passes narrowly and on
few markets, and §9 says what that earns: a shadow test, not a bot.

Cost of the answer: a few days of build, a replay that runs in minutes, zero
dollars of principal. Arm B can be run as soon as the instrument is frozen
(its games have settled); Arm A runs on 2026-09-17, the day after the
September FOMC settlement.

## 11. Sequence and sign-off

1. Owner merges this charter (sign-off).
2. Build `atlas/making.py` + tests + runner; commit; record the freeze hash
   here.
3. Run Arm B; hold the result unread in the artifact until Arm A has run.
4. Run Arm A on 2026-09-17; write both results into this file.

- Proposed: 2026-09-08 (Claude, having verified the tape and book endpoints
  and computed no P&L).
- Freeze commit: _pending — recorded after sign-off._
- Owner signature: _pending — merging this file constitutes sign-off._
