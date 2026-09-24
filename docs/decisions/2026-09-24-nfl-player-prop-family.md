# 2026-09-24 — NFL player props: recognize and explain, no approval path

**Decision (owner, in chat, 2026-09-24):** "yes, teach the rules player props" → option A
"recognize and explain"; approach 1 (new family reader); NFL only.

**Why no approval path:** on live text, Kalshi settles a no-snap player at the fair price
*before game start* while Polymarket uses the *last* fair price, and Kalshi does not state
inactive, overtime, stat-correction, or postponement rules. Treating silence as agreement is
inference, which the hard invariant forbids. Evidence table: the design spec,
docs/plans/2026-09-24-nfl-player-prop-family-design.md.

**What it does:** twins share a canonical fingerprint; the verifier reports the three true
differences (settlement policy, resolution source, non-guaranteed settlement). A pair could
only become approvable if both venues state all five branches identically with a named source
and no fair-price outcome.
