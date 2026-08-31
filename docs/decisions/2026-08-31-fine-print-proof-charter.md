# The fine-print proof charter

**Status: PROPOSED — awaiting owner sign-off.** The Test C predictions below
become binding the moment this file merges to `main`; git's history is the
timestamp and the hash. Predictions may be amended only BEFORE the release they
concern, and every amendment must say what changed and why.

**Owner decision this charter serves (2026-08-25):** no product funnel — no
landing page, no waitlist, no paid tier — is built until the theory below is
proven. If the theory fails its tests, Atlas stays a research instrument and
that outcome is published with the same prominence a pass would have received.

## The theory, stated so it can fail

> Markets with low Settlement Clarity Scores produce more settlement trouble —
> disputed resolutions, surprise outcomes, late settlements — than well-graded
> markets in the same category.

If this is false, the grade is trivia. Three tests, each with thresholds fixed
here, before data collection, so no result can be argued into a pass after the
fact.

### What would NOT count as proof (recorded up front)

- Low grades alone. Most of both catalogs grades D; the claim is *separation*
  from a control population, never absolute levels.
- Agreement with Atlas's own labels. Approved pairs required guarantee-complete
  text and the grade is built partly from guarantee codes — testing against
  them is the machine confirming itself. Ground truth must come from OUTSIDE
  the pipeline: public disputes, venue timestamps, real September outcomes.
- A quiet September. Test C's conditional predictions fire only if their
  triggers occur; a month with no triggers is a null result, not a pass.

## Test A — retrodiction on public controversies

**Corpus:** at least 15 publicly documented resolution disputes across Kalshi
and Polymarket (news coverage, official venue statements, or well-documented
community disputes — each with a citable source). **Control:** for each
disputed market, 3 settled, undisputed markets from the same venue and
category, selected by a rule written down before grading (nearest close-times),
never by hand.

**Graded blind:** the grader runs on rules text only; whoever assembles the
corpus records the dispute BEFORE seeing any grade.

**Pre-registered pass criteria (both must hold):**

1. The disputed group's **median clarity score is at least 10 points below**
   its matched control group's median.
2. At least **60% of disputed markets** carry one or more findings from the
   pre-named trouble class — `DISCRETIONARY_FAIR_PRICE_SETTLEMENT`,
   `NO_EXPLICIT_EXCEPTION_FALLBACK`, `MISSING_AUTHORITATIVE_SOURCE`,
   `CONFLICTING_AUTHORITATIVE_SOURCE`, `UNPARSED_SETTLEMENT_POLICY` — at a
   rate at least **2x** the control group's.

**Fail:** criteria unmet with a corpus of ≥15. **Inconclusive:** fewer than 15
citable disputes can be assembled; the shortfall is reported, never papered
over with weaker cases.

## Test B — settlement delay vs. grade

**Data:** a bounded fetch of ≥200 settled markets carrying BOTH a stated
resolution/close time and an actual settlement timestamp (the terminal-evidence
machinery in `atlas/backfill.py` already reads these). Delay is not an input to
any grade, so the test is non-circular.

**Pre-registered pass criterion:** markets carrying
`NO_EXPLICIT_EXCEPTION_FALLBACK` or `DISCRETIONARY_FAIR_PRICE_SETTLEMENT`
settle **more than 24 hours late at ≥2x the rate** of same-venue markets
carrying neither.

**Fail:** the ratio is below 2x. **Inconclusive:** fewer than 200 qualifying
markets, or fewer than 20 in either arm.

## Test C — pre-registered September predictions

Grades recorded 2026-08-31 with clarity scoring v1.1; the finding codes below
are quoted from live output, not summarized. Each prediction names its trigger.
**A prediction whose trigger never fires scores as NULL — neither hit nor
miss.** An unpredicted settlement dispute on a watched market counts AGAINST
the instrument and is recorded as a miss.

### Sep 4 — August jobs report (payrolls + unemployment)

