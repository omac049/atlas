import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx

from atlas.config import settings
from atlas.models import Market, MarketStatus, OrderBook, OrderBookLevel, VenueName
from atlas.venues.base import (
    PredictionVenue,
    classify_terminal_error,
    normalize_settled_at,
    pending_terminal_evidence,
)
from atlas.venues.fixtures import fixture_books, fixture_markets
from atlas.venues.http import get_json

# Order-book states in which the gateway's settlement price is final.
_TERMINAL_STATES = {"MARKET_STATE_EXPIRED", "MARKET_STATE_TERMINATED"}


def _book_settled_at(market_data: object) -> str | None:
    """Recover the settlement time from a `/book` payload.

    ``stats.settlementSetTime`` is the gateway's authoritative stamp;
    ``transactTime`` carries the same value on settled books and is used only
    as a fallback. Both are nanosecond precision, so they must be normalized.
    """
    if not isinstance(market_data, dict):
        return None
    stats = market_data.get("stats")
    raw = stats.get("settlementSetTime") if isinstance(stats, dict) else None
    return normalize_settled_at(raw or market_data.get("transactTime"))


class PolymarketUSVenue(PredictionVenue):
    name = VenueName.POLYMARKET_US
    base_url = settings.polymarket_us_public_url

    def __init__(self, fixture: bool = True):
        self.fixture = fixture

    async def _get(
        self, path: str, params: dict | list[tuple[str, str]] | None = None
    ) -> dict | list:
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=settings.http_timeout_seconds
        ) as client:
            return await get_json(client, path, params)

    async def list_markets(self, max_pages: int = 20) -> list[Market]:
        if self.fixture:
            return fixture_markets()[VenueName.POLYMARKET_US]
        markets: list[Market] = []
        seen: set[str] = set()
        limit, offset = 100, 0
        for _ in range(max_pages):
            payload = await self._get(
                "/v1/events",
                params={
                    "active": True, "closed": False, "with_nested_markets": True,
                    "limit": limit, "offset": offset,
                },
            )
            events = payload.get("events", []) if isinstance(payload, dict) else []
            items = [market for event in events for market in event.get("markets", [])]
            for item in items:
                slug = item.get("slug") or item.get("marketSlug") or item.get("id")
                if slug not in seen:
                    seen.add(slug)
                    markets.append(self._normalize_market(item))
            # Short pages occur mid-stream on this gateway (verified 2026-08-13),
            # so only an EMPTY page terminates the sweep; max_pages stays the
            # hard bound either way.
            if not events:
                return markets
            offset += limit
        return markets

    async def list_open_category_markets(
        self, categories: tuple[str, ...], max_pages: int = 40
    ) -> list[Market]:
        """Open markets in the named gateway categories, filtered during paging.

        The radar needs a BOUNDED Polymarket US universe. The full open sweep is
        ~22k markets and costs ~56s just to fingerprint (measured 2026-08-20),
        which a 30-second release-window burst cannot afford. Categories are the
        gateway's own published field, so this narrows the scope without
        inferring anything: ``macro`` carries the FOMC/CPI/GDP/payrolls/
        unemployment ladders and ``politics`` the chamber-control contracts.

        Filtering happens before normalization, so a skipped event costs nothing
        beyond the page fetch it already shared with the wanted ones.
        """
        if self.fixture:
            return [
                market
                for market in fixture_markets()[VenueName.POLYMARKET_US]
                if market.status == MarketStatus.ACTIVE
            ]
        wanted = {category.lower() for category in categories}
        markets: list[Market] = []
        seen: set[str] = set()
        limit, offset = 100, 0
        for _ in range(max_pages):
            # Two silent gateway traps here, both verified live 2026-08-20:
            #   * the SINGULAR `category=macro` is ignored and returns the
            #     unfiltered catalog;
            #   * a comma-joined `categories=macro,politics` returns zero
            #     events with HTTP 200 — a silent empty, not an error.
            # Only REPEATED `categories` params filter correctly, so they are
            # sent as a param list. The client-side re-check below is the
            # guard: if this ever stops filtering, scope must not silently
            # widen to all ~22k open markets.
            params: list[tuple[str, str]] = [
                ("active", "true"),
                ("closed", "false"),
                ("with_nested_markets", "true"),
                ("limit", str(limit)),
                ("offset", str(offset)),
            ]
            params.extend(("categories", category) for category in sorted(wanted))
            payload = await self._get("/v1/events", params=params)
            events = payload.get("events", []) if isinstance(payload, dict) else []
            if not events:
                return markets
            for event in events:
                if str(event.get("category") or "").lower() not in wanted:
                    continue
                for item in event.get("markets", []):
                    slug = item.get("slug") or item.get("marketSlug") or item.get("id")
                    if slug in seen:
                        continue
                    seen.add(slug)
                    markets.append(self._normalize_market(item))
            offset += limit
        return markets

    async def list_closed_markets(self, max_pages: int = 20) -> list[Market]:
        """List closed public markets for settlement-verified research.

        On this gateway ``active`` means "not manually deactivated" — resolved
        markets keep ``active: true`` — so the filter must be ``closed`` only.
        ``orderDirection`` is silently ignored unless ``orderBy`` is also sent
        (both verified live 2026-08-13).
        """
        if self.fixture:
            return await self.list_markets()
        markets: list[Market] = []
        seen: set[str] = set()
        limit = 100
        for page in range(max_pages):
            payload = await self._get(
                "/v1/markets",
                params={
                    "closed": True,
                    "limit": limit,
                    "offset": page * limit,
                    "orderBy": "id",
                    "orderDirection": "desc",
                },
            )
            items = payload.get("markets", []) if isinstance(payload, dict) else []
            for item in items:
                slug = str(item.get("slug") or item.get("marketSlug") or item.get("id"))
                if slug not in seen:
                    seen.add(slug)
                    markets.append(self._normalize_market(item))
            if not items:
                break
        return markets

    async def list_markets_by_slugs(self, slugs: list[str]) -> list[Market]:
        """Targeted slug lookups — the efficient path to macro markets buried
        under the ~400k-market closed sweep. Missing slugs are skipped."""
        if self.fixture:
            wanted = set(slugs)
            return [
                market
                for market in await self.list_markets()
                if market.venue_market_id in wanted
            ]
        markets: list[Market] = []
        for slug in slugs:
            try:
                markets.append(await self.get_market(slug))
            except httpx.HTTPError:
                continue
        return markets

    async def list_event_markets(self, event_slug: str) -> list[Market]:
        """Targeted event-slug lookup (plural ``/v1/events/slug/{slug}``) — the
        door to settled macro ladders buried under the ~400k-row closed sweep
        (e.g. resolved CPI months whose market ids sit far below the recent-id
        horizon). Returns the event's nested markets; an unknown slug yields an
        empty list. Bounded by the shared ``get_json`` retry/timeout budget —
        no additional retries here."""
        if self.fixture:
            return []
        try:
            payload = await self._get(f"/v1/events/slug/{event_slug}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise
        event = payload.get("event", payload) if isinstance(payload, dict) else {}
        items = event.get("markets", []) if isinstance(event, dict) else []
        return [self._normalize_market(item) for item in items if isinstance(item, dict)]

    async def get_market(self, market_id: str) -> Market:
        if self.fixture:
            market = next(
                (
                    m
                    for m in await self.list_markets()
                    if m.market_id == market_id or m.venue_market_id == market_id
                ),
                None,
            )
            if market is None:
                raise ValueError(f"unknown fixture market {market_id}")
            return market
        path = (
            f"/v1/market/id/{market_id}"
            if str(market_id).isdigit()
            else f"/v1/market/slug/{market_id}"
        )
        payload = await self._get(path)
        return self._normalize_market(
            payload.get("market", payload) if isinstance(payload, dict) else {}
        )

    async def get_orderbook(self, market_id: str) -> OrderBook:
        if self.fixture:
            key = (
                market_id
                if market_id.startswith("polymarket_us:")
                else f"polymarket_us:{market_id}"
            )
            return fixture_books()[key]
        payload = await self._get(f"/v1/markets/{market_id}/book")
        item = payload.get("marketData", payload) if isinstance(payload, dict) else {}
        bids = _levels(item.get("bids", []), "px", "qty")
        offers = _levels(item.get("offers", []), "px", "qty")
        return OrderBook(
            venue=VenueName.POLYMARKET_US,
            market_id=f"polymarket_us:{market_id}",
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

    async def get_settlement(self, market_id: str) -> dict:
        if self.fixture:
            return {}
        payload = await self._get(f"/v1/markets/{market_id}/settlement")
        return payload if isinstance(payload, dict) else {}

    async def _settlement_timestamp(self, market_id: str) -> str | None:
        """Best-effort settlement time for a market the settlement endpoint settled.

        Strictly observability: every failure path returns ``None`` so that a
        book outage can never turn final settlement evidence into pending
        evidence.
        """
        try:
            book = await self._get(f"/v1/markets/{market_id}/book")
        except httpx.HTTPError:
            return None
        return _book_settled_at(book.get("marketData") if isinstance(book, dict) else None)

    async def get_terminal_settlement_evidence(self, market_id: str) -> dict:
        """Return final binary public evidence, preferring the settlement endpoint."""
        if self.fixture:
            return pending_terminal_evidence(
                "polymarket_us_settlement", "fixture_mode", retryable=False
            )
        endpoint_error: httpx.HTTPError | None = None
        try:
            payload = await self.get_settlement(market_id)
        except httpx.HTTPError as exc:
            payload = {}
            endpoint_error = exc
        settlement = payload.get("settlement") if isinstance(payload, dict) else None
        if _binary_decimal(settlement) is not None:
            return {
                "source": "settlement_endpoint",
                "status": "settled",
                "reason": "settlement_endpoint",
                "retryable": False,
                "settlement": str(_binary_decimal(settlement)),
                # The settlement endpoint carries no timestamp, so the book is
                # consulted purely for settlement timing. Best-effort by design:
                # a failed lookup leaves settled_at None and never downgrades
                # otherwise-final evidence.
                "settled_at": await self._settlement_timestamp(market_id),
                "payload": payload,
            }
        try:
            book = await self._get(f"/v1/markets/{market_id}/book")
        except httpx.HTTPError as exc:
            # Classify the book failure only: the settlement endpoint 404s for
            # every unsettled market, so it must never shadow this request.
            reason, retryable, http_status = classify_terminal_error(exc)
            evidence = pending_terminal_evidence(
                "polymarket_us_terminal_book",
                reason,
                retryable=retryable,
                http_status=http_status,
            )
            if isinstance(endpoint_error, httpx.HTTPStatusError):
                evidence["settlement_endpoint_http_status"] = (
                    endpoint_error.response.status_code
                )
            return evidence
        market_data = book.get("marketData", {}) if isinstance(book, dict) else {}
        stats = market_data.get("stats", {}) or {}
        raw_price = stats.get("settlementPx")
        value = raw_price.get("value") if isinstance(raw_price, dict) else raw_price
        binary = _binary_decimal(value)
        state = str(market_data.get("state") or "")
        preliminary = stats.get("settlementPreliminaryFlag")
        # The gateway serializes this flag inconsistently (bool, "true", 1).
        is_preliminary = str(preliminary).lower() in {"true", "1"}
        if binary is None or state not in _TERMINAL_STATES or is_preliminary:
            reason = (
                "preliminary_terminal_result"
                if is_preliminary
                else "not_terminal"
                if state not in _TERMINAL_STATES
                else "terminal_result_missing"
            )
            return pending_terminal_evidence(
                "polymarket_us_terminal_book", reason, retryable=True
            )
        return {
            "source": "terminal_market_book",
            "status": "settled",
            "reason": "terminal_market_book",
            "retryable": False,
            "settlement": str(binary),
            "state": state,
            "preliminary": is_preliminary,
            "settled_at": _book_settled_at(market_data),
            # Deterministic subset only: embedding the full order book made the
            # validation evidence hash churn with quote noise.
            "payload": {
                "market_slug": market_id,
                "state": state,
                "settlement": str(binary),
                "preliminary": is_preliminary,
            },
        }

    @staticmethod
    def _normalize_market(item: dict) -> Market:
        slug = item.get("slug") or item.get("marketSlug") or item.get("id")
        title = item.get("title") or item.get("question") or slug
        raw_status = str(item.get("status", "MARKET_STATUS_OPEN")).lower()
        status = (
            MarketStatus.SETTLED
            if "resolved" in raw_status or "settled" in raw_status
            else MarketStatus.CLOSED
            if bool(item.get("closed"))
            or any(value in raw_status for value in ("closed", "expired"))
            else MarketStatus.PAUSED
            if any(value in raw_status for value in ("suspend", "halt", "term"))
            else MarketStatus.ACTIVE
        )
        yes_label, no_label = _outcome_labels(item)
        rule_text = _rule_text(item)
        return Market(
            market_id=f"polymarket_us:{slug}",
            venue=VenueName.POLYMARKET_US,
            venue_market_id=slug,
            title=title,
            subtitle=item.get("subtitle"),
            description=item.get("description") or rule_text,
            outcome_yes_label=yes_label,
            outcome_no_label=no_label,
            resolution_source=_resolution_source(item),
            resolution_text=rule_text,
            event_subject=item.get("eventSubject") or slug,
            event_action=item.get("eventAction") or title,
            status=status,
            raw_market_json=item,
            raw_rules_text=rule_text,
            open_time=_parse_datetime(item.get("startDate")),
            close_time=_parse_datetime(item.get("endDate")),
            resolution_time=_parse_datetime(item.get("endDate")),
            measurement_period=item.get("gameStartTime"),
            market_type=item.get("marketType") or item.get("sportsMarketTypeV2"),
            threshold=_threshold(item.get("question") or title),
            threshold_upper=_threshold_upper(
                " ".join(
                    str(value)
                    for value in (item.get("question"), title, item.get("description"))
                    if value
                )
            ),
            threshold_operator=_threshold_operator(item.get("question") or title),
            threshold_unit=_threshold_unit(item.get("question") or title) or _unit_from_slug(slug),
            participants=_participants(item),
        )


def _levels(values: list, price_key: str | int, quantity_key: str | int) -> list[OrderBookLevel]:
    def scalar(value: object) -> object:
        return value.get("value") if isinstance(value, dict) else value

    return [
        OrderBookLevel(
            price=Decimal(str(scalar(row[price_key]))),
            quantity=Decimal(str(scalar(row[quantity_key]))),
        )
        for row in values
    ]


def _binary_decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed in {Decimal(0), Decimal(1)} else None


def _market_items(payload: dict | list) -> list[dict]:
    if isinstance(payload, list):
        return payload
    return payload.get("markets", payload.get("data", []))


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value)
    return datetime.fromisoformat(f"{text[:-1]}+00:00" if text.endswith("Z") else text)


def _participants(item: dict) -> list[str]:
    names = {
        side.get("team", {}).get("name")
        for side in item.get("marketSides", [])
        if side.get("team", {}).get("name")
    }
    return sorted(names)


def _outcome_labels(item: dict) -> tuple[str, str]:
    sides = item.get("marketSides", [])
    long_side = next((side for side in sides if side.get("long") is True), None)
    short_side = next((side for side in sides if side.get("long") is False), None)

    def label(side: dict | None, fallback: str) -> str:
        if not side:
            return fallback
        return (
            side.get("team", {}).get("name")
            or side.get("description")
            or fallback
        )

    return label(long_side, "Yes"), label(short_side, "No")


def _resolution_source(item: dict) -> str:
    for key in ("resolutionSource", "resolution_source", "settlementSource", "settlement_source"):
        if item.get(key):
            return str(item[key])
    text = _rule_text(item)
    match = re.search(r"outcome (?:sourced|verified) from\s+([^.;]+)", text, re.IGNORECASE)
    return match.group(1).strip() if match else "unknown"


def _rule_text(item: dict) -> str:
    """Preserve explicit venue rule fields for deterministic evidence parsing."""
    for key in (
        "resolutionText",
        "resolutionRules",
        "resolution_rules",
        "rules",
        "ruleText",
        "rule_text",
        "description",
    ):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _threshold(text: str) -> Decimal | None:
    match = re.search(r"(?:over|under|cover|more than|less than)\s+([0-9]+(?:\.[0-9]+)?)", text.lower())
    match = match or re.search(r"\b([0-9]+(?:\.[0-9]+)?)\s*\+", text.lower())
    return Decimal(match.group(1)) if match else None


def _threshold_upper(text: str) -> Decimal | None:
    match = re.search(
        r"between\s+\$?([0-9][0-9,.]*)\s*(?:°?f|fahrenheit|%)?\s+"
        r"(?:and|to|-)\s*\$?([0-9][0-9,.]*)",
        text.lower(),
    )
    return Decimal(match.group(2).replace(",", "")) if match else None


def _threshold_operator(text: str) -> str | None:
    lowered = text.lower()
    if "under" in lowered or "less than" in lowered:
        return "<"
    if re.search(r"\b[0-9]+(?:\.[0-9]+)?\s*\+", lowered):
        return ">="
    if "over" in lowered or "more than" in lowered or "cover" in lowered:
        return ">"
    return None


def _threshold_unit(text: str) -> str | None:
    match = re.search(r"(?:over|under|cover|more than|less than)\s+[0-9]+(?:\.[0-9]+)?\s+([a-z]+)", text.lower())
    match = match or re.search(r"\b[0-9]+(?:\.[0-9]+)?\s*\+\s*([a-z][a-z-]*)", text.lower())
    unit = match.group(1) if match else None
    return None if unit in {"vs", "the"} else unit


def _unit_from_slug(slug: str) -> str | None:
    lowered = slug.lower()
    if any(token in lowered for token in ("mlb", "baseball")):
        return "runs"
    if any(token in lowered for token in ("fwc", "soccer", "mls", "epl", "uefa")):
        return "goals"
    if any(token in lowered for token in ("nba", "nfl", "wnba", "ncaab", "ncaaf")):
        return "points"
    return None
