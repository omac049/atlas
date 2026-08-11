from decimal import Decimal

from atlas.models import OrderBook, OrderBookLevel, VenueName
from atlas.orderbooks.state import OrderBookState, SequenceGapError


class StreamCoordinator:
    def __init__(self):
        self.states: dict[str, OrderBookState] = {}

    def kalshi_event(self, market_ticker: str, message: dict) -> OrderBook | None:
        market_id = f"kalshi:{market_ticker}"
        state = self.states.setdefault(market_id, OrderBookState(VenueName.KALSHI, market_id))
        from atlas.streams.kalshi import KalshiOrderBookStream

        try:
            changed = KalshiOrderBookStream.apply_message(state, message)
        except SequenceGapError:
            return None
        return state.as_orderbook() if changed and state.synced else None

    def polymarket_event(self, message: dict) -> OrderBook | None:
        data = message.get("marketData")
        if not data:
            return None
        slug = data.get("marketSlug")
        if not slug:
            return None
        bids = [_level(row) for row in data.get("bids", [])]
        offers = [_level(row) for row in data.get("offers", [])]
        return OrderBook(
            venue=VenueName.POLYMARKET_US,
            market_id=f"polymarket_us:{slug}",
            yes_bids=bids,
            yes_asks=offers,
        )


def _level(row: dict) -> OrderBookLevel:
    price = row.get("px", {})
    return OrderBookLevel(
        price=Decimal(str(price.get("value", price))),
        quantity=Decimal(str(row.get("qty", "0"))),
    )
