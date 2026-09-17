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
        """Apply one feed message. Shapes as Kalshi documents and sends them:

        snapshot  msg.yes_dollars_fp / msg.no_dollars_fp = [[price, size], ...]
        delta     msg.side, msg.price_dollars, msg.delta_fp (a SIGNED change)

        Until 2026-09-17 the snapshot was read under ``yes``/``yes_dollars`` (so
        every book started empty), the delta was stored as the level's size, and
        the NO side, which this subscription receives in YES prices, was mirrored
        a second time. The stored "books" were crossed 90-99% of the time and
        matched the venue's own REST book in nothing but the ticker.
        """
        global _observed_no_side_in_yes_prices
        msg_type = message.get("type")
        msg = message.get("msg", {})
        if msg_type == "orderbook_snapshot":
            yes = _levels(_first(msg, "yes_dollars_fp", "yes_dollars", "yes") or [])
            no = _levels(_first(msg, "no_dollars_fp", "no_dollars", "no") or [])
            seen = no_side_convention([lv.price for lv in yes], [lv.price for lv in no])
            if seen is not None:
                _observed_no_side_in_yes_prices = seen
            state.no_side_in_yes_prices = (
                seen if seen is not None
                else _observed_no_side_in_yes_prices
                if _observed_no_side_in_yes_prices is not None
                else NO_SIDE_IN_YES_PRICES_DEFAULT
            )
            if state.no_side_in_yes_prices:
                no = [_mirror(level) for level in no]
            state.apply_snapshot(yes, no, message.get("seq"))
            return True
        if msg_type == "orderbook_delta":
            side = str(_first(msg, "side", "book_side") or "yes")
            price = _decimal(_first(msg, "price_dollars", "price"))
            if side.lower().startswith("no") and state.no_side_in_yes_prices:
                price = 1 - price
            state.apply_kalshi_delta(
                side, price, _decimal(_first(msg, "delta_fp", "delta", "quantity") or 0),
                int(message["seq"]),
            )
            return True
        return False

    @staticmethod
    def snapshot_summary(ticker: str, message: dict, state: OrderBookState) -> str:
        """One log line per opening snapshot: what arrived, and how it was read."""
        msg = message.get("msg", {})
        yes = _first(msg, "yes_dollars_fp", "yes_dollars", "yes") or []
        no = _first(msg, "no_dollars_fp", "no_dollars", "no") or []
        prices = sorted(_decimal(row[0]) for row in no)
        return (
            f"kalshi_stream_snapshot {ticker} yes_levels={len(yes)} no_levels={len(no)} "
            f"best_yes={max((_decimal(row[0]) for row in yes), default=None)} "
            f"no_side_prices={prices[0] if prices else None}..{prices[-1] if prices else None} "
            f"no_side_in_yes_prices={state.no_side_in_yes_prices}"
        )


# What the feed does under this subscription (use_yes_price), measured against
# the REST book on 2026-09-17: NO-side levels arrived at 0.52-0.81 while the
# venue's NO bids sat at 0.01-0.48. Every two-sided snapshot re-measures it.
NO_SIDE_IN_YES_PRICES_DEFAULT = True
_observed_no_side_in_yes_prices: bool | None = None


def no_side_convention(yes_prices: list, no_prices: list) -> bool | None:
    """True when the NO side is expressed in YES prices, False when in NO prices,
    None when this snapshot cannot tell (a side is empty, or both readings fit).

    In NO prices a resting book obeys best YES bid + best NO bid < 1. In YES
    prices the NO side is the ask side, so every NO-side price sits above the
    best YES bid. A two-sided book of any depth satisfies exactly one.
    """
    if not yes_prices or not no_prices:
        return None
    fits_no_prices = max(yes_prices) + max(no_prices) < 1
    fits_yes_prices = min(no_prices) > max(yes_prices)
    if fits_no_prices == fits_yes_prices:
        return None
    return fits_yes_prices


def _first(msg: dict, *keys: str):
    for key in keys:
        value = msg.get(key)
        if value is not None:
            return value
    return None


def _decimal(value: object):
    from decimal import Decimal

    return Decimal(str(value))


def _mirror(level):
    from atlas.models import OrderBookLevel

    return OrderBookLevel(price=1 - level.price, quantity=level.quantity)


def _levels(values: list) -> list:
    """[[price, size], ...] as levels; a zero-size row is not a level."""
    from atlas.models import OrderBookLevel

    rows = [(_decimal(row[0]), _decimal(row[1])) for row in values]
    return [OrderBookLevel(price=price, quantity=size) for price, size in rows if size > 0]
