from decimal import Decimal

import pytest

from atlas.models import OrderBookLevel, VenueName
from atlas.orderbooks.state import OrderBookState, SequenceGapError


def test_state_rebuilds_asks_from_binary_bid_books():
    state = OrderBookState(VenueName.KALSHI, "kalshi:TEST")
    state.apply_snapshot([OrderBookLevel(price=Decimal("0.40"), quantity=Decimal(10))], [], 1)
    book = state.as_orderbook()
    assert book.no_asks[0].price == Decimal("0.60")


def test_sequence_gap_resets_state():
    state = OrderBookState(VenueName.KALSHI, "kalshi:TEST")
    state.apply_snapshot([], [], 1)
    with pytest.raises(SequenceGapError):
        state.apply_kalshi_delta("yes", Decimal("0.40"), Decimal(10), 3)
    assert state.synced is False
