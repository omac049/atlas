from decimal import Decimal

import pytest

from atlas.orderbooks.state import SequenceGapError
from atlas.streams.coordinator import StreamCoordinator


def test_coordinator_emits_canonical_kalshi_book_after_snapshot():
    coordinator = StreamCoordinator()
    book = coordinator.kalshi_event(
        "TEST",
        {
            "type": "orderbook_snapshot",
            "seq": 1,
            "msg": {"market_ticker": "TEST", "yes": [["0.40", "10"]], "no": []},
        },
    )
    assert book is not None
    assert book.market_id == "kalshi:TEST"
    assert book.no_asks[0].price == Decimal("0.60")


def test_coordinator_parses_polymarket_currency_objects():
    book = StreamCoordinator().polymarket_event(
        {
            "marketData": {
                "marketSlug": "test",
                "bids": [{"px": {"value": "0.41"}, "qty": "4"}],
                "offers": [],
            }
        }
    )
    assert book is not None
    assert book.yes_bids[0].price == Decimal("0.41")


def test_coordinator_parses_polymarket_scalar_prices():
    book = StreamCoordinator().polymarket_event(
        {
            "marketData": {
                "marketSlug": "test",
                "bids": [{"px": "0.41", "qty": "4"}],
                "offers": [{"px": "0.47", "qty": "2"}],
            }
        }
    )
    assert book is not None
    assert book.yes_bids[0].price == Decimal("0.41")
    assert book.yes_asks[0].price == Decimal("0.47")


def test_coordinator_derives_polymarket_no_side_as_exact_complement():
    """APPROVED_EQUIVALENT pairs price the Polymarket leg off asks_for("NO");
    a YES-only stream book would make the live monitor permanently blind."""
    book = StreamCoordinator().polymarket_event(
        {
            "marketData": {
                "marketSlug": "test",
                "bids": [{"px": {"value": "0.41"}, "qty": "4"}],
                "offers": [{"px": {"value": "0.47"}, "qty": "2"}],
            }
        }
    )
    assert book is not None
    assert book.no_bids[0].price == Decimal("0.53")
    assert book.no_bids[0].quantity == Decimal(2)
    assert book.no_asks[0].price == Decimal("0.59")
    assert book.no_asks[0].quantity == Decimal(4)
    assert book.asks_for("NO"), "NO asks must be populated for equivalent pairs"


def test_coordinator_raises_on_sequence_gap_after_synced_snapshot():
    """A gap on a synced book resets the state, and Kalshi only sends a
    snapshot on (re)subscribe — the caller must see the gap and reconnect."""
    coordinator = StreamCoordinator()
    coordinator.kalshi_event(
        "TEST",
        {
            "type": "orderbook_snapshot",
            "seq": 1,
            "msg": {"market_ticker": "TEST", "yes": [["0.40", "10"]], "no": []},
        },
    )
    with pytest.raises(SequenceGapError):
        coordinator.kalshi_event(
            "TEST",
            {
                "type": "orderbook_delta",
                "seq": 5,
                "msg": {"market_ticker": "TEST", "side": "yes", "price": "0.40", "quantity": "5"},
            },
        )


def test_coordinator_ignores_delta_before_first_snapshot():
    """At subscribe time a delta can outrace the snapshot; that is not a gap —
    the snapshot is coming, so the event is simply dropped."""
    coordinator = StreamCoordinator()
    book = coordinator.kalshi_event(
        "TEST",
        {
            "type": "orderbook_delta",
            "seq": 3,
            "msg": {"market_ticker": "TEST", "side": "yes", "price": "0.40", "quantity": "5"},
        },
    )
    assert book is None
