import argparse
import asyncio
import os

from dotenv import load_dotenv

from atlas.storage import AtlasStore
from atlas.streams.coordinator import StreamCoordinator
from atlas.streams.kalshi import KalshiOrderBookStream
from atlas.streams.polymarket_us import PolymarketUSMarketStream


async def run_kalshi(tickers: list[str]) -> None:
    key_id = os.getenv("KALSHI_API_KEY_ID")
    key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH")
    if not key_id or not key_path:
        raise RuntimeError("KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH are required")
    stream = KalshiOrderBookStream(key_id, key_path, tickers)
    coordinator, store = StreamCoordinator(), AtlasStore()
    async for message in stream.messages():
        ticker = message.get("msg", {}).get("market_ticker") or tickers[0]
        book = coordinator.kalshi_event(ticker, message)
        if book:
            await store.save_orderbook(book)
            print(
                f"{book.market_id} seq={book.sequence} yes_ask={book.yes_asks[0].price if book.yes_asks else '—'}"
            )


async def run_polymarket(slugs: list[str]) -> None:
    headers = PolymarketUSMarketStream.auth_headers(
        os.environ["POLYMARKET_US_API_KEY"], os.environ["POLYMARKET_US_API_SECRET"]
    )
    stream = PolymarketUSMarketStream(headers, slugs)
    coordinator, store = StreamCoordinator(), AtlasStore()
    async for message in stream.messages():
        book = coordinator.polymarket_event(message)
        if book:
            await store.save_orderbook(book)
            print(f"{book.market_id} yes_bid={book.yes_bids[0].price if book.yes_bids else '—'}")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="atlas-worker")
    parser.add_argument("venue", choices=["kalshi", "polymarket_us"])
    parser.add_argument("markets", nargs="+")
    args = parser.parse_args()
    if args.venue == "kalshi":
        asyncio.run(run_kalshi(args.markets))
    else:
        asyncio.run(run_polymarket(args.markets))
