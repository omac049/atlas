# PENDING DECISION — published vs. unpublished rounding on the fed-funds families

Status: **awaiting explicit human sign-off. No verifier or normalizer change may land from this
memo until the decision below is recorded.** (AGENTS.md / TODO.md Step 5 review gate.)

Prepared 2026-08-12 by a read-only analysis agent; all quotes verified live against
`external-api.kalshi.com/trade-api/v2` and `gamma-api.polymarket.com` the same day.

## The single decision required

Polymarket's decision buckets publish: *"the change will be rounded up to the nearest 25 and
will resolve to the relevant bracket. (e.g. if there's a cut/increase of 12.5 bps it will be
considered to be 25 bps)"*. The proposal below is only provable under the **ceiling-in-magnitude
reading** of that clause (any nonzero change rounds up in magnitude to the next 25bp multiple).
Under a "nearest, ties up" reading, the proofs break and the status quo must stand everywhere.
The published 12.5→25 example cannot discriminate between the readings (12.5 is exactly the
tie). Signing off on the ceiling reading is a text-interpretation judgment the human owner must
own explicitly. It is not an inference about Fed behavior.

## Live-text status quo (verified 2026-08-12)

- Kalshi publishes **no rounding terms** on any Sep/Oct/Dec 2026 fed market (checked all 15
  `KXFEDDECISION` and all 33 `KXFED` markets; zero occurrences of "round").
- Polymarket publishes round-up on all decision buckets and nearest-25/away-from-zero on the
  end-of-2026 level market (byte-identical to the frozen fixtures in
  `tests/test_fomc_decision.py` / `tests/test_fed_funds_level.py`).
- Data-quality quirk found: every Kalshi *Cut >25bps* market (`...-C26`) drops the ">" in
  `rules_primary` ("does a Cut of   25bps"), making its rules text indistinguishable from C25's.
  Atlas parses correctly today only because titles are scanned first. Deserves a defensive
  regression test regardless of this decision.
- Parser gap found (independent of this decision): Polymarket's "Will there be no change in Fed
  interest rates after the <month> <year> meeting?" bucket is not captured by
  `_fomc_decision_bucket_terms` at all.

## Stakes

The divergence bites only on a non-multiple-of-25bp change to the target-range upper bound:
zero occurrences in ~300 decisions since 1990, but both venues hedge the branch in writing, so
it is contemplated, not fantastical.

## The analysis (preimage equality under each leg's own published policy)

Compute each leg's Yes-set over the raw outcome using only that leg's published rounding policy
(identity when none is published — nothing is inferred), then require exact set equality or
complement.

Decision family (change Δ in bps):

| Pair | Kalshi Yes-set | Polymarket Yes-set | Verdict |
|---|---|---|---|
| H0 / "no change" | {0} ∪ fallback | {0} ∪ fallback | **Equal — provably rounding-immune** |
| H25 / "+25 bps" | {25} | (0, 25] | Diverge on (0,25) — **correctly blocked forever** |
| H26 ">25bps" / "50+ bps" | (25, ∞) | (25, ∞) | **Equal — provably rounding-immune** (mirror: C26 / "−50+") |

Level family: **no admissible pairing exists** — Kalshi boundaries sit on the 25bp grid, the
Polymarket preimage boundaries sit at grid±12.5bp (counterexamples: U=4.30 → K ">4.25" Yes,
PM "≥4.5" No; U=1.05 → PM "≤1.0" Yes, K-complement No). The offset is invariant across strikes;
the whole family stays `REVIEW_REQUIRED` under every option.

## Recommendation

- Reject reclassifying the families as structurally unreachable (Kalshi revises these rules in
  flight; auto-promotion must stay possible).
- Keep the status quo (`REVIEW_REQUIRED`) for the level family and the exact-±25 buckets —
  genuinely divergent.
- Adopt preimage-equality for the decision family's provably immune subset (maintain pair,
  open-tail pairs), **conditional on explicit sign-off on the ceiling reading above.**

Payoff if approved: up to three settled `APPROVED_EQUIVALENT` pairs from the already-captured
July 2026 meeting — the project's first trusted labels — recurring ~8 meetings/year via the
existing batch defaults.

## Implementation plan (only after sign-off)

1. `atlas/normalization.py`: capture PM's "no change" bucket as maintain/0/`=` (pure parser
   gap, may land before the decision — it changes no verdict on its own).
2. `atlas/verification.py`: narrow branch modeled on `_is_threshold_complement` — fires only
   for `fomc_rate_change_bucket` scope, same meeting subject, equal `no_meeting` tokens, both
   legs `GUARANTEED`, and computed preimages exactly equal/complementary. Fingerprints keep the
   rounding token; nothing is erased.
3. Tests: `test_open_ended_bucket_operators_do_not_cross_match` currently pins H26×"50+" as
   `REVIEW_REQUIRED` — under this decision that pair becomes `APPROVED_EQUIVALENT`; that test
   rewrite is the semantic flip and must be named in the review. Add frozen-text tests for the
   maintain-pair approval, the ±25 refusal, the level-family counterexamples, and the C26
   dropped-">" quirk.
4. Record the signed decision here (flip Status above) and in `TODO.md`.
