# The repricing-lag charter (fourth hypothesis)

**Status: PROPOSED — awaiting owner sign-off.** Merging this file to `main`
constitutes sign-off on the theory, the instrument definition, and every
threshold below. Nothing may change after the freeze commit named in §6.

**Lineage.** Hypotheses one through three tested, in order: cross-venue
arbitrage (dead — dust depth, months of lock-up), machinery gaps predicting
disputes (FAIL — no separation), predicate ambiguity predicting disputes
(abandoned at the gate). This fourth hypothesis is the one the owner actually
described at the outset and the machine never tested: **in a live baseball
game, the market's price lags the game.** The owner's observation was that a
favorite pulling ahead is "already a profit"; the honest reframe is that the
profit is symmetric and fee-eaten — *unless* the price is slow to move, in
which case, for a window of seconds, the price is simply wrong and anyone with
the public play-by-play feed knows it.

**Paper-only, without exception.** This is a retrospective measurement over
published trade tapes. It places nothing, simulates no orders, and reads no
credentials. A PROVEN result reopens nothing automatically (§8).

## 1. The theory, stated so it can fail

> After a lead-changing play in an MLB game, Kalshi's moneyline market
> continues to trade at pre-play prices for long enough, and in enough size,
> that a participant holding only the public MLB play-by-play feed could have
> taken the stale price — and the stale-price gap exceeds the taker fee.

Three things must all be true: the lag is **long** (a human could act), the
stale liquidity is **real** (contracts actually traded at the old price, not
merely quoted), and the gap is **larger than fees**. Any one failing kills the
edge, whatever the other two show.

## 2. Why this is a different question from the last three

The first three hypotheses were about *whether a price is right*. This one is
about *when* it becomes right. The crowd can be perfectly calibrated and still
be slow. Speed is the only version of the owner's original idea that survives
the symmetry argument, and it is the only one where being *with* the crowd —
just earlier — could pay.

## 3. Data — both sources verified live 2026-09-04, exact fields named

**Kalshi trade tape.** `GET /trade-api/v2/markets/trades?ticker=…`, paginated
by cursor. Each row carries `created_time` (exchange match time, microsecond
precision), `yes_price_dollars`, `count_fp` (contracts), `taker_side`, and
`is_block_trade`. Markets: series `KXMLBGAME`, one contract per team per game
(e.g. `KXMLBGAME-26SEP032210STLLAD-STL`, "St. Louis wins"; the ticker encodes
date, first pitch UTC, and both clubs). One probed game carried **24,346
trades**. Public endpoint, no credentials.

**MLB play-by-play.** `statsapi.mlb.com/api/v1.1/game/{gamePk}/feed/live`,
public, no credentials. Every plate appearance carries `about.startTime` and
`about.endTime` (millisecond precision) and the running `result.homeScore` /
`result.awayScore`. Score-changing and lead-changing plays are derived
deterministically from consecutive score pairs — no interpretation. Games are
joined to Kalshi tickers by date and the two club codes parsed from the
ticker.

**Sample.** Every settled `KXMLBGAME` market with first pitch between
**2026-08-15 and 2026-09-10** inclusive (fixed now; not extended after data is
seen), joined to its MLB game. Expected: ~350 games, several hundred
lead-changing plays.

## 4. The instrument — measured per lead-changing play

For each play whose result changes which team leads (ties count as a change
from either side, and a change to a tie counts), with `T0 = about.endTime`:

- **Direction.** The contract for the team that benefits is expected to rise.
  Both team contracts are analyzed; the benefiting side is the one measured.
- **Pre-play price** `P0`: the volume-weighted `yes_price_dollars` of the last
  20 trades (non-block) before `T0` on the benefiting contract.
- **Repricing lag** `L`: seconds from `T0` to the first non-block trade at a
  price at least **5¢** above `P0`. Undefined (recorded as `NO_REPRICE`) if no
  such trade prints within 300s.
- **Stale fills** `S`: total `count_fp` of non-block trades on the benefiting
  contract printed in the window **[5s, 60s]** after `T0` at a price within
  **2¢** of `P0`. The 5-second floor is the charter's estimate of the fastest
  realistic human reaction with the public feed (feed delivery plus a decision
  plus an order); bots are faster and are not what this test is about.
- **Post-play price** `P1`: the volume-weighted price of the first 20
  non-block trades printed after `T0 + 60s`.
- **Stale gap net of fee** `G = P1 − P0 − fee(P0)` where `fee(p)` is Kalshi's
  published taker schedule, `ceil(0.07 · p · (1 − p))` per contract — the same
  conservative per-contract ceiling `atlas/gap_radar.py` already uses.

