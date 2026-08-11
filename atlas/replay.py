import json
from pathlib import Path

from atlas.arbitrage import calculate_opportunity
from atlas.discovery import scan_market_pairs
from atlas.models import Market, VenueName


def write_market_bundle(markets: dict[VenueName, list[Market]], path: str, books: dict[str, object] | None = None) -> int:
    payload = {
        "schema_version": "1.0",
        "markets": {venue.value: [market.model_dump(mode="json") for market in values] for venue, values in markets.items()},
        "books": {key: value.model_dump(mode="json") for key, value in (books or {}).items()},
    }
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload), encoding="utf-8")
    return sum(len(values) for values in markets.values())


def read_market_bundle(path: str) -> dict[VenueName, list[Market]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        VenueName.KALSHI: [Market.model_validate(item) for item in payload["markets"].get("kalshi", [])],
        VenueName.POLYMARKET_US: [Market.model_validate(item) for item in payload["markets"].get("polymarket_us", [])],
    }


def replay_scan(path: str) -> dict[str, int]:
    markets = read_market_bundle(path)
    pairs = scan_market_pairs(markets[VenueName.KALSHI], markets[VenueName.POLYMARKET_US])
    approved = sum(
        pair.status.value in {"APPROVED_EQUIVALENT", "APPROVED_INVERSE"}
        for pair in pairs
    )
    return {"comparisons": len(pairs), "approved": approved, "review": len(pairs) - approved}


def replay_opportunities(path: str) -> int:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    books = {key: _orderbook(value) for key, value in payload.get("books", {}).items()}
    markets = read_market_bundle(path)
    count = 0
    for pair in scan_market_pairs(markets[VenueName.KALSHI], markets[VenueName.POLYMARKET_US]):
        book_a = books.get(pair.market_a.market_id)
        book_b = books.get(pair.market_b.market_id)
        if book_a and book_b and calculate_opportunity(pair, book_a, book_b):
            count += 1
    return count


def _orderbook(payload: dict):
    from atlas.models import OrderBook

    return OrderBook.model_validate(payload)
