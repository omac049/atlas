# The 90-day opportunity study

**Question:** after real fees, latency, liquidity, and settlement risk, do verified
cross-venue opportunities occur often enough, survive long enough, and carry enough
size to justify building execution — or is Atlas's durable value the contract
intelligence itself?

Adopted 2026-08-19 from the external viability review's protocol. The study answers
with a measured, timestamped record — not opinion. **Paper-only throughout; nothing
in this study touches or builds an execution path.**

- **Start:** 2026-08-19 (observations retroactive to 2026-08-12 are included and
  labeled by their recorded fee model)
- **End / decision date:** 2026-11-17 (day 90)
- **Weekly artifact:** `data/study/study-report-YYYYMMDD.json`, written by
  `atlas gaps study` (scheduled Mondays 07:00 via `com.atlas.study`). Reports are
  regenerable bit-for-bit from `gap_observations`; the dated files exist so the
  day-90 decision can cite a trail that was written down *before* the outcome was
  known.

## Frozen-rules policy

The deterministic verifier and normalizers are **frozen for measurement purposes**
for the study's duration. A rule change during the study requires (1) the existing
owner-sign-off gate, and (2) an amendment note appended to this file stating what
changed and which metrics it can move. Bug fixes that cannot change any verdict
(logging, performance) are exempt. The study began at commit `47adf0c`.

## Metric definitions (must match `atlas/study.py`)

| Metric | Definition |
| --- | --- |
| Opportunity | a (Kalshi market, Polymarket market, UTC day) with ≥1 executable observation — a gap persisting across many 5-minute sweeps in one day counts **once** |
| Executable observation | best locked basket costs < $1 after venue-published taker fees at displayed top-of-book prices |
| Survival run | consecutive executable observations of one pair ≤15 min apart; duration = last − first; isolated observations are "single-sweep-only" |
| Venue-text-only opportunity | an opportunity whose verification refusal consists **solely** of codes a venue text revision would clear (`SETTLEMENT_GUARANTEE_UNKNOWN`, `SETTLEMENT_POLICY_MISMATCH`, `REVISION_POLICY_MISMATCH`) — the precursor of a *verified* opportunity |
| Size | Kalshi displayed size on the best basket (the binding leg; Polymarket depth is not modeled yet) |
| Settlement horizon | days from the observation until the **later** leg's published anchor (`resolution_time`, else `close_time`) — a locked basket frees capital only when both legs settle, so the later anchor is the honest lock-up |
| Annotated observation | a row carrying a `settlement_timing` key at all, i.e. recorded after the annotation shipped (2026-08-19T23:25Z). Distinct from a row whose venues published no anchor |

Fee model: venue-published schedules, encoded 2026-08-19 (Kalshi quadratic
ceil-per-contract; Polymarket per-market `feeSchedule`, conservative max-rate
fallback). Rows recorded before that date carry the legacy flat 2¢ buffer and are
counted separately in `fee_model_rows` — the two populations are never silently
mixed in a per-week comparison.

Settlement-timing curve: the same separation rule applies, for the same reason.
`unannotated_observations` counts rows recorded before the annotation existed;
`observations_without_horizon` counts annotated rows whose venues published no
usable anchor. Pooling them would permanently understate coverage, because the
pre-annotation block never shrinks while the study runs. The four counts
(`with_horizon`, `without_horizon`, `unannotated`, `after_horizon`) reconcile
exactly to `observations_reviewed`.

**Asymmetry split — closed 2026-08-20, was previously unmeasurable.** The
asymmetric-vs-symmetric gap comparison exists to test whether a settlement-timing
asymmetry is compensated as carry or is genuine mispricing. Until 2026-08-20 no
pair the gap radar watched could carry the tag: its scope was the frozen macro
families (FOMC, fed funds level, CPI×4, payrolls, core PCE, GDP, U3, ISM), all of
which settle on a scheduled BLS/BEA/Fed release and publish no early-determination
clause. The chamber-control family was added that day (see the amendment below),
giving the split an eligible population for the first time. The report still emits
`asymmetry_measured` and `asymmetry_blind_spot` so that if the population ever
empties again — the 2026 contracts settle, and nothing replaces them — a null
`asymmetric_median_gap` beside a populated symmetric one is never read as "we
measured asymmetry and it did not matter".

