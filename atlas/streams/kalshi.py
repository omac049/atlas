import base64
import json
import ssl
import time
from pathlib import Path
from typing import Any

import certifi
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from websockets.asyncio.client import connect

from atlas.orderbooks.state import OrderBookState


class KalshiOrderBookStream:
    url = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"

    def __init__(self, api_key_id: str, private_key_path: str, market_tickers: list[str]):
        self.api_key_id = api_key_id
        self.private_key_path = Path(private_key_path).expanduser()
        self.market_tickers = market_tickers
        self.subscription_id = 1

    def _headers(self) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}GET/trade-api/ws/v2".encode()
        private_key = serialization.load_pem_private_key(
            self.private_key_path.read_bytes(), password=None
        )
        signature = private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }

    async def messages(self) -> Any:
        tls_context = ssl.create_default_context(cafile=certifi.where())
        async with connect(
            self.url, additional_headers=self._headers(), ssl=tls_context
        ) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "id": self.subscription_id,
                        "cmd": "subscribe",
                        "params": {
                            "channels": ["orderbook_delta"],
                            "market_tickers": self.market_tickers,
                            "use_yes_price": True,
                        },
                    }
                )
            )
            async for raw in websocket:
                yield json.loads(raw)

    @staticmethod
    def apply_message(state: OrderBookState, message: dict) -> bool:
        msg_type = message.get("type")
        msg = message.get("msg", {})
        if msg_type == "orderbook_snapshot":
            state.apply_snapshot(
                _levels(msg.get("yes", msg.get("yes_dollars", []))),
                _levels(msg.get("no", msg.get("no_dollars", []))),
                message.get("seq"),
            )
            return True
        if msg_type == "orderbook_delta":
            state.apply_kalshi_delta(
                msg.get("side", msg.get("book_side", "yes")),
                _decimal(msg.get("price_dollars", msg.get("price"))),
                _decimal(msg.get("delta_fp", msg.get("quantity", 0))),
                int(message["seq"]),
            )
            return True
        return False


def _decimal(value: object):
    from decimal import Decimal

    return Decimal(str(value))


def _levels(values: list) -> list:
    from atlas.models import OrderBookLevel

    return [OrderBookLevel(price=_decimal(row[0]), quantity=_decimal(row[1])) for row in values]
