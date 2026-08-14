# Polymarket US FOMC rounding analysis — no approval path today

- **Date:** 2026-08-14
- **Authorization:** owner replied "2 - yes" to the flagged item "the PM-US July
  Fed pair needs a fresh analysis and your explicit yes" (this authorized the
  analysis; it did not pre-authorize any approval rule).
- **Status:** analysis complete; **no new approval rule signed**. The captured
  phrasings and a distinct rounding token shipped; every PM-US FOMC pair
  remains `REVIEW_REQUIRED`.

## What the venue actually publishes (captured live 2026-08-14)

Event `usfed-fomc-2026-07-29` (settled 2026-07-29; no-change resolved Yes):

> This market will settle to Yes if the Federal Reserve does not change the
> upper bound of the target federal funds rate at the July 2026 FOMC meeting.
> Outcome verified from the Federal Reserve.

The July texts publish **no rounding clause and no canceled-meeting/no-decision
fallback**. The 2026-08-13 census note attributing a rounding rule to PM-US
FOMC markets described the **September/October open events**, not July:

> If the change in the specified rate does not precisely match the displayed
> options, changes smaller than the smallest option of the same direction
> (increase/decrease) will be rounded to that smallest option, and changes
> greater than the smallest option of the same direction will be rounded to
> the nearest displayed option, with values exactly halfway between options
> being rounded away from zero.

## Findings

1. **The settled July no-change approval candidate dies on a published-text
   gap, not on Atlas.** Approval requires both legs `GUARANTEED`; the PM-US
   leg publishes no no-meeting branch, so it assesses `UNKNOWN`
   (`FAMILY_POLICY_INCOMPLETE`). The pair stays `REVIEW_REQUIRED` with
   agreeing outcomes (inconclusive) — correct under the no-inference doctrine.
2. **The September scheme is not Gamma's scheme.** Gamma publishes "rounded up
   to the nearest 25" (signed ceiling-in-magnitude reading, 2026-08-13
   decision); PM-US publishes nearest-displayed-option with ties away from
   zero AND a round-up-to-smallest-option floor for sub-bucket moves. Under
   that scheme the signed round-up preimage table does not transfer (the tails
   differ; e.g. a +10bps raw move is Yes on the PM-US 25-hike bucket but No on
   Gamma's exact-25 bucket under ceiling reading — different preimages).
3. **Under the PM-US scheme the maintain pair's preimages would be identical**
   ({0} on both sides: any nonzero move rounds to a directional bucket, never
   to no-change). This is recorded for a FUTURE signed decision — it cannot
   fire today because of finding 1, and no rule for it has been signed.

## What shipped (no verifier approval change)

- `_fomc_decision_bucket_terms` now parses the PM-US phrasings ("increases the
  upper bound … by 25 basis points at the July 2026 FOMC meeting", "does not
  change the upper bound …", "or more" → `>=`) and captures the September
  clause as the distinct token `rounding=nearest_bucket_away_from_zero`.
- `_fomc_preimage` hardening: any rounding token other than the signed
  `rounding=up_nearest_25bps` refuses the preimage (returns None) instead of
  being treated as an unrounded leg — defense in depth beneath the
  both-guaranteed gate.
- Frozen-text pins in `tests/test_fomc_pmus.py`.

## Future trigger

If a future PM-US FOMC event publishes a no-meeting/no-decision fallback, its
maintain leg can reach `GUARANTEED` through the existing
`_complete_fomc_decision_policy` path, and the maintain-pair preimage identity
in finding 3 becomes actionable — **via a new owner-signed decision extending
the preimage rule to the PM-US token**, not automatically.
