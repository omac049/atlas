# The market-making charter (fifth hypothesis)

**Status: SIGNED — merged to `main` by the owner on 2026-09-08 (#32).** Merging
this file constituted sign-off on the theory, the instrument definition, and every
threshold below. Nothing may change after the freeze commit named in §7.

**Amendment 1 (§12) — signed by the owner on 2026-09-17.** Asked in session
what to do about Arm A, the owner chose to keep it alive and directed Claude to
merge the amendment's pull request (#78) on their behalf; the instruction is
recorded on that pull request. It changes where Arm A's single widened run reads its order
books from, because the books the charter assumed turned out not to exist. It
changes no rule, parameter, floor, criterion or consequence.

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
- Freeze commit: `383d609f739166d07df8e0ff2923ef74291bba50` (merge of #65,
  2026-09-15). The instrument was validated before this commit only on the
  excluded probe game, STL at LAD on 2026-09-03 (`docs/proof/making-probe.json`:
  29,681 prints, 657 quotes, 227 fills, net −$16.23), and on synthetic tapes in
  `tests/test_making.py`. No fill, fee or P&L was computed on any sampled
  market before the freeze.
- 2026-09-15: Arm B started from the freeze commit on `main`; its artifact is
  `docs/proof/making-result-armB.json` and its verdict stays unread until Arm A
  has run (§11.3). Arm A runs on 2026-09-17 against the markets that settle on
  2026-09-16.
- Owner signature: merged 2026-09-08 (#32).
- 2026-09-15, before the freeze: the September markets cannot satisfy §5. Five
  `KXFEDDECISION-26SEP-*` markets settle on 2026-09-16, but Atlas holds book
  snapshots for only three (H0, H26, C26), so Arm A's primary run on
  2026-09-17 is expected to read INCONCLUSIVE and widen once, per §9, to
  markets settling by 2026-10-28. To keep that widened run possible, the
  Fed-decision books are exempted from the 30-day prune until Arm A is judged
  (`PRUNE_ORDERBOOK_RETAIN_PREFIXES` in `atlas/storage.py`). This changes what is
  retained, not a §4 parameter or a §5–§6 threshold.
- 2026-09-15, 22:16 UTC: **Arm B finished** — 352 settled games replayed in
  1,145 s (the §5 floor is 100); 22 tickers left out and listed in the artifact
  (20 `UNPARSEABLE_TICKER`, 2 `EXCLUDED_PROBE_GAME`).
  - **The §11.3 hold was broken the same day, by accident.** While checking the
    finished artifact's structure before committing it, Claude printed its
    `verdict` block instead of only its keys. That is recorded here rather than
    papered over. Why it cannot colour Arm A: the rule (§4), floors (§5),
    criteria (§6), windows, and the single widening (§9) were all fixed in this
    signed charter and frozen at `383d609` before either arm ran; the runner
    applies them mechanically, and no discretionary step remains between now
    and the Arm A verdict. Because the result is now read, it is recorded at
    the prominence §9 requires instead of being held; Arm A is still written
    here on 2026-09-17 as planned.
  - **Arm B result: FAIL** — all four §6 criteria fail. Primary parameters
    (s = 2¢, q = 10, L = 1 s, Δ = 30 s): 352 markets, 227,154 contracts filled,
    net **−$4,492.52**; 90 of 352 markets positive (25.6%, ≥60% required);
    −1.98¢ per contract (≥ +0.5¢ required). Slow run (L = 5 s): net −$4,865.30,
    214,919 contracts, 79 positive (22.4%), −2.26¢ per contract. This is the
    §10 pre-registered expectation ("near-certain FAIL") for the reason the
    fourth charter measured: a 30-second quoter is the counterparty on every
    repricing, and it pays the maker fee for the privilege.
  - Artifact: `docs/proof/making-result-armB.json.gz` (`gunzip -k` restores the
    11.6 MB JSON, which git now ignores; regenerable from the cached tapes with
    `python docs/proof/run_making.py --arm B --reveal`). Its `instrument_commit`
    reads `e1f4827` (the merge of #67), not the freeze hash, because the field
    stamps `HEAD` at write time and `main` moved while the run was in flight;
    `git diff 383d609 e1f4827 -- atlas/making.py docs/proof/run_making.py` is
    empty, so the instrument that ran is byte-identical to the frozen one.
- 2026-09-17: **Arm A was not run. Its order-book data is invalid.** Found
  while checking the run's input, before any replay: no fill, fee or P&L has
  been computed for any Arm A market, under any parameters.
  - **What is wrong.** The websocket recorder (`atlas/streams/kalshi.py`,
    `atlas/orderbooks/state.py`, unchanged since the first public commit)
    reads the feed's opening snapshot under `yes`/`yes_dollars`; the feed sends
    `yes_dollars_fp`/`no_dollars_fp`, so every book starts empty. It then
    stores each `delta_fp` — a signed *change* in size — as the level's whole
    size, and deletes the level on any reduction. What it kept is a pile of
    recent size increases, not a book. Its NO side also arrives in YES prices
    (the subscription sets `use_yes_price`) and is mirrored a second time,
    which is why the stored books read as crossed.
  - **Evidence.**
    1. *Against the venue.* `KXFEDDECISION-26OCT-H0`, 2026-09-17 about 18:05
       UTC. Kalshi's REST book: 25 YES-bid levels, best 51¢ × 3,108.74; 40
       NO-bid levels, best 48¢ × 8,630 (a 52¢ YES ask); quoted 51/52¢. The
       recorder's latest state, 46 s old: 9 YES-bid levels, best 48¢ × 116; 8
       NO-side levels at 52–81¢, which it mirrors into YES asks of 19–48¢.
    2. *Opening snapshots.* All 15 examined (two markets, five weeks) hold
       zero levels on both sides.
    3. *State transitions.* Of 3,999 consecutive stored states of that market,
       2,698 differ from the one before by exactly one level and 1,301 by
       none; a level of 931 contracts vanishes on one message, another goes
       100 → 225.
    4. *As this instrument parses them* (§4: the mid of best YES bid and best
       YES ask), inside the Arm A window. September H0: 420,780 snapshots,
       97.8% crossed or locked, median "spread" −35¢, a valid two-sided book
       for 1.9% of the window, and 47,303 of 50,157 non-block prints more than
       1¢ outside the recorded spread. H26: two-sided in 19.6% of 23,071
       snapshots, valid for 6.3% of the window. C26: never two-sided in 12,893
       snapshots. Every week since 2026-08-21 reads the same (90–99% crossed).
  - **What the frozen runner would have replayed.** Its literal reading of §3
    ("any other market with both a full tape and Atlas book snapshots")
    selects 52 markets today, not three: the three September Fed markets plus
    49 tennis game-spread leftovers from an August shadow test, whose books
    are REST samples taken about every 400 s of markets that reprice in
    seconds (20 of the 49 closed before the window opens). On 2026-10-29 it
    would also have swept in the markets study phase 2's bursts have touched
    since 2026-09-15 — 15 so far, 20 seconds of books at a time. Neither is
    the continuous record §4's "latest book snapshot at or before `t`"
    presumes. The 2026-09-15 note above, which counted "book snapshots for
    only three" markets, had not looked at what the database held.
  - **Primary run: INCONCLUSIVE (§5), recorded without a replay.** The floor
    is four settled markets; the number with a valid book is zero. Not padded
    — and not run "for the record" either: a replay that quotes around the mid
    of a book crossed by 35¢ is not measuring a market maker, and a number
    like that, once written down, gets quoted.
  - **Blast radius: Arm A only.** The live pair monitor that writes these
    books has never produced a paper opportunity from them (0 rows in
    `paper_trades`, 0 "PAPER OPPORTUNITY" lines in its log: its Polymarket leg
    is the offshore venue, which publishes no book). The radar, the 90-day
    study, the four earlier charters and Arm B read REST quotes and trade
    tapes, never these snapshots.
  - **The error, stated.** "Having verified the tape and book endpoints"
    (Proposed, above) was true of Kalshi's endpoints and was never true of
    Atlas's stored snapshots. §3 and §4 were built on recorded books nobody
    had compared with the venue's, and the recorder's own tests had passed
    since the first commit against a message shape the venue does not send.
  - **What follows.** §9 already prescribes it: one widening, to markets
    settling by 2026-10-28, rerun on 2026-10-29. That rerun needs real books.
    `atlas/book_recorder.py` (#73) has been recording Kalshi's public REST
    book for all five `KXFEDDECISION-26OCT-*` markets every 5 s since
    2026-09-17T18:21:47Z, into `data/making/books.sqlite3`. Whether the rerun
    may read them is a change to §3's data source after the freeze, so it is
    an amendment for the owner to sign, proposed separately. Until it is
    signed the recorder only collects; it decides nothing.
- 2026-09-17, 19:04 UTC: **Amendment 1 (§12) signed.** Asked in session "What do
  you want to do about Arm A?", the owner chose *Keep it alive*: turn the
  recorder back on and merge #78 on their behalf. Merged by Claude at that
  instruction, which is recorded on the pull request. (The wording at the top
  of this file, written before the merge, said "the owner's merge"; it now says
  what happened.)
  - **The recorder's first outage, and how the data shows it.** Claude's
    report to the owner ended with the command that removes the recorder,
    offered "in case you ever want to"; the desktop app renders such a block
    with a Run button, and it was clicked. The recorder was off from
    2026-09-17T18:59:27Z to 19:03:37Z. On restart it wrote an unknown-book
    marker for each of the five markets at 65 s after that market's last row
    (18:59:53Z–19:00:32Z) — the first real use of the rule in §12.1. Nothing
    was lost that the replay would have used: it places no quotes across a
    marker.
  - **The invalid stream rows were deleted** the same hour, at the owner's
    instruction (they were half the database): 944,102 rows, after a verified
    backup (`data/backups/atlas-pre-cleanup-20260917.sqlite3.gz`); the file
    went from 3.06 GB to 1.05 GB, `integrity_check` ok, every other table
    unchanged. 2,000 consecutive rows are kept as evidence for the note above
    in `docs/proof/stream-books-sample-2026-09-17.jsonl.gz`: 1,500 of
    `KXFEDDECISION-26OCT-H0` from a reconnect (the first row is sequence 1
    with zero levels on both sides) and 500 of `KXFEDDECISION-26SEP-H0` from
    inside the Arm A window.
  - The repaired websocket recorder (#76) was checked against Kalshi's REST
    book after fifteen minutes of live deltas: 9 of 9 watched markets matched
    on best bid and ask (`KXFEDDECISION-26OCT-H0`: 51/52¢ both ways). It is not
    Arm A's data source — §12.1 is — and nothing reads it for this charter.

## 12. Amendment 1 — the widened Arm A run reads the venue's own books

*Proposed 2026-09-17 by Claude, who knows Arm B's result (FAIL) and has computed
no fill, fee or P&L for any Arm A market. In force only by the owner's merge.*

**Why an amendment is needed at all.** §9 already says what follows an
INCONCLUSIVE Arm A: one widening, to markets settling by 2026-10-28, rerun on
2026-10-29. But the books §3 and §4 assumed were never books (the 2026-09-17
note in §11), and the same recorder would have gone on producing them through
October. Run as written, the widened replay would be as meaningless as the
primary one. Reading different books is a change to §3's data source after
the freeze, so it needs the signature this file says it needs.

**What changes — the data source, and what that forces.**

1. **Books.** `data/making/books.sqlite3`, written by `atlas/book_recorder.py`
   (#73, #75) from 2026-09-17T18:21:47Z: Kalshi's public REST order book — the
   venue's own statement of it — read every 5 s, the best ten levels a side, a
   row on every change and at least every 60 s. Any stretch of more than 65 s
   without a successful read is written down as an unknown-book marker (an
   empty book), so an outage costs the replay its quotes for that stretch and
   can never make the rule trade against a stale book (§4: "If no reference
   exists, no quotes are placed").
2. **Sample.** Every `KXFEDDECISION-26OCT-*` market — five: C25, C26, H0, H25,
   H26 — which is what §3 meant by "every Kalshi Fed-decision market" of a
   meeting. The September markets drop out: no valid book exists for them and
   none can be made. The tennis leftovers and the phase-2 bursts are not in
   that database and are not Arm A.
3. **Window.** Each market from its first recorded book to its close on
   2026-10-28 — the runner's existing rule, `max(first snapshot, 2026-08-21)`,
   unchanged. About 41 days, which is still "multi-week" (§3).
4. **The command, fixed now:**
   `python docs/proof/run_making.py --arm A --settle-by 2026-10-28 --db data/making/books.sqlite3`
   with `atlas/making.py` and `docs/proof/run_making.py` byte-identical to the
   freeze commit (`git diff 383d609 HEAD -- atlas/making.py
   docs/proof/run_making.py` must print nothing; the artifact's
   `instrument_commit` stamps `HEAD`, as Arm B's did).
5. **Tapes.** Kalshi serves prints for about six weeks and this window is 41
   days long, so the October prints are also archived on 2026-10-13 with the
   runner's own `fetch_tape`. If the final fetch starts later than the archive
   does, the missing front is restored from the archive into the runner's
   cache file before the run, and the result says so. Prints are immutable
   facts; this restores data and selects nothing.

**What does not change.** The rule and every parameter in §4. The floors in
§5. The four criteria in §6. The consequences in §9 — including that this is
the one widening Arm A gets: INCONCLUSIVE on 2026-10-29 counts as FAIL for
product purposes. If this amendment is not signed before the markets close,
the widened run has no valid input and that is the outcome recorded.

**Limits of the repaired measurement, named before it is taken.**

- *A five-second poll is a coarser clock than a change-driven feed.* The book
  the rule sees at `t` can be about five seconds old. At Δ = 30 s that is
  small, and criterion 4 (L = 5 s) already asks whether the result survives a
  clock that slow. The Δ = 5 s column of the sensitivity grid is the one place
  it bites; the grid is never decisive (§4).
- *Ten levels a side.* The rule quotes within 5¢ of the mid; ten levels always
  cover it. Depth beyond that is not recorded.
- *Coverage is audited before the replay and reported beside the verdict,
  never used as a filter:* per market, the share of the window not under an
  unknown-book marker.

**Known today about the sample's composition (not about any result).** Two of
the five markets quote mid-book (H0 51/52¢, H25 47/48¢); three sit at the floor
(C25 0/1¢, C26 0/1¢, H26 1/2¢). A market with no YES bid has no mid, so the
rule places no quotes there and its net is exactly $0.00 — which criterion 2
counts as "not positive". If C25 and C26 stay that way, a PASS needs every
other market positive (3 of 5 = 60%). That is the arithmetic this charter
froze, stated here so that nobody discovers it on 2026-10-29. It is not
adjusted.

**After the run.** Both Arm A records (2026-09-17: INCONCLUSIVE, not replayed;
2026-10-29: the result) go into §11 and `docs/FINDINGS.md`; `com.atlas.books`
is removed; the Fed-decision prune exemption (#64) is removed.
