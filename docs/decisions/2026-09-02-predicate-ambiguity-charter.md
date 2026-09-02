# The predicate-ambiguity charter (third hypothesis)

**Status: PROPOSED — awaiting owner sign-off.** Merging this file to `main`
constitutes sign-off on the protocol and thresholds below. Nothing about the
holdout test may change after the instrument-freeze commit named in §4.

**Lineage.** Hypothesis one (cross-venue arbitrage) failed on capital lock-up
and dust-sized depth. Hypothesis two — *settlement-machinery completeness
predicts disputes* — failed Test A of the fine-print proof charter on
2026-09-01: disputed markets graded identically to controls (65 vs 65). That
failure produced this hypothesis. The famous disputes were not about missing
fallbacks or absent sources; they were about what a **word means** — "suit",
"performance", "permanent", "invasion", "involved", "found", "banned".

## 1. The theory, stated so it can fail

> Markets whose resolution predicate turns on an **undefined, judgment-laden
> term** produce disputed resolutions at a materially higher rate than markets
> whose predicate is anchored to a **measurable criterion** — a number, a named
> data series, a scheduled event, or an official act by a named body — in the
> same venue and category.

Oracle governance (UMA token votes, venue overrides) is deliberately **not**
part of this theory. It is venue-wide and outcome-side: it shapes *how* a
dispute resolves, not *which* markets get disputed, so it cannot separate
disputed from undisputed markets within one venue. It is recorded as context,
never scored.

## 2. The contamination this charter must survive — stated first

The drafter of this charter has read all 20 disputes in
`docs/proof/test-a-corpus.json` and their 45 controls. Any instrument designed
now is at risk of being tuned to those exact markets. Therefore:

- The Test A corpus and its controls are the **development set**. They may be
  used to build, calibrate, and sanity-check the instrument. They may **never**
  be the test.
- The test runs on a **holdout corpus assembled AFTER the instrument is
  frozen**, containing zero markets from the development set, gathered by a
  method that does not depend on which disputes were newsworthy (§5).
- Separation on the development set is a **feasibility gate** (§4), necessary
  to justify assembling a holdout at all — and it is never, under any framing,
  evidence for the theory.

## 3. The instrument — Predicate Ambiguity Score (PAS)

Deterministic, explainable, catalog-wide, read-only — the same constraints as
the clarity score, and the same one-way dependency: nothing in the approval
pipeline may import it. It operates on the market's published resolution text
only. Scale 0–100, where higher means **more ambiguous**.

**Feature families (fixed here; per-feature weights are fixed in code at the
freeze commit and may not change afterward):**

Ambiguity signals — add points:

- `OPERATIVE_TERM_UNDEFINED` — the predicate's operative noun or verb has no
  definition clause in the text ("for the purposes of this market, X means…").
- `JUDGMENT_QUALIFIER` — the text conditions resolution on judgment language:
  "consensus of credible reporting", "explicitly", "definitively",
  "intended to", "in the physical presence of", "any portion of",
  "substantially", "generally understood".
- `MULTI_SOURCE_DISCRETION` — more than one resolution source, or a
  "will also suffice" secondary source.
- `VENUE_CLARIFICATION_RESERVED` — the venue reserves the right to clarify or
  resolve at its discretion.

Anchoring signals — subtract points:

- `MEASURABLE_ANCHOR` — the predicate references a numeric threshold, a named
  data series, a scheduled dated event, or an official act by a named body.
- `DEFINITION_CLAUSE_PRESENT` — an explicit definition of the operative term.
- `EDGE_CASES_ENUMERATED` — "for the avoidance of doubt" style inclusions or
  exclusions.

Every feature carries plain-English prose and, where applicable, "what would
fix it". A market is **flagged ambiguous** when PAS ≥ 50 (fixed here).

**What the instrument is not.** It does not read outcomes, prices, volume,
news, or the identity of the market. It cannot know a market was disputed.
Its inputs are the same rules text the clarity score reads.

### Secondary, exploratory only: an AI judge

An AI-rated ambiguity score (frozen prompt, versioned model, run blind on
rules text with market names stripped) **may** be computed alongside PAS and
reported. It is not decisive: **a pass on the AI judge alone proves
nothing**, because it cannot be audited the way a deterministic feature can.
It exists to tell us whether deterministic features are missing something
obvious. Any product claim rests on PAS only.

