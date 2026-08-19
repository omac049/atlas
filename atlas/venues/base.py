from abc import ABC, abstractmethod

import httpx

from atlas.models import Market, OrderBook

TerminalEvidence = dict[str, object]


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