## Scope

The go/no-go rate is computed on the family scope **frozen at STUDY_START**.
Families added to radar scope afterwards are listed in
`POST_START_SCOPE_FAMILIES` (`atlas/study.py`), measured in full, and reported
under `post_start_scope` — held out of `distinct_opportunities`,
`venue_text_only_opportunities_total`, the two rates, `meets_go_threshold`, and
the weekly table. They ARE included in the settlement-timing curve, which is not
a go/no-go input and which exists precisely to compare families.

Rationale: the go/no-go asks whether opportunities occur often enough. If the
instrument widens mid-study, the rate rises because we changed what we look at,
not because the market changed, and week 1–2 stops being comparable to week 3+.
Quarantining keeps the headline honest while still surfacing the new family, so
the day-90 reader can see both and combine them deliberately.

## Phases

**Phase 1 — days 1–30 (to 2026-09-17): opportunity frequency.**
Runs automatically: the monitor sweeps every 5 minutes across the frozen macro
families (FOMC, fed funds level, CPI×4, payrolls, core PCE, GDP, U3, ISM). The
weekly report delivers opportunities/week, survival, size, and the venue-text-only
count. September releases land inside this window (jobs 9/4, CPI 9/11, FOMC 9/16).

**Phase 2 — days 31–60 (by 2026-09-18): latency-adjusted shadow execution.**
To build before day 31: replay each executable observation under simulated
execution delays of 250 ms, 500 ms, 1 s, and 2 s using the next recorded quotes;
require both legs to remain fillable; measure legging/partial-fill exposure; report
latency-adjusted edge and **return on locked capital until settlement**, not just
profit per basket. (Design note: the current 5-minute sweep cannot resolve
sub-second decay — phase 2 needs burst sampling around detected gaps, which is an
instrumentation addition, not a rule change.)

**Phase 3 — days 61–90: willingness to pay (owner-led).**
Show the verified-alert view to 5–10 prediction-market traders/market makers. Ask
whether it helped them find a missed market, avoid a false equivalence, judge
settlement risk faster, or act on a spread. Atlas's role: a shareable weekly report
and the frontier watch; the conversations are the owner's.

## Decision rule at day 90 (adopted from the review)

**Continue toward execution** only if all of:
- ≥10 verified (venue-text-only, both-legs-sized) opportunities per 30 days
- positive edge surviving conservative fees **and** phase-2 latency adjustment
- executable size that matters (not pennies on 3-contract books)
- low frequency of one-leg-only fills in phase-2 simulation
- ≥3 users who would pay for alerts or data

**Pivot fully to contract intelligence** if opportunities are rare or evaporate
after costs while the settlement-risk analysis is what users engage with.

The go/no-go inputs are computed every week by `atlas gaps study`; the day-90
decision is the owner's.

**Status as of 2026-08-20 (day 2) — the owner has already ruled on execution.**
Three of the five criteria are now measurable and two of them fail. Return on
locked capital reads **3.38% annualized** overall and **1.66%** on the tradeable
venue; median basket notional is **$459**. The tradeable-venue evidence that
matters is thin: **36 Kalshi x Polymarket-US twin pairs, 2 executable, both GDP
Q3 at under a cent**. A hand-run snapshot across FOMC Sep/Oct, CPI YoY, and
unemployment the same day found **27 tradeable pairs and 0 executable**, with
baskets costing $1.00-$1.02 — the two US venues are arbitrage-linked and agree
on price.

On that evidence the owner **dropped the execution track and pivoted to contract
intelligence** (2026-08-20). The paper-only invariant therefore stands
indefinitely, not provisionally: no promotion gate is being built, and the
staged path in `README.md` is dormant rather than pending. The study continues
because it is now measuring the right venue for the first time, and because the
near-dated release windows (jobs 2026-09-04, CPI 2026-09-11, FOMC 2026-09-16)
are the honest test of whether a tradeable gap ever opens at all.

