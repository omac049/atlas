import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable

from websockets.exceptions import WebSocketException


class StreamCollector:
    def __init__(self, reconnect_max_seconds: float = 30):
        self.reconnect_max_seconds = reconnect_max_seconds

    async def run_forever(
        self, connect: Callable[[], Awaitable[AsyncIterator[dict]]]
    ) -> AsyncIterator[dict]:
        delay = 1.0
        while True:
            try:
                stream = await connect()
                delay = 1.0
                async for event in stream:
                    yield event
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError, ValueError, WebSocketException):
                await asyncio.sleep(delay + random.random() * 0.25)
                delay = min(delay * 2, self.reconnect_max_seconds)
