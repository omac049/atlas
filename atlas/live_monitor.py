import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

from websockets.exceptions import WebSocketException

from atlas.arbitrage import calculate_opportunity
from atlas.models import ContractPair, PaperTradeRecord
from atlas.orderbooks.state import SequenceGapError
from atlas.storage import AtlasStore
from atlas.streams.coordinator import StreamCoordinator
from atlas.streams.kalshi import KalshiOrderBookStream
from atlas.streams.polymarket_us import PolymarketUSMarketStream


class LiveStreamCredentialsMissing(RuntimeError):
    """Raised when the authenticated order-book streams cannot be opened.

    Distinct from a venue/network error so the monitor can report a missing
    credential as a configuration blocker rather than a transient failure.
    """


# Environment variable NAMES the live streams require — names only, so the API
# can report which credentials are absent without ever touching their values.
REQUIRED_STREAM_CREDENTIALS = (
    "KALSHI_API_KEY_ID",
    "KALSHI_PRIVATE_KEY_PATH",
    "POLYMARKET_US_API_KEY",
    "POLYMARKET_US_API_SECRET",
)


async def run_pair(pair: ContractPair, store: AtlasStore | None = None) -> None:
    if pair.approved_by is None or pair.status.value not in {
        "APPROVED_EQUIVALENT",
        "APPROVED_INVERSE",
    }:
        raise ValueError("live monitor requires an explicitly approved deterministic pair")
    # Name every missing credential in one message. A bare KeyError here is
    # invisible in practice: this coroutine is spawned with create_task by the
    # continuous monitor, so the exception is swallowed and the pair simply
    # never streams — indistinguishable from "no opportunity was found".
    missing = [name for name in REQUIRED_STREAM_CREDENTIALS if not os.environ.get(name)]
    if missing:
        raise LiveStreamCredentialsMissing(
            "live order-book streaming needs "
            + ", ".join(missing)
            + " (values are read from the environment or .env; never logged)"
        )
    key_id, key_path = os.environ["KALSHI_API_KEY_ID"], os.environ["KALSHI_PRIVATE_KEY_PATH"]
    poly_key, poly_secret = (
        os.environ["POLYMARKET_US_API_KEY"],
        os.environ["POLYMARKET_US_API_SECRET"],
    )
    store = store or AtlasStore()
    coordinator, books, last_signature = StreamCoordinator(), {}, None
    kalshi_ticker, poly_slug = pair.market_a.venue_market_id, pair.market_b.venue_market_id

    async def evaluate():
        nonlocal last_signature
        if "a" not in books or "b" not in books:
            return
        now = datetime.now(UTC)
        if any((now - book.timestamp).total_seconds() > 5 for book in books.values()):
            return
        opportunity = calculate_opportunity(pair, books["a"], books["b"])
        if opportunity is None:
            return
        signature = (
            opportunity.leg_a_average_price,
            opportunity.leg_b_average_price,
            opportunity.contracts,
        )
        if signature == last_signature:
            return
        last_signature = signature
        await store.save_opportunity(opportunity)
        await store.save_paper_trade(
            PaperTradeRecord(
                trade_id=str(uuid4()),
                opportunity_id=opportunity.opportunity_id,
                status=opportunity.status,
                simulated_profit=opportunity.expected_profit,
            )
        )
        print(
            f"PAPER OPPORTUNITY {opportunity.opportunity_id} edge={opportunity.expected_roi:.2%} size={opportunity.contracts}"
        )

    async def consume_kalshi():
        delay = 1.0
        while True:
            try:
                stream = KalshiOrderBookStream(key_id, key_path, [kalshi_ticker])
                async for message in stream.messages():
                    delay = 1.0
                    try:
                        book = coordinator.kalshi_event(kalshi_ticker, message)
                    except SequenceGapError:
                        # The book state has reset and Kalshi only sends a
                        # snapshot on (re)subscribe: drop the stale book and
                        # reconnect so a fresh snapshot resyncs it.
                        books.pop("a", None)
                        break
                    if book:
                        books["a"] = book
                        await store.save_orderbook(book)
                        await evaluate()
            except asyncio.CancelledError:
                raise
            except (KeyError, TypeError, ArithmeticError, OSError, RuntimeError, ValueError,
                    WebSocketException):
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def consume_polymarket():
        delay = 1.0
        while True:
            try:
                headers = PolymarketUSMarketStream.auth_headers(poly_key, poly_secret)
                stream = PolymarketUSMarketStream(headers, [poly_slug])
                async for message in stream.messages():
                    delay = 1.0
                    book = coordinator.polymarket_event(message)
                    if book:
                        books["b"] = book
                        await store.save_orderbook(book)
                        await evaluate()
            except asyncio.CancelledError:
                raise
            except (KeyError, TypeError, ArithmeticError, OSError, RuntimeError, ValueError,
                    WebSocketException):
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async with asyncio.TaskGroup() as group:
        group.create_task(consume_kalshi())
        group.create_task(consume_polymarket())
