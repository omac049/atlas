"""A sentence-ending period after a number must not crash the monitor.

Kalshi's NFL receiving-yards escalators (first listed 2026-09-24) end their rules
with "Yes pays out at most $1.00." The threshold capture `[0-9][0-9,.]*` took
"1.00." and Decimal() raised, killing every monitor cycle for a day.
"""

from decimal import Decimal

import pytest

from atlas.fingerprints import build_fingerprint
from atlas.normalization import _number
from atlas.venues.kalshi import KalshiVenue

ESCALATOR_RULES = (
    "If Each Yes contract pays per receiving yards by Tucker Kraft in the Atlanta vs Green "
    "Bay Pro Football game originally scheduled for Sep 24, 2026. Each 10 receiving yards "
    "increase the payout by the following schedule: 0–9 yards, $0.0000; 10–19, $0.0001; "
    "190–199, $0.8573; and 200 or more, $1.0000. Each No contract pays $1.00 minus the Yes "
    "payout. Yes pays out at most $1.00. Then the market resolves to Yes."
)


@pytest.mark.parametrize(
    ("capture", "expected"),
    [
        ("1.00.", Decimal("1.00")),
        ("3.1.", Decimal("3.1")),
        ("1,000.", Decimal(1000)),
        # Captures that already parsed must keep their exact value (frozen normalizers).
        ("3.1", Decimal("3.1")),
        ("1,250,000", Decimal(1250000)),
        ("-0.25", Decimal("-0.25")),
        ("5.", Decimal(5)),
        ("4.25", Decimal("4.25")),
    ],
)
def test_number_parses_the_leading_number(capture, expected):
    assert _number(capture) == expected


def test_escalator_market_fingerprints_without_crashing():
    market = KalshiVenue._normalize_market(
        {
            "ticker": "KXNFLESCALATORRECYDS-26SEP24ATLGB-GBTKRAFT85",
            "title": "Tucker Kraft Receiving Yards Escalator",
            "yes_sub_title": "Tucker Kraft",
            "rules_primary": ESCALATOR_RULES,
            "status": "active",
            "strike_type": "custom",
        }
    )
    build_fingerprint(market)
