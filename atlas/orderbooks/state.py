from datetime import UTC, datetime
from decimal import Decimal

from atlas.models import OrderBook, OrderBookLevel, VenueName


class SequenceGapError(RuntimeError):
    pass


class CrossedBookError(RuntimeError):
    """The maintained levels describe a book that cannot rest on the venue: a YES
    bid and a NO bid that sum to a dollar or more would already have matched."""


class OrderBookState:
    """Mutable in-memory book with explicit snapshot/delta safety semantics."""

    def __init__(self, venue: VenueName, market_id: str):
        self.venue = venue
        self.market_id = market_id
        self._levels: dict[str, dict[Decimal, Decimal]] = {"yes_bid": {}, "no_bid": {}}
        self.sequence: int | None = None
        self.timestamp: datetime | None = None
        self.synced = False
        # Kalshi's feed can express the NO side in YES prices; learned from each
        # opening snapshot by the stream layer and kept across resets, because
        # it is a property of the subscription, not of the book.
        self.no_side_in_yes_prices: bool | None = None

    def reset(self) -> None:
        self._levels = {"yes_bid": {}, "no_bid": {}}
        self.sequence = None
        self.timestamp = None
        self.synced = False

    def apply_snapshot(
        self,
        yes_bids: list[OrderBookLevel],
        no_bids: list[OrderBookLevel],
        sequence: int | None = None,
    ) -> None:
        self._levels = {
            "yes_bid": {level.price: level.quantity for level in yes_bids if level.quantity > 0},
            "no_bid": {level.price: level.quantity for level in no_bids if level.quantity > 0},
        }
        self.sequence = sequence
        self.timestamp = datetime.now(UTC)
        self.synced = True

    def apply_kalshi_delta(
        self, side: str, price: Decimal, delta: Decimal, sequence: int
    ) -> None:
        """``delta`` is the signed CHANGE in resting size at ``price`` (Kalshi's
        ``delta_fp``), never the new size. Until 2026-09-17 it was stored as the
        size itself, and any reduction deleted the level outright."""
        if not self.synced or self.sequence is None:
            raise SequenceGapError("delta received before a valid snapshot")
        if sequence != self.sequence + 1:
            expected = self.sequence + 1
            self.reset()
            raise SequenceGapError(f"expected sequence {expected}, received {sequence}")
        key = "yes_bid" if side.lower().startswith("yes") else "no_bid"
        size = self._levels[key].get(price, Decimal(0)) + delta
        if size <= 0:
            self._levels[key].pop(price, None)
        else:
            self._levels[key][price] = size
        self.sequence = sequence
        self.timestamp = datetime.now(UTC)

    def as_orderbook(self) -> OrderBook:
        if not self.synced:
            raise RuntimeError("book is not synchronized")
        best_yes = max(self._levels["yes_bid"], default=None)
        best_no = max(self._levels["no_bid"], default=None)
        if best_yes is not None and best_no is not None and best_yes + best_no >= 1:
            raise CrossedBookError(
                f"{self.market_id}: YES bid {best_yes} + NO bid {best_no} cannot both rest"
            )
        yes_bids = [
            OrderBookLevel(price=p, quantity=q)
            for p, q in sorted(self._levels["yes_bid"].items(), reverse=True)
        ]
        no_bids = [
            OrderBookLevel(price=p, quantity=q)
            for p, q in sorted(self._levels["no_bid"].items(), reverse=True)
        ]
        return OrderBook(
            venue=self.venue,
            market_id=self.market_id,
            timestamp=self.timestamp or datetime.now(UTC),
            yes_bids=yes_bids,
            no_bids=no_bids,
            yes_asks=[
                OrderBookLevel(price=Decimal(1) - x.price, quantity=x.quantity) for x in no_bids
            ],
            no_asks=[
                OrderBookLevel(price=Decimal(1) - x.price, quantity=x.quantity) for x in yes_bids
            ],
            sequence=self.sequence,
        )
