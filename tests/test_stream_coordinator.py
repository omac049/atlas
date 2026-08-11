from decimal import Decimal

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
