# PENDING DECISION — evidence-backed REJECTED labels from same-subject review pairs

Status: **SIGNED OFF by the project owner on 2026-08-13** ("yes, proceed"). Implemented the
same day: invariant wording amended in AGENTS.md/README, `_historical_label` extended with the
same-subject/divergent-terminal gate, approved-first + complement-first labeling priority, and
the 5-per-event cap (`REVIEW_REJECTIONS_PER_EVENT`). The prior review-pairs-stay-inconclusive
test was rewritten as the named semantic flip. Outcome: the settled July 2026 CPI event minted
the first five evidence-backed `REJECTED` labels (K `>4.6`..`>5.0` settled `no` × PM `3.4%`
settled `Yes`, cap enforced). Trusted labels: 3 `APPROVED_EQUIVALENT` + 5 `REJECTED` — the
label-mix requirement is satisfied and the backfill reports `MILESTONE_COMPLETE`; only volume
(50+) remains before learning readiness.

## Why this decision exists

The balanced-dataset milestone requires 50+ trusted labels spanning BOTH classes
(`APPROVED_EQUIVALENT` and `REJECTED`), and the learning loop stays blocked until then. The
approval side now flows (~8 FOMC meetings/year plus whatever venue-text alignments bring). The
rejection side is dormant: `_resolution_label` (`atlas/validation.py`) already returns
`("DIVERGED", "REJECTED")` for a non-approved pair whose settled outcomes diverge, but no
pipeline currently routes review pairs into reconciliation, so zero `REJECTED` labels can ever
mint today.

## The invariant tension — this is the decision

AGENTS.md states the hard invariant: *"`REVIEW_REQUIRED`, inconclusive, unknown, or
non-guaranteed pairs must never become trusted labels."*

Read literally, that forbids minting ANY trusted label — including `REJECTED` — from a
`REVIEW_REQUIRED` pair, and the balanced dataset can then never exist (an approved pair that
settles divergently is the only other source, and every current approval is mathematically
immune to that). Read by spirit, the invariant exists to prevent *uncertainty laundering* —
treating an unproven relationship as an approved, actionable one. A `REJECTED` label does the
opposite: it records that terminal settlement evidence on both venues **disproved**
equivalence. The label's trust comes from the evidence, not from the review status.

**Decision required:** amend the invariant's wording to "must never become trusted *approval*
labels (`APPROVED_EQUIVALENT` / `APPROVED_INVERSE`)", and permit evidence-backed `REJECTED`
labels under the strict gate below. Amending AGENTS.md's hard-invariant text is an owner-only
action; nothing ships without it.

## The proposed gate (strict, deterministic)

Mint `REJECTED` only when ALL hold:

1. Both legs share an identical canonical `event_subject` (`family|anchor` form from the
   specialized normalizers) and `threshold_unit` — the twin-shape gate. This is the critical
   restriction: divergent-settling pairs with different subjects are trivially unrelated, and
   labeling them would flood the dataset with useless negatives. Same-subject divergences are
   *hard negatives* — the informative kind.
2. Deterministic verification returned `REVIEW_REQUIRED` (not unknown/unparsed junk).
3. Both venues published terminal outcomes, captured as evidence with hashes, and the outcomes
   diverge under the equivalence hypothesis (`outcome_a != outcome_b`).
4. Full provenance persisted: hypothesis, mismatch codes, outcomes, evidence versions.
5. Bounded per event: at most 5 rejections per shared settled event, complement-shaped
   near-misses first — one CPI month otherwise yields hundreds of exact-bucket rejections and
   imbalances the dataset in the other direction.

Worked example (real, recurring monthly): the settled July CPI tail pair — Kalshi `>3.1`
settled `yes`, Polymarket `≤3.1` settled `No`, same published BLS number. Under the
equivalence hypothesis the outcomes diverge, so it is a settlement-proven non-equivalent:
exactly what `REJECTED` should mean. (It is *plausibly* an inverse; if venue texts ever align,
the inverse rule approves it separately as `APPROVED_INVERSE` — no conflict, different
question.)

## What this is not

- Not a change to any approval path; approvals still require the full deterministic gate.
- Not retroactive relabeling: inconclusive pairs stay inconclusive; agreement never proves
  equivalence (same-subject pairs that settle agreeing remain `INCONCLUSIVE`, no label).
- Not self-training: labels derive from venue settlement evidence only.

## Implementation plan (only after sign-off)

1. AGENTS.md + README: amend the invariant wording (owner edit or explicit approval of exact
   wording).
2. `atlas/backfill.py`: route same-subject `REVIEW_REQUIRED` resolved pairs through
   `_resolution_label` with the per-event cap; report `rejected_labels` per run.
3. Tests: frozen July CPI tail pair mints `REJECTED`; different-subject divergent pair does
   NOT; same-subject agreeing pair stays `INCONCLUSIVE`; per-event cap enforced; approval
   paths untouched.
4. Record the decision here and in `TODO.md`.