| Market | Grade | Trouble finding |
|---|---|---|
| `kalshi:KXPAYROLLS-26AUG-T50000` | D (45) | `FAMILY_POLICY_INCOMPLETE` — no revision rule, no missing-release branch |
| `kalshi:KXU3-26AUG-T4.2` | F (35) | `FAMILY_POLICY_INCOMPLETE` — same gaps |
| `polymarket_us:nfpc-...-gt50k` | B (80) | publishes revision + missing-data policy; lacks only cancellation terms |
| `polymarket_us:urc-...-gt4pt2pct` | C (60) | `FAMILY_POLICY_INCOMPLETE` |

- **C1 (conditional).** Trigger: BLS delays the Employment Situation release
  past the markets' stated resolution window (as in a government shutdown).
  Prediction: the Polymarket US legs settle by their published previous-month
  fallback while the Kalshi legs — which publish no missing-release branch —
  settle late, by unpublished discretion, or divergently from PM-US.
- **C2 (conditional).** Trigger: BLS revises the August print between first
  release and any leg's settlement. Prediction: PM-US settles on first release
  per its published `revision=first_official_release`; Kalshi's behavior is
  unpredictable from its text — any Kalshi settlement matching a revised
  figure while PM-US used the first print is a hit for C2.
- **C3 (unconditional).** Absent both triggers, all legs settle within 24h of
  the print, consistently — clean-settlement nulls are expected and recorded.

### Sep 11 — August CPI (YoY)

| Market | Grade | Trouble finding |
|---|---|---|
| `kalshi:KXCPIYOY-26AUG-T3.0` | F (35) | `FAMILY_POLICY_INCOMPLETE` — no terminal missing-data fallback |
| `polymarket_us:cpic-...-gt3pt0pct` | C (60) | `CONFLICTING_AUTHORITATIVE_SOURCE` — names more than one source |

- **C4 (conditional).** Trigger: BLS delays the CPI release past the
  resolution window. Prediction: PM-US settles by its published
  `missing=first_within_3m_else_previous_month` branch; Kalshi, publishing no
  terminal fallback, settles late or divergently. (This is the same divergence
  the approval frontier has watched since August — pre-registering it makes
  the watching falsifiable.)
- **C5 (conditional).** Trigger: the two sources PM-US names for CPI disagree
  at settlement time. Prediction: a delayed or disputed PM-US settlement —
  this is the first `CONFLICTING_AUTHORITATIVE_SOURCE` finding with a live
  test attached.

### Sep 16 — September FOMC decision

| Market | Grade | Note |
|---|---|---|
| `kalshi:KXFEDDECISION-26SEP-H0` | B (85) | best-graded contract on either venue; lacks only a revision rule |
| `polymarket_us:rdc-...-nochng` | D (45) | `FAMILY_POLICY_INCOMPLETE` + the away-from-zero rounding clause |

- **C6 (unconditional).** The three live-path `APPROVED_EQUIVALENT` validation
  cases settling this day (`KXFEDDECISION-26SEP-{H26,H0,C26}` ×
  `polymarket_global`) reconcile `CONFIRMED` — both venues terminal, same
  economic outcome — within 72h of the decision. This tests the deterministic
  verifier and the revived label loop; a `DIVERGED` here is the loudest
  possible falsification and must be published as such.
- **C7 (conditional).** Trigger: the Fed moves by an amount not equal to a
  listed bucket (e.g., an intermeeting or non-25bps move interacting with
  PM-US's away-from-zero rounding). Prediction: the PM-US bucket family and
  Kalshi's settle divergently — the rounding divergence the verifier has
  refused to paper over since 2026-08-14.

## Decision rule

- **Theory PROVEN:** Test A passes, AND (Test B passes OR any Test C
  conditional prediction scores a hit).
- **Theory DISPROVEN:** Test A fails on an adequate corpus, OR a watched
  September market produces a settlement dispute the grades gave no warning of.
- **Anything else:** inconclusive — collection widens (more months, more
  disputes) and the funnel stays unbuilt. Inconclusive is not permission.

The result — pass, fail, or inconclusive — is written into this file with the
evidence, and the funnel decision follows it. Nobody gets to relitigate the
thresholds after the data arrives; that is the entire point of this document.

## Owner sign-off

- Thresholds and predictions proposed: 2026-08-31 (Claude, from live-graded
  data).
- Owner signature: _pending_ — merging this file to `main` before 2026-09-04
  constitutes sign-off on the thresholds and arms Test C.
