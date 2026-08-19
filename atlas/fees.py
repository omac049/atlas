"""Demo/fixture basket cost constants for the fixture opportunity pipeline.

These are the fee and slippage figures used by the deterministic fixture demo
(monitor run_once, `atlas opportunities demo`, and the API's fixture demo).
They are NOT the venue-published fee schedule — real per-venue taker-fee math
lives in atlas/gap_radar.py and must not be conflated with these.
"""

from decimal import Decimal

DEMO_BASKET_FEES = Decimal("0.83")
DEMO_BASKET_SLIPPAGE = Decimal("0.20")
