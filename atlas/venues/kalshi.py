import re
from datetime import datetime
from decimal import Decimal

import httpx

from atlas.config import settings
from atlas.models import Market, MarketStatus, OrderBook, OrderBookLevel, VenueName
from atlas.venues.base import PredictionVenue
from atlas.venues.fixtures import fixture_books, fixture_markets
from atlas.venues.http import get_json

_EVENT_SOURCE_CACHE: dict[str, str] = {}


class KalshiVenue(PredictionVenue):
    name = VenueName.KALSHI
    base_url = settings.kalshi_rest_url

    def __init__(self, fixture: bool = True):
        self.fixture = fixture

    async def _get(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=settings.http_timeout_seconds
        ) as client:
            payload = await get_json(client, path, params)
            return payload if isinstance(payload, dict) else {}

    async def list_markets(self, max_pages: int = 20) -> list[Market]:
        if self.fixture:
            return fixture_markets()[VenueName.KALSHI]
        markets: list[Market] = []
        cursor = ""
        for _ in range(max_pages):
            params = {"status": "open", "limit": 1000, "mve_filter": "exclude"}
            if cursor:
                params["cursor"] = cursor
            payload = await self._get("/markets", params)
            items = payload.get("markets", [])
            markets.extend(self._normalize_market(item) for item in items)
            cursor = payload.get("cursor", "")
            if not cursor or len(items) < 1000:
                return markets
        return markets

    async def list_settled_events(self, max_pages: int = 100) -> list[dict]:
        """List terminal event metadata without loading every historical market."""
        if self.fixture:
            return [
                {
                    "event_ticker": market.raw_market_json.get("event_ticker")
                    or market.event_subject,
                    "title": market.title,
                    "sub_title": market.subtitle or "",
                }
                for market in await self.list_markets()
            ]
        events: list[dict] = []
        cursor = ""
        for _ in range(max_pages):
            params: dict[str, object] = {"status": "settled", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            payload = await self._get("/events", params)
            items = payload.get("events", [])
            events.extend(item for item in items if isinstance(item, dict))
            cursor = str(payload.get("cursor") or "")
            if not cursor or len(items) < 200:
                break
        return events

    async def list_settled_event_markets(self, event_ticker: str) -> list[Market]:
        """Load recent and archived settled markets for one already-matched event."""
        if self.fixture:
            return [
                market
                for market in await self.list_markets()
                if str(market.raw_market_json.get("event_ticker") or market.event_subject)
                == event_ticker
            ]
        markets: dict[str, Market] = {}
        successful_requests = 0
        request_errors: list[httpx.HTTPError] = []
        for path, params in (
            (
                "/markets",
                {
                    "status": "settled",
                    "event_ticker": event_ticker,
                    "limit": 1000,
                    "mve_filter": "exclude",
                },
            ),
            (
                "/historical/markets",
                {
                    "event_ticker": event_ticker,
                    "limit": 1000,
                },
            ),
        ):
            try:
                payload = await self._get(path, params)
            except httpx.HTTPError as exc:
                request_errors.append(exc)
                continue
            successful_requests += 1
            for item in payload.get("markets", []):
                market = self._normalize_market(item)
                markets[market.market_id] = market
        if successful_requests == 0 and request_errors:
            raise request_errors[-1]
        return list(markets.values())

    async def get_market(self, market_id: str) -> Market:
        if self.fixture:
            return next(
                m
                for m in await self.list_markets()
                if m.market_id == market_id or m.venue_market_id == market_id
            )
        payload = await self._get(f"/markets/{market_id}")
        return self._normalize_market(payload.get("market", payload))

    async def enrich_market_source(self, market: Market) -> Market:
        if self.fixture:
            return market
        event_ticker = str(market.raw_market_json.get("event_ticker") or "")
        if not event_ticker:
            return market
        source = _EVENT_SOURCE_CACHE.get(event_ticker)
        if source is None:
            payload = await self._get(f"/events/{event_ticker}")
            event = payload.get("event", payload)
            source = _settlement_source_names(event.get("settlement_sources", []))
            _EVENT_SOURCE_CACHE[event_ticker] = source
        market.resolution_source = source
        market.raw_market_json["event_settlement_source"] = source
        return market

    async def get_orderbook(self, market_id: str) -> OrderBook:
        if self.fixture:
            key = market_id if market_id.startswith("kalshi:") else f"kalshi:{market_id}"
            return fixture_books()[key]
        payload = await self._get(f"/markets/{market_id}/orderbook")
        item = payload.get("orderbook_fp", payload.get("orderbook", payload))
        yes_bids = _levels(item.get("yes_dollars", []))
        no_bids = _levels(item.get("no_dollars", []))
        return OrderBook(
            venue=VenueName.KALSHI,
            market_id=f"kalshi:{market_id}",
            yes_bids=yes_bids,
            no_bids=no_bids,
            yes_asks=[
                OrderBookLevel(price=Decimal(1) - x.price, quantity=x.quantity) for x in no_bids
            ],
            no_asks=[
                OrderBookLevel(price=Decimal(1) - x.price, quantity=x.quantity) for x in yes_bids
            ],
        )

    @staticmethod
    def _normalize_market(item: dict) -> Market:
        ticker = item.get("ticker") or item.get("market_id")
        title = item.get("title") or item.get("subtitle") or ticker
        status = str(item.get("status", "active")).lower()
        normalized_status = (
            MarketStatus.SETTLED
            if status in {"finalized", "settled"}
            else MarketStatus.CLOSED
            if status in {"closed", "determined"}
            else MarketStatus.PAUSED
            if status in {"paused", "inactive"}
            else MarketStatus.ACTIVE
        )
        primary_rules = item.get("rules_primary") or ""
        secondary_rules = item.get("rules_secondary") or ""
        rules = "\n\n".join(value for value in (primary_rules, secondary_rules) if value)
        threshold = _rule_threshold(rules) or _plus_threshold(title)
        threshold_operator = _rule_threshold_operator(rules) or _plus_threshold_operator(title)
        threshold_unit = (
            _rule_threshold_unit(rules) or _plus_threshold_unit(title) or _threshold_unit(title)
        )
        return Market(
            market_id=f"kalshi:{ticker}",
            venue=VenueName.KALSHI,
            venue_market_id=ticker,
            title=title,
            subtitle=item.get("subtitle"),
            description=rules,
            resolution_source=item.get("settlement_sources") or "unknown",
            resolution_text=rules,
            event_subject=item.get("event_ticker") or ticker,
            event_action=title,
            threshold=threshold
            or (
                _decimal(item.get("floor_strike") or item.get("cap_strike"))
                if item.get("floor_strike") is not None or item.get("cap_strike") is not None
                else None
            ),
            threshold_upper=(
                _decimal(item.get("cap_strike"))
                if item.get("strike_type") == "between" and item.get("cap_strike") is not None
                else None
            ),
            threshold_operator=threshold_operator
            or {"greater": ">", "less": "<", "greater_or_equal": ">=", "less_or_equal": "<="}.get(
                item.get("strike_type")
            ),
            threshold_unit=threshold_unit,
            measurement_period=item.get("occurrence_datetime"),
            resolution_time=_parse_datetime(item.get("expiration_time")),
            market_type=item.get("market_type"),
            close_time=_parse_datetime(item.get("close_time")),
            status=normalized_status,
            volume=_decimal(item.get("volume_fp") or item.get("volume")),
            open_interest=_decimal(item.get("open_interest_fp") or item.get("open_interest")),
            raw_market_json=item,
            raw_rules_text=rules,
        )


def _decimal(value: object) -> Decimal:
    return Decimal(str(value or "0"))


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value)
    return datetime.fromisoformat(f"{text[:-1]}+00:00" if text.endswith("Z") else text)