## Amendments

- **2026-08-20 — Polymarket US added to radar scope, and the go/no-go learned to
  fail (measurement change; verifier and normalizers byte-unchanged).** This is
  the largest amendment the study will carry. It is recorded in full because it
  reverses the headline.
  - **The finding that forced it:** every one of the first 18,650 radar
    observations — and all 1,490 "executable" ones — priced a **Polymarket
    Global** leg. That venue is offshore, publishes no order book, and
    `atlas/venues/polymarket_global.py` states in its own module docstring that
    its markets "can never reach shadow, approval, or paper-trading paths that
    require executable prices". The study was measuring the spread between a
    venue that can be traded and one that cannot, which is why those gaps
    survive for hours: nobody can close them.
  - **What changed (scope):** `gaps_scan` now also loads Polymarket **US**
    (`GAP_RADAR_PMUS_CATEGORIES = ("macro",)`), the venue a US account can
    actually reach. It publishes a two-sided book, so US legs are sized from
    real displayed depth instead of assumed at the quote. Every observation now
    records `polymarket_venue` and `tradeable_venue_pair`.
  - **What changed (metrics):**
    - `return_on_locked_capital` — the phase-2 deliverable, pulled forward from
      day 31. Edge divided by the later leg's settlement horizon. The horizon
      was already computed and bucketed; nothing divided by it.
    - `distinct_pairs` alongside `distinct_opportunities`, because the latter
      counts pair-**days**: "26 opportunities" came from **11 distinct pairs**,
      three of which supply most of the total.
    - `tradeable` — the same metrics restricted to Polymarket US rows.
    - `meets_go_threshold` is now an **object of named sub-tests**, not a
      boolean: frequency, return on locked capital, basket size, and tradeable-
      venue evidence. A sub-test with no eligible population reports `null` and
      does **not** pass — an untested condition is not a satisfied one. The
      former boolean is preserved as `meets_frequency_threshold`.
    - The `$2k` paper meter no longer stakes an opportunity whose displayed
      depth is unknown. It previously ran those **uncapped** at the full 5%
      stake — 8 of 26 opportunities, 35% of recorded profit, on assumed size.
      Skipped opportunities are counted (`unsized_opportunities_skipped`), never
      silently dropped.
  - **Which metrics it moves, measured the same day:** `meets_go_threshold`
    flips **GO → NO-GO** on unchanged data. Frequency still passes (73.3/30d vs
    10); return on locked capital reads **3.38% annualized** against a 15%
    hurdle, and median basket notional **$459** against a $500 floor. The
    tradeable subset annualizes at **1.66%**. The paper meter reads **+$19.29**
    over nine days rather than +$29.74.
  - **Provisional, pending owner sign-off:** the two new thresholds
    (`GO_MIN_ANNUALIZED_RETURN_ON_LOCKED_CAPITAL = 0.15`,
    `GO_MIN_MEDIAN_BASKET_NOTIONAL_USD = 500`) are placeholders chosen as
    roughly the risk-free rate plus a premium for settlement-divergence,
    legging, and venue risk, and the smallest basket worth an operator's
    attention. They are reported inside the decision under
    `provisional_pending_owner_signoff`. **Record a decision before day 90
    treats either as authoritative.**
  - **Deliberately NOT done — `politics` is excluded from Polymarket US scope.**
    The gateway's joint "2026 Midterms: Balance of Power" contracts
    (`paccc-balpow-*`) settle on both chambers at once, yet normalize to the
    single-chamber subject `us_house_control|2026` with a **non-null and
    incorrect** affirmative outcome: `...-rhou-dsen` ("R House, D Senate")
    reports `democratic_party`. The categorical-twin guard recorded in the
    amendment below relies on joint contracts carrying a *null* outcome — true
    on Gamma, false here — so a Kalshi "Will Democrats win the House" leg paired
    straight through and the radar printed phantom gaps of **31.5c and 79.8c**.
    Three such observations were recorded and **deleted**; they postdate the
    last published report, so no study artifact contains them. Both the defect
    and the scope containment are pinned by test. Re-admitting `politics`
    requires fixing the election normalizer's chamber attribution first — a
    frozen-path change needing its own sign-off.

