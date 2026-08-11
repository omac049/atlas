from decimal import Decimal

from atlas.arbitrage import calculate_opportunity
from atlas.storage import AtlasStore
from atlas.venues.fixtures import fixture_books, fixture_markets
from atlas.verification import verify_equivalence


async def run_once(store: AtlasStore | None = None):
    store = store or AtlasStore()
    markets, books = fixture_markets(), fixture_books()
    pair = verify_equivalence(
        markets["kalshi"][0], markets["polymarket_us"][0], "fixture-fed-sep26"
    )
    book_a, book_b = books["kalshi:KALSHI-FED-SEP26"], books["polymarket_us:PM-FED-SEP26"]
    await store.save_markets(list(markets["kalshi"]) + list(markets["polymarket_us"]))
    await store.save_orderbook(book_a)
    await store.save_orderbook(book_b)
    opportunity = calculate_opportunity(
        pair, book_a, book_b, Decimal(100), fees=Decimal("0.83"), slippage=Decimal("0.20")
    )
    if opportunity:
        await store.save_opportunity(opportunity)
    return opportunity
