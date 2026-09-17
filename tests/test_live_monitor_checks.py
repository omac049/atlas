"""The streamed Kalshi book is compared with the venue's REST book."""

from datetime import UTC, datetime
from decimal import Decimal

from atlas.live_monitor import compare_top_of_book
from atlas.models import OrderBook, OrderBookLevel, VenueName


def book(yes_bids, yes_asks) -> OrderBook:
    def levels(pairs):
        return [OrderBookLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in pairs]

    return OrderBook(venue=VenueName.KALSHI, market_id="kalshi:T", timestamp=datetime.now(UTC),
                     yes_bids=levels(yes_bids), yes_asks=levels(yes_asks))


def test_a_cent_of_drift_between_two_reads_is_a_match():
    rest = book([("0.50", "9"), ("0.51", "3108")], [("0.53", "4"), ("0.52", "8630")])
    assert compare_top_of_book(book([("0.51", "2900")], [("0.52", "8000")]), rest)["match"] is True
    assert compare_top_of_book(book([("0.50", "10")], [("0.52", "10")]), rest)["match"] is True


def test_the_book_the_old_recorder_kept_is_a_mismatch():
    """2026-09-17, KXFEDDECISION-26OCT-H0: venue 51c/52c, recorder 48c bid and a 19c "ask"."""
    rest = book([("0.51", "3108.74")], [("0.52", "8630")])
    verdict = compare_top_of_book(book([("0.48", "116")], [("0.19", "2133")]), rest)
    assert verdict == {"match": False, "stream": "0.48/0.19", "rest": "0.51/0.52"}


def test_an_empty_side_only_matches_an_empty_side():
    assert compare_top_of_book(book([], [("0.01", "5")]), book([], [("0.01", "9")]))["match"] is True
    assert compare_top_of_book(book([], [("0.01", "5")]), book([("0.01", "1")], [("0.02", "9")]))["match"] is False