- **2026-08-20 — settlement-timing curve reporting (no rule change, no go/no-go
  metric moved).** The deterministic verifier and normalizers are byte-unchanged;
  this amendment is recorded because the *shape* of a reported metric changed
  mid-study, and the day-90 trail should show when.
  - **What changed:** `settlement_timing_curve` now separates
    `unannotated_observations` (recorded before the annotation shipped) from
    `observations_without_horizon` (annotated, but the venues published no
    anchor); adds `annotated_observations` / `annotated_pairs`; and adds
    `asymmetry_measured` + `asymmetry_blind_spot` so an empty asymmetry split
    names its cause instead of presenting a null median as a finding.
  - **Which metrics it can move:** none of the go/no-go inputs. Opportunity
    counts, rates, `meets_go_threshold`, survival, and size arithmetic are
    untouched. Within the curve, the previously reported
    `observations_without_horizon` of 15,527 was the pre-annotation block; it is
    now 0, with those rows counted as `unannotated_observations`. Horizon
    coverage since the annotation shipped is 100% (2,856 of 2,856), which the
    old pooled count hid.
  - **Why:** the same rule the charter already applies to `fee_model_rows` —
    two populations that mean different things are never silently mixed.

- **2026-08-20 — chamber-control family added to gap-radar scope (measurement
  scope change; quarantined from go/no-go; verifier unchanged).**
  - **What changed:** `match_twin_shapes` learned a second twin kind. It was a
    threshold-only matcher, so *categorical* contracts — one party, one chamber,
    one cycle, no number anywhere — could never pair, no matter what scope it was
    given. Categorical twins now pair on identical subject, action, scope, and a
    **non-null, equal** affirmative outcome, with neither leg publishing a
    threshold. A threshold contract is never compared to a categorical one.
    Radar scope gained Kalshi `CONTROLH`/`CONTROLS` and Polymarket tag `144`.
  - **Deliberately NOT done:** opposing parties are not treated as an inverse
    shape. "Democrats win" and "Republicans win" are not a published complement —
    ties and third outcomes exist, which is why both venues publish tiebreak
    clauses. Treating them as complements would be inference.
  - **Guard:** Polymarket's "2026 Balance of Power: D Senate, D House" joint
    contracts normalize to the *house-control* subject with no affirmative
    outcome. Requiring a non-null, equal outcome keeps a joint Senate+House bet
    from pairing with a House-only bet. Pinned by test.
  - **Which metrics it can move:** none of the go/no-go inputs, because the two
    families are quarantined (see **Scope** above). Verified on the live catalogs
    the same day: the frozen-scope headline was byte-identical before and after
    (24 opportunities, 66.7 verified/30d, `meets_go_threshold: true`). What it
    moves: `post_start_scope` (new), and the settlement-timing curve, which went
    from `asymmetry_measured: false` to 4 asymmetric pairs against 3,043
    symmetric observations.
  - **Why it was worth a mid-study change:** without it, settlement-timing
    asymmetry — one of the five day-90 decision inputs — could never be tested at
    all. The family also carries the first executable size that is not pennies on
    a thin book: median 163,706 Kalshi contracts against 474 for the macro
    families, which speaks directly to the "executable size that matters"
    criterion. Note the Polymarket leg publishes no displayed size, so that depth
    is one-legged; `polymarket_fill_assumed_at_quote` still applies.
  - **Caution, not opportunity:** these pairs verify `REVIEW_REQUIRED` and must
    stay that way. Kalshi may settle on a media-call consensus months before the
    Polymarket twin's official sources resolve, so a "locked" basket is not
    actually locked — that risk is what the curve is measuring, not a gap to act
    on.
