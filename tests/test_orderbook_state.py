from decimal import Decimal

import pytest

from atlas.models import OrderBookLevel, VenueName
from atlas.orderbooks.state import CrossedBookError, OrderBookState, SequenceGapError


def level(price: str, size: str) -> OrderBookLevel:
    return OrderBookLevel(price=Decimal(price), quantity=Decimal(size))


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


def test_a_delta_is_a_signed_change_not_the_new_size():
    """Kalshi's delta_fp is how much the level changed. Stored as the size
    itself (the defect until 2026-09-17), a 208-contract reduction of a
    3,108-contract level deleted the level, and a 100-contract addition to it
    left a level of 100."""
    state = OrderBookState(VenueName.KALSHI, "kalshi:TEST")
    state.apply_snapshot([level("0.51", "3108")], [level("0.48", "8630")], 1)
    state.apply_kalshi_delta("yes", Decimal("0.51"), Decimal(-208), 2)
    assert state.as_orderbook().yes_bids[0].quantity == Decimal(2900)
    state.apply_kalshi_delta("yes", Decimal("0.51"), Decimal(100), 3)
    assert state.as_orderbook().yes_bids[0].quantity == Decimal(3000)
    state.apply_kalshi_delta("yes", Decimal("0.51"), Decimal(-3000), 4)
    assert state.as_orderbook().yes_bids == []
    state.apply_kalshi_delta("no", Decimal("0.47"), Decimal(25), 5)
    assert [(lv.price, lv.quantity) for lv in state.as_orderbook().no_bids] == [
        (Decimal("0.48"), Decimal(8630)), (Decimal("0.47"), Decimal(25)),
    ]


def test_a_book_that_cannot_rest_on_the_venue_is_refused():
    state = OrderBookState(VenueName.KALSHI, "kalshi:TEST")
    state.apply_snapshot([level("0.51", "10")], [level("0.49", "10")], 1)  # 51c + 49c = $1: would match
    with pytest.raises(CrossedBookError):
        state.as_orderbook()
    state.apply_snapshot([level("0.51", "10")], [level("0.48", "10")], 1)
    assert state.as_orderbook().yes_asks[0].price == Decimal("0.52")
