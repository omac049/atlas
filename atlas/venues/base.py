from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from atlas.models import Market, OrderBook


class PredictionVenue(ABC):
    name: str

    @abstractmethod
    async def list_markets(self) -> list[Market]: ...

    @abstractmethod
    async def get_market(self, market_id: str) -> Market: ...

    @abstractmethod
    async def get_orderbook(self, market_id: str) -> OrderBook: ...

    async def stream_orderbook(self, market_ids: list[str]) -> AsyncIterator[OrderBook]:
        raise NotImplementedError("WebSocket collection is not enabled in the first scaffold")

    async def get_rules(self, market_id: str) -> str:
        return (await self.get_market(market_id)).raw_rules_text

    async def get_settlement(self, market_id: str) -> dict:
        return {}
