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
from atlas.venues.kalshi import KalshiVenue

# The streamed Kalshi book is compared with the venue's own REST book on this
# cadence. It exists because for six weeks nothing did: the stream stored books
# that matched the venue in nothing but the ticker, and no test could see it.
BOOK_CHECK_SECONDS = 900.0
BOOK_CHECK_MISMATCHES_BEFORE_RESYNC = 3
RESYNC_FORGIVEN_AFTER_BOOKS = 500


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
        # The two books behind a recorded opportunity are its audit trail. Books
        # are no longer stored on every stream message: that wrote ~50-140 MB a
        # day which nothing read.
        await store.save_orderbook(books["a"])
        await store.save_orderbook(books["b"])
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

    resync_requested = asyncio.Event()

    async def consume_kalshi():
        delay, resyncs = 1.0, 0
        while True:
            try:
                stream = KalshiOrderBookStream(key_id, key_path, [kalshi_ticker])
                emitted = 0
                resync_requested.clear()
                async for message in stream.messages():
                    delay = 1.0
                    try:
                        book = coordinator.kalshi_event(kalshi_ticker, message)
                    except SequenceGapError:
                        # The book state has reset and Kalshi only sends a
                        # snapshot on (re)subscribe: drop the stale book and
                        # reconnect so a fresh snapshot resyncs it.
                        books.pop("a", None)
                        resyncs += 1
                        break
                    if message.get("type") == "orderbook_snapshot":
                        state = coordinator.states[f"kalshi:{kalshi_ticker}"]
                        print(KalshiOrderBookStream.snapshot_summary(kalshi_ticker, message, state))
                    if book:
                        emitted += 1
                        if emitted == RESYNC_FORGIVEN_AFTER_BOOKS:
                            resyncs = 0
                        books["a"] = book
                        await evaluate()
                    if resync_requested.is_set():
                        books.pop("a", None)
                        resyncs += 1
                        break
                # A resubscribe is a new connection: never in a tight loop.
                await asyncio.sleep(min(2.0**resyncs, 300.0))
            except asyncio.CancelledError:
                raise
            except (KeyError, TypeError, ArithmeticError, OSError, RuntimeError, ValueError,
                    WebSocketException):
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def check_kalshi_book():
        """Compare the streamed top of book with the venue's REST book."""
        venue, mismatches = KalshiVenue(fixture=False), 0
        while True:
            await asyncio.sleep(BOOK_CHECK_SECONDS)
            streamed = books.get("a")
            if streamed is None:
                continue
            try:
                rest = await venue.get_orderbook(kalshi_ticker)
            except Exception as exc:  # noqa: BLE001 - a failed check is not a finding
                print(f"kalshi_stream_check {kalshi_ticker} skipped={type(exc).__name__}")
                continue
            streamed = books.get("a") or streamed  # the freshest state, read after the fetch
            verdict = compare_top_of_book(streamed, rest)
            mismatches = 0 if verdict["match"] else mismatches + 1
            print(
                f"kalshi_stream_check {kalshi_ticker} match={str(verdict['match']).lower()} "
                f"stream={verdict['stream']} rest={verdict['rest']}"
            )
            if mismatches >= BOOK_CHECK_MISMATCHES_BEFORE_RESYNC:
                print(f"kalshi_stream_check {kalshi_ticker} resync=requested after {mismatches} misses")
                mismatches = 0
                resync_requested.set()

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
        group.create_task(check_kalshi_book())


def _top(book) -> tuple:
    best_bid = max((level.price for level in book.yes_bids), default=None)
    best_ask = min((level.price for level in book.yes_asks), default=None)
    return best_bid, best_ask


def compare_top_of_book(streamed, rest, tolerance="0.01") -> dict:
    """Do the two books agree on the best YES bid and ask, within a cent?

    A cent of slack because the two reads are a few hundred milliseconds apart
    on a live market; a broken state machine misses by tens of cents.
    """
    from decimal import Decimal

    slack = Decimal(tolerance)

    def close(a, b) -> bool:
        return (a is None and b is None) or (
            a is not None and b is not None and abs(a - b) <= slack
        )

    (s_bid, s_ask), (r_bid, r_ask) = _top(streamed), _top(rest)
    return {
        "match": close(s_bid, r_bid) and close(s_ask, r_ask),
        "stream": f"{s_bid}/{s_ask}",
        "rest": f"{r_bid}/{r_ask}",
    }