## 4. Feasibility gate on the development set (before any holdout work)

After the instrument is built and its weights fixed:

1. Score the 15 gradeable Test A disputes and their 45 controls.
2. Required to proceed: disputed median PAS exceeds control median by **≥ 15
   points** AND ≥ 60% of disputed markets are flagged at ≥ 2x the control
   flag rate.
3. **Commit the instrument** — code, weights, tests, and the development-set
   scores — and record that commit's hash in this file as the **freeze
   commit**. From that hash forward the instrument is immutable for the
   duration of the test.

If the gate fails, the hypothesis is **abandoned before a holdout is ever
assembled**, and that is written here. Passing the gate says only that the
instrument is worth testing.

## 5. The holdout test

**Corpus source — systematic, not newsworthy.** Polymarket resolutions that
escalated to a UMA Optimistic Oracle dispute (a DVM vote) are a public,
enumerable record independent of press coverage. The holdout is: every market
whose resolution was escalated to a DVM vote inside a fixed date window chosen
at freeze time, **excluding every development-set market**, capped at the
first 30 by escalation date. If the escalation record proves unenumerable in
practice, the fallback is a fresh press-sourced corpus assembled by a
researcher given the development-set list as an **exclusion** list — and that
fallback is recorded as a weaker design.

**Controls:** the same rule as Test A — 3 settled, undisputed, same-venue,
same-primary-tag markets per dispute, nearest close-times, market_id
tiebreak, selected by `atlas.proof.select_controls`. Never hand-picked.

**Blind:** the holdout corpus is assembled and committed **after** the freeze
commit and **before** any PAS is computed on it.

**Pre-registered pass criteria (both must hold, on ≥ 15 gradeable holdout
disputes):**

1. Disputed median PAS exceeds control median PAS by **≥ 15 points**.
2. **≥ 60%** of disputed markets are flagged ambiguous, at **≥ 2x** the
   control group's flag rate.

**Fail:** either criterion unmet with ≥ 15 gradeable holdout disputes.
**Inconclusive:** fewer than 15 gradeable holdout disputes; reported as such,
never padded.

## 6. What would NOT count as proof

- Development-set separation — that is fit, and §4 says so.
- The AI judge passing alone.
- High ambiguity scores in absolute terms; the claim is separation from
  controls.
- A holdout assembled after anyone has computed PAS on candidate markets.
- Any change to weights, features, the flag threshold, the window, or the
  corpus cap after the freeze commit.

## 7. Decision rule and consequence

- **PROVEN:** both holdout criteria pass. The funnel gate of 2026-08-25
  reopens — on the Predicate Ambiguity Score, not the clarity score — and the
  product premise becomes "we tell you which markets turn on a word nobody
  defined."
- **DISPROVEN:** either criterion fails on an adequate holdout. This line of
  product inquiry closes; a fourth hypothesis would need a fourth charter, and
  the owner should weigh whether the pattern — three theses, three negatives —
  is itself the answer.
- **INCONCLUSIVE:** the holdout could not reach 15; widen the window once (a
  new date range, same rules) and rerun; a second inconclusive is treated as
  DISPROVEN for product purposes.

The result is written into this file with the evidence, at the same
prominence either way.

## 8. Honest odds, recorded before the work

This is harder than what failed. Machinery completeness was cheap to detect;
ambiguity of meaning is semantic, and a lexical instrument will catch only its
surface. The development set is small (15 disputes) and famous, which biases
toward markets with rich text. The instrument may pass the feasibility gate on
fit alone and then fail the holdout — that outcome is expected to be the
single most likely one, and it is a legitimate answer. The reason to run it
anyway is that Test A pointed here with unusual clarity, and the cost of a
pre-registered answer is days.

## 9. Sequence and sign-off

1. Owner merges this charter (sign-off).
2. Build `atlas/ambiguity.py` + tests; run the §4 gate; record the freeze
   commit hash here.
3. Verify the UMA escalation record is enumerable; assemble and commit the
   holdout corpus.
4. Run the holdout; write the result here.

- Proposed: 2026-09-02 (Claude, one day after Test A's failure, having read
  the development set — see §2).
- Freeze commit: _pending_.
- Owner signature: _pending — merging this file constitutes sign-off on the
  protocol, thresholds, and the development/holdout separation._