Every per-play row is written to the artifact with its timestamps and the
trade ids that determined it, so any single number can be traced back to the
tape.

## 5. Pre-registered pass criteria — all three must hold

On a sample of at least **100 lead-changing plays** across at least **50
games**:

1. **Lag is long:** the median repricing lag `L` is **≥ 5 seconds**, with
   `NO_REPRICE` plays counted at 300s (they favour the theory; capping them
   prevents them from dominating the median).
2. **Stale liquidity is real:** in **≥ 50%** of plays, stale fills `S` total
   **≥ 20 contracts** — someone actually traded at the old price in the human
   window, on at least half of all lead changes.
3. **The gap beats fees:** the median stale gap `G` is **≥ 3¢**.

**FAIL:** any criterion unmet on an adequate sample. **INCONCLUSIVE:** fewer
than 100 plays or 50 games in the window — reported, never padded by widening
the window after the fact.

## 6. Freeze and blindness

The instrument (`atlas/repricing.py` + tests) is committed **before** the
sample is pulled, and that commit's hash is recorded here as the freeze
commit. The date window in §3 and every number in §4–§5 are fixed by this
file. No parameter — the 5¢ move, the 2¢ stale band, the 5s floor, the 60s
window, the 20-trade VWAP, the 300s cap — changes after the freeze.

There is no development set this time: the author has not looked at any
lead-change tape. The single probed game (STL @ LAD, 2026-09-03) was used only
to confirm the endpoints exist and is **excluded from the sample**.

## 7. What would NOT count as proof — and the confounds, named first

- **Trades are a lower bound on stale liquidity, and the bound cuts the
  conservative way.** A stale trade proves the old price was takeable; the
  absence of trades does not prove the book had moved. Criterion 2 can only
  under-count the edge.
- **MLB's timestamp is itself late.** `endTime` is when the official stringer
  finalized the play, seconds after the ball landed. A market maker on a
  faster feed can reprice *before* `T0`, producing a **negative** lag. Negative
  lags are recorded as-is and counted against the theory (they pull the median
  down), never clipped to zero. This bias runs against the hypothesis.
- **Block trades are excluded** from every metric; they are negotiated, not
  taken from the book.
- **Score changes that do not change the leader** are measured and reported
  but are not part of the pass criteria: the theory is about the plays that
  should move the price most.
- **A PROVEN result proves the tape, not a human.** That stale prints existed
  in the 5–60s window says a participant *could* have taken them; it does not
  say one *would* have won the race for them against every other participant
  watching the same feed.

## 8. Decision rule and consequence

- **PROVEN:** all three criteria on an adequate sample. Consequence: a
  **paper-only live shadow test** becomes the next charter — watch real games
  with the public feed and record, in real time, what a rule-following
  participant would have submitted and whether the price was still there when
  the order would have landed. Nothing in this charter, and nothing in a
  PROVEN result, opens an order path: Atlas's paper-only invariant and the
  owner's 2026-08-20 decision to drop execution both stand until a separate,
  explicitly signed execution charter says otherwise.
- **DISPROVEN:** any criterion fails on an adequate sample. The owner's
  original idea has then been tested in its strongest form and found wanting,
  and the honest pattern is four hypotheses, four negatives.
- **INCONCLUSIVE:** widen the date window **once**, backward only, by 30 days,
  and rerun; a second inconclusive is treated as DISPROVEN for product
  purposes.

The result is written into this file with the evidence, at the same
prominence either way.

## 9. Honest odds, recorded before the work

Market makers on Kalshi's sports books run automated quoting off licensed
feeds that are faster than the public MLB stringer. The prior is that the
median lag is **negative or under two seconds** and criterion 1 fails outright.
Against that: Kalshi's in-game MLB books are thinner than a sportsbook's, and
thin books reprice in jumps, sometimes late. This is the cheapest of the four
hypotheses to test — one to two days, entirely retrospective, no credentials —
and it is the one that actually matches what the owner saw. That is enough to
justify running it once, properly, and then believing the answer.

## 10. Sequence and sign-off

1. Owner merges this charter (sign-off).
2. Build `atlas/repricing.py` + tests; commit; record the freeze hash here.
3. Pull the sample, run, write the result into this file.

- Proposed: 2026-09-04 (Claude, having probed one game to confirm the feeds
  and nothing else).
- Freeze commit: _pending_.
- Owner signature: _pending — merging this file constitutes sign-off._
