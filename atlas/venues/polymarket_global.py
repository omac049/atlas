"""Read-only Polymarket Global history for settlement-validation research.

This adapter intentionally exposes no order-book or order-placement interface. Global
markets are evidence inputs only and cannot enter Atlas's live execution pipeline.
It also deliberately does NOT subclass ``PredictionVenue``: implementing that
interface would require an order-book method, which this venue must never have.

Note on retryability: ``get_terminal_settlement_evidence`` reports
``retryable=False`` for ambiguous *settled* outcomes (non-Yes/No outcome lists,
non-binary terminal prices). That is correct, not a bug — a market that already
settled with a non-binary result will never become binary, so re-polling it is
pointless. Only ``resolution_not_final`` (UMA still deliberating) is retryable.
"""

import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx

from atlas.config import settings
from atlas.models import Market, MarketStatus, VenueName
from atlas.venues.base import classify_terminal_error, pending_terminal_evidence
from atlas.venues.http import get_json


class PolymarketGlobalHistoricalVenue:
    name = VenueName.POLYMARKET_GLOBAL
    base_url = settings.polymarket_global_public_url

    def __init__(self, tag_ids: tuple[str, ...] | None = None) -> None:
        self._closed_markets: dict[str, dict] = {}
        self.tag_ids = tuple(tag_ids or ())
        self.catalog_scope = "tagged:" + ",".join(self.tag_ids) if self.tag_ids else "all"

    async def _get(self, path: str, params: dict | None = None) -> dict | list:
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=settings.http_timeout_seconds
        ) as client:
            return await get_json(client, path, params)

    async def list_closed_markets(self, max_pages: int = 20) -> list[Market]:
        markets: list[Market] = []
        seen: set[str] = set()
        limit = 100
        for tag_id in self.tag_ids or (None,):
            for page in range(max_pages):
                params = {
                    "closed": True,
                    "limit": limit,
                    "offset": page * limit,
                    "order": "closedTime",
                    "ascending": False,
                }
                if tag_id is not None:
                    params["tag_id"] = tag_id
                payload = await self._get("/markets", params=params)
                items = payload if isinstance(payload, list) else []
                for item in items:
                    market_id = str(item.get("id") or "")
                    if not market_id or market_id in seen:
                        continue
                    seen.add(market_id)
                    self._closed_markets[market_id] = item
                    markets.append(self._normalize_market(item))
                if len(items) < limit:
                    break
        return markets

    async def list_open_markets(self, max_pages: int = 2) -> list[Market]:
        """List tag-scoped open markets for settlement-candidate discovery.

        Open Global markets are research inputs for the queue only: this adapter
        still exposes no order books, so they can never reach shadow, approval,
        or paper-trading paths that require executable prices.
        """
        markets: list[Market] = []
        seen: set[str] = set()
        limit = 100
        for tag_id in self.tag_ids or (None,):
            for page in range(max_pages):
                params = {
                    "closed": False,
                    "active": True,
                    "limit": limit,
                    "offset": page * limit,
                    "order": "volumeNum",
                    "ascending": False,
                }
                if tag_id is not None:
                    params["tag_id"] = tag_id
                payload = await self._get("/markets", params=params)
                items = payload if isinstance(payload, list) else []
                for item in items:
                    market_id = str(item.get("id") or "")
                    if not market_id or market_id in seen:
                        continue
                    seen.add(market_id)
                    markets.append(self._normalize_market(item))
                if len(items) < limit:
                    break
        return markets

    async def list_event_markets(self, event_slug: str) -> list[Market]:
        """Targeted event-slug lookup (``/events?slug=``) — the door to settled
        Gamma macro ladders with no known tag ID (e.g. the June 2026 core-PCE
        events), mirroring the Polymarket US ``list_event_markets`` path. The
        payload nests markets inside the event without an ``events`` back-link,
        so the parent event (sans its market list) is reattached for
        normalization. Bounded by the shared ``get_json`` retry/timeout budget;
        an unknown slug yields an empty list."""
        payload = await self._get("/events", params={"slug": event_slug})
        event = next(iter(payload), {}) if isinstance(payload, list) else {}
        if not isinstance(event, dict):
            return []
        items = [item for item in event.get("markets") or [] if isinstance(item, dict)]
        parent = {key: value for key, value in event.items() if key != "markets"}
        markets: list[Market] = []
        for item in items:
            enriched = {**item, "events": [parent]}
            market_id = str(enriched.get("id") or "")
            if market_id:
                self._closed_markets[market_id] = enriched
            markets.append(self._normalize_market(enriched))
        return markets

    async def get_terminal_settlement_evidence(self, market_id: str) -> dict:
        item = self._closed_markets.get(str(market_id))
        if item is None:
            try:
                payload = await self._get(f"/markets/{market_id}")
            except httpx.HTTPError as exc:
                reason, retryable, http_status = classify_terminal_error(exc)
                return pending_terminal_evidence(
                    "polymarket_global_gamma_closed_market",
                    reason,
                    retryable=retryable,
                    http_status=http_status,
                )
            item = payload if isinstance(payload, dict) else {}
        if item.get("closed") is not True:
            return pending_terminal_evidence(
                "polymarket_global_gamma_closed_market", "not_terminal", retryable=True
            )
        if str(item.get("umaResolutionStatus") or "").lower() != "resolved":
            return pending_terminal_evidence(
                "polymarket_global_gamma_closed_market",
                "resolution_not_final",
                retryable=True,
            )
        outcomes = _json_list(item.get("outcomes"))
        prices = _json_list(item.get("outcomePrices"))
        if len(outcomes) != 2 or len(prices) != 2:
            return pending_terminal_evidence(
                "polymarket_global_gamma_closed_market",
                "terminal_result_missing",
                retryable=True,
            )
        normalized = [str(outcome).strip().lower() for outcome in outcomes]
        if set(normalized) != {"yes", "no"}:
            return pending_terminal_evidence(
                "polymarket_global_gamma_closed_market",
                "ambiguous_outcomes",
                retryable=False,
            )
        parsed_prices = [_binary_decimal(price) for price in prices]
        if any(price is None for price in parsed_prices) or set(parsed_prices) != {
            Decimal(0),
            Decimal(1),
        }:
            return pending_terminal_evidence(
                "polymarket_global_gamma_closed_market",
                "ambiguous_terminal_prices",
                retryable=False,
            )
        yes_index = normalized.index("yes")
        settlement = parsed_prices[yes_index]
        return {
            "source": "polymarket_global_gamma_closed_market",
            "status": "settled",
            "reason": "resolved_gamma_market",
            "retryable": False,
            "settlement": str(settlement),
            "closed": True,
            "closed_time": item.get("closedTime"),
            "uma_resolution_status": "resolved",
            "resolved_by": item.get("resolvedBy"),
            "automatically_resolved": item.get("automaticallyResolved"),
            "outcomes": outcomes,
            "outcome_prices": prices,
            "market_id": str(item.get("id") or market_id),
        }

    @staticmethod
    def _normalize_market(item: dict) -> Market:
        venue_id = str(item.get("id") or item.get("slug") or "")
        title = str(item.get("question") or item.get("slug") or venue_id)
        event = next(iter(item.get("events") or []), {})
        description = _rule_text(item)
        event_title = str(event.get("title") or "")
        return Market(
            market_id=f"polymarket_global:{venue_id}",
            venue=VenueName.POLYMARKET_GLOBAL,
            venue_market_id=venue_id,
            title=title,
            description=description or None,
            outcome_yes_label="Yes",
            outcome_no_label="No",
            category=item.get("category") or event.get("category"),
            open_time=_parse_datetime(item.get("startDate")),
            close_time=_parse_datetime(item.get("endDate")),
            resolution_time=_parse_datetime(item.get("closedTime")),
            resolution_source=_resolution_source(item, event),
            resolution_text=description,
            event_subject=str(
                event.get("ticker") or event.get("slug") or item.get("slug") or venue_id
            ),
            event_action=title,
            threshold=_threshold(title),
            threshold_operator=_threshold_operator(title),
            threshold_unit=_threshold_unit(title),
            measurement_period=item.get("gameStartTime") or event.get("eventDate"),
            market_type=item.get("marketType") or item.get("sportsMarketType"),
            participants=_participants(event_title),
            status=MarketStatus.CLOSED if item.get("closed") else MarketStatus.ACTIVE,
            volume=_decimal(item.get("volumeNum") or item.get("volume")),
            open_interest=Decimal(0),
            raw_market_json=item,
            raw_rules_text=description,
        )


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _rule_text(item: dict) -> str:
    """Preserve explicit historical rule fields for settlement verification."""
    for key in (
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


def _resolution_source(item: dict, event: dict) -> str:
    for source in (
        item.get("resolutionSource"),
        item.get("resolution_source"),
        item.get("settlementSource"),
        event.get("resolutionSource"),
    ):
        if source:
            return str(source)
    return "unknown"


def _binary_decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed in {Decimal(0), Decimal(1)} else None


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value)
    return datetime.fromisoformat(f"{text[:-1]}+00:00" if text.endswith("Z") else text)


def _threshold(text: str) -> Decimal | None:
    match = re.search(
        r"(?:over|under|more than|less than|at least|at most)\s+\$?([0-9]+(?:\.[0-9]+)?)",
        text.lower(),
    )
    return Decimal(match.group(1)) if match else None


def _threshold_operator(text: str) -> str | None:
    lowered = text.lower()
    if "at least" in lowered:
        return ">="
    if "at most" in lowered:
        return "<="
    if "more than" in lowered or "over" in lowered:
        return ">"
    if "less than" in lowered or "under" in lowered:
        return "<"
    return None


def _threshold_unit(text: str) -> str | None:
    lowered = text.lower()
    for term, unit in (
        ("degrees", "degrees"),
        ("percent", "percent"),
        ("points", "points"),
        ("votes", "votes"),
        ("seats", "seats"),
    ):
        if term in lowered:
            return unit
    return None


def _participants(event_title: str) -> list[str]:
    values = re.split(r"\s+(?:vs\.?|versus)\s+", event_title, flags=re.IGNORECASE)
    return [value.strip() for value in values if value.strip()] if len(values) == 2 else []