def _threshold_unit(title: str) -> str | None:
    match = re.search(r"(?:over|under|more than|less than) [0-9.]+ ([a-z]+)", title.lower())
    return match.group(1) if match else None


def _settlement_source_names(values: list[dict]) -> str:
    names = [str(item.get("name", "")).strip() for item in values]
    return " | ".join(name for name in names if name) or "unknown"


def _plus_threshold(text: str) -> Decimal | None:
    match = re.search(r"\b([0-9]+(?:\.[0-9]+)?)\s*\+", text.lower())
    return Decimal(match.group(1)) if match else None


def _plus_threshold_operator(text: str) -> str | None:
    return ">=" if _plus_threshold(text) is not None else None


def _plus_threshold_unit(text: str) -> str | None:
    match = re.search(r"\b[0-9]+(?:\.[0-9]+)?\s*\+\s*([a-z][a-z-]*)", text.lower())
    return match.group(1) if match else None


def _rule_threshold(text: str) -> Decimal | None:
    return _plus_threshold(text) or _threshold_from_comparator(text)


def _rule_threshold_operator(text: str) -> str | None:
    if _plus_threshold(text) is not None:
        return ">="
    lowered = text.lower()
    if re.search(r"\b(at least|greater than or equal to)\b", lowered):
        return ">="
    if re.search(r"\b(at most|less than or equal to)\b", lowered):
        return "<="
    if "greater than" in lowered:
        return ">"
    if "less than" in lowered:
        return "<"
    return None


def _rule_threshold_unit(text: str) -> str | None:
    match = re.search(r"\b[0-9]+(?:\.[0-9]+)?\s*\+\s*([a-z][a-z-]*)", text.lower())
    return match.group(1) if match else _threshold_unit(text)


def _threshold_from_comparator(text: str) -> Decimal | None:
    match = re.search(
        r"\b(?:at least|at most|greater than(?: or equal to)?|less than(?: or equal to)?)\s+([0-9]+(?:\.[0-9]+)?)",
        text.lower(),
    )
    return Decimal(match.group(1)) if match else None


def _levels(values: list) -> list[OrderBookLevel]:
    return [OrderBookLevel(price=_decimal(row[0]), quantity=_decimal(row[1])) for row in values]
