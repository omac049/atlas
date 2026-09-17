from decimal import Decimal

from atlas.models import OrderBook, OrderBookLevel, VenueName
from atlas.orderbooks.state import CrossedBookError, OrderBookState, SequenceGapError

# A cross can be real for a message or two while a sweep's deltas arrive one
# level at a time. One that outlives this many messages is a broken state.
MAX_CROSSED_STATES = 200


class StreamCoordinator:
    def __init__(self):
        self.states: dict[str, OrderBookState] = {}
        self.crossed: dict[str, int] = {}

    def kalshi_event(self, market_ticker: str, message: dict) -> OrderBook | None:
        market_id = f"kalshi:{market_ticker}"
        state = self.states.setdefault(market_id, OrderBookState(VenueName.KALSHI, market_id))
        from atlas.streams.kalshi import KalshiOrderBookStream

        was_synced = state.synced
        try:
            changed = KalshiOrderBookStream.apply_message(state, message)
        except SequenceGapError:
            if was_synced:
                # A genuine gap on a synced book: the state has reset itself and
                # Kalshi only sends a snapshot on (re)subscribe, so the caller
                # must reconnect — swallowing this kills the book forever.
                raise
            # Delta before the subscribe-time snapshot: the snapshot is coming.
            return None
        if not (changed and state.synced):
            return None
        try:
            book = state.as_orderbook()
        except CrossedBookError:
            # Never hand a book that cannot exist to the evaluator or the store.
            self.crossed[market_id] = self.crossed.get(market_id, 0) + 1
            if self.crossed[market_id] > MAX_CROSSED_STATES:
                self.crossed[market_id] = 0
                state.reset()
                raise SequenceGapError("book stayed crossed; resubscribe for a snapshot") from None
            return None
        self.crossed[market_id] = 0
        return book

    def polymarket_event(self, message: dict) -> OrderBook | None:
        data = message.get("marketData")
        if not data:
            return None
        slug = data.get("marketSlug")
        if not slug:
            return None
        bids = [_level(row) for row in data.get("bids", [])]
        offers = [_level(row) for row in data.get("offers", [])]
        # The venue streams YES levels only; derive the NO side as the exact
        # complement, matching the REST adapter, so APPROVED_EQUIVALENT pairs
        # (which read asks_for("NO")) can actually price a live opportunity.
        return OrderBook(
            venue=VenueName.POLYMARKET_US,
            market_id=f"polymarket_us:{slug}",
            yes_bids=bids,
            yes_asks=offers,
            no_bids=[
                OrderBookLevel(price=Decimal(1) - level.price, quantity=level.quantity)
                for level in offers
            ],
            no_asks=[
                OrderBookLevel(price=Decimal(1) - level.price, quantity=level.quantity)
                for level in bids
            ],
        )


def _level(row: dict) -> OrderBookLevel:
    price = row.get("px", {})
    value = price.get("value", price) if isinstance(price, dict) else price
    return OrderBookLevel(
        price=Decimal(str(value)),
        quantity=Decimal(str(row.get("qty", "0"))),
    )
