import base64
import json
import ssl
import time
from collections.abc import AsyncIterator

import certifi
from cryptography.hazmat.primitives.asymmetric import ed25519
from websockets.asyncio.client import connect


class PolymarketUSMarketStream:
    url = "wss://api.polymarket.us/v1/ws/markets"

    def __init__(self, api_headers: dict[str, str], market_slugs: list[str]):
        self.api_headers = api_headers
        self.market_slugs = market_slugs

    @staticmethod
    def auth_headers(api_key: str, secret_key: str, path: str = "/v1/ws/markets") -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        secret_bytes = base64.b64decode(secret_key)
        private_key = ed25519.Ed25519PrivateKey.from_private_bytes(secret_bytes[:32])
        signature = private_key.sign(f"{timestamp}GET{path}".encode())
        return {
            "X-PM-Access-Key": api_key,
            "X-PM-Timestamp": timestamp,
            "X-PM-Signature": base64.b64encode(signature).decode(),
        }

    async def messages(self) -> AsyncIterator[dict]:
        tls_context = ssl.create_default_context(cafile=certifi.where())
        async with connect(
            self.url, additional_headers=self.api_headers, ssl=tls_context
        ) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "subscribe": {
                            "requestId": "atlas-market-data",
                            "subscriptionType": "SUBSCRIPTION_TYPE_MARKET_DATA",
                            "marketSlugs": self.market_slugs,
                        }
                    }
                )
            )
            async for raw in websocket:
                yield json.loads(raw)
