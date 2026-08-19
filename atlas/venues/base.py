import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime

import httpx

from atlas.models import Market, OrderBook

TerminalEvidence = dict[str, object]

_SUBSECOND = re.compile(r"\.(\d+)")


def normalize_settled_at(value: object) -> str | None:
    """Normalize a venue settlement timestamp to an ISO-8601 UTC string.

    Venues publish settlement times in incompatible shapes: Kalshi's
    ``settlement_ts`` is microsecond precision, Polymarket US's
    ``settlementSetTime`` is nanosecond precision, and Gamma's ``closedTime``
    is a space-separated offset stamp. Sub-second digits are truncated to the
    six that ``datetime`` can represent, naive stamps are read as UTC, and
    anything unparseable returns ``None`` so a missing timestamp can never
    raise inside settlement evidence.
    """
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        text = text.replace("Z", "+00:00").replace("z", "+00:00")
        text = _SUBSECOND.sub(lambda match: "." + match.group(1)[:6], text, count=1)
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def pending_terminal_evidence(
    source: str,
    reason: str,
    *,
    retryable: bool,
    http_status: int | None = None,
) -> TerminalEvidence:
    evidence: TerminalEvidence = {
        "source": source,
        "status": "pending",
        "reason": reason,
        "retryable": retryable,
    }
    if http_status is not None:
        evidence["http_status"] = http_status
    return evidence


def classify_terminal_error(exc: httpx.HTTPError) -> tuple[str, bool, int | None]:
    """Classify a bounded public-API failure for the next settlement poll."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return "rate_limited", True, status
        if status >= 500:
            return "venue_server_error", True, status
        return "venue_client_error", False, status
    if isinstance(exc, httpx.TimeoutException):
        return "request_timeout", True, None
    if isinstance(exc, httpx.NetworkError):
        return "network_error", True, None
    return "venue_request_error", True, None


class PredictionVenue(ABC):
    name: str

    @abstractmethod
    async def list_markets(self) -> list[Market]: ...

    @abstractmethod
    async def get_market(self, market_id: str) -> Market: ...

    @abstractmethod
    async def get_orderbook(self, market_id: str) -> OrderBook: ...
