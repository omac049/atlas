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

Fee model: venue-published schedules, encoded 2026-08-19 (Kalshi quadratic
ceil-per-contract; Polymarket per-market `feeSchedule`, conservative max-rate
fallback). Rows recorded before that date carry the legacy flat 2¢ buffer and are
counted separately in `fee_model_rows` — the two populations are never silently
mixed in a per-week comparison.

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

## Amendments

- *(none yet)*
