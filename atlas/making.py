"""Fifth charter: a naive market-making rule, replayed on Kalshi tapes.

Paper-only. This module reads published trade prints and Atlas's own recorded
order books, and computes what a fixed quoting rule would have been filled at,
charged, and left holding. It places nothing and reads no credentials.

Every parameter and threshold below is fixed by
docs/decisions/2026-09-08-market-making-charter.md (sections 4 to 6). Nothing
here may change after that charter's freeze commit.

Prices are handled in whole cents as integers wherever they are compared, and
as Decimal dollars wherever money is counted, so no float ever touches a
result.
"""

from __future__ import annotations

import bisect
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal(0)
ONE = Decimal(1)
# Kalshi's published maker formula (July 7, 2026 schedule): ceil(0.0175 * C * P * (1 - P)),
# rounded up to the cent. Fed decisions and MLB both carry maker multiplier 1.
MAKER_RATE = Decimal("0.0175")
# The taker ceiling atlas/gap_radar.py applies: ceil to the cent per contract at 0.07.
# Sensitivity only; never the primary fee.
TAKER_RATE = Decimal("0.07")
REFERENCE_PRINTS = 20  # Arm B reference: VWAP of the last 20 non-block prints

# Section 5: sample floors.
ARM_A_MIN_MARKETS = 4
ARM_A_MIN_PRINTS = 500
ARM_B_MIN_GAMES = 100
# Section 6: pass criteria.
MIN_POSITIVE_SHARE = Decimal("0.6")
MIN_NET_PER_CONTRACT = Decimal("0.005")
SLOW_LATENCY_SECONDS = 5.0


@dataclass(frozen=True)
class Params:
    """Section 4. The primary rule is the default instance."""

    half_spread_cents: int = 2
    quantity: int = 10
    latency_seconds: float = 1.0
    requote_seconds: float = 30.0
    inventory_cap: int = 100
    fee: str = "maker"  # "maker" (primary) or "taker" (sensitivity)

    def label(self) -> str:
        return (f"s={self.half_spread_cents}c q={self.quantity} L={self.latency_seconds:g}s "
                f"delta={self.requote_seconds:g}s fee={self.fee}")


PRIMARY = Params()
SLOW = Params(latency_seconds=SLOW_LATENCY_SECONDS)
GRID_HALF_SPREADS = (1, 2, 3, 5)
GRID_REQUOTES = (5.0, 30.0, 300.0)
GRID_LATENCIES = (1.0, 5.0)
GRID_FEES = ("maker", "taker")


@dataclass(frozen=True)
class Print:
    trade_id: str
    at: datetime
    price_cents: int  # YES price
    count: Decimal  # contracts, fractional on Kalshi (count_fp)
    taker_bought_yes: bool  # taker_side == "yes"; False means the taker sold YES
    is_block: bool


def parse_prints(rows: Iterable[dict]) -> list[Print]:
    """Kalshi /markets/trades rows, typed and sorted by time; block trades kept but flagged."""
    prints: list[Print] = []
    for row in rows:
        try:
            price = Decimal(str(row["yes_price_dollars"]))
            prints.append(
                Print(
                    trade_id=str(row.get("trade_id")),
                    at=datetime.fromisoformat(str(row["created_time"])),
                    price_cents=int((price * 100).quantize(ONE, rounding=ROUND_HALF_UP)),
                    count=Decimal(str(row.get("count_fp") or "0")),
                    taker_bought_yes=str(row.get("taker_side", "")).lower() == "yes",
                    is_block=bool(row.get("is_block_trade")),
                )
            )
        except (KeyError, ValueError, ArithmeticError, TypeError):
            continue
    return sorted(prints, key=lambda p: p.at)


@dataclass(frozen=True)
class Level:
    price_cents: int
    quantity: Decimal


@dataclass(frozen=True)
class Book:
    at: datetime
    bids: tuple[Level, ...]  # YES bids, best (highest) first
    asks: tuple[Level, ...]  # YES asks, best (lowest) first

    @property
    def best_bid(self) -> int | None:
        return self.bids[0].price_cents if self.bids else None

    @property
    def best_ask(self) -> int | None:
        return self.asks[0].price_cents if self.asks else None

    def displayed(self, side: str, price_cents: int) -> Decimal:
        levels = self.bids if side == "bid" else self.asks
        return sum((lvl.quantity for lvl in levels if lvl.price_cents == price_cents), ZERO)


def _levels(raw: Iterable[dict], descending: bool) -> tuple[Level, ...]:
    out = []
    for item in raw or ():
        try:
            price = int((Decimal(str(item["price"])) * 100).quantize(ONE, rounding=ROUND_HALF_UP))
            out.append(Level(price, Decimal(str(item["quantity"]))))
        except (KeyError, ValueError, ArithmeticError, TypeError):
            continue
    return tuple(sorted(out, key=lambda lvl: lvl.price_cents, reverse=descending))


def parse_books(rows: Iterable[tuple[str, str]]) -> list[Book]:
    """(timestamp, payload_json) rows from orderbook_snapshots, sorted by time."""
    books = []
    for stamp, payload_json in rows:
        try:
            payload = json.loads(payload_json)
            books.append(
                Book(
                    at=datetime.fromisoformat(str(stamp)),
                    bids=_levels(payload.get("yes_bids"), descending=True),
                    asks=_levels(payload.get("yes_asks"), descending=False),
                )
            )
        except (ValueError, TypeError, AttributeError):
            continue
    return sorted(books, key=lambda b: b.at)


class BookIndex:
    """The latest snapshot at or before a moment, by bisection."""

    def __init__(self, books: list[Book]):
        self.books = books
        self.stamps = [b.at for b in books]

    def at_or_before(self, when: datetime) -> Book | None:
        i = bisect.bisect_right(self.stamps, when)
        return self.books[i - 1] if i else None


def reference_from_book(books: BookIndex, when: datetime) -> Decimal | None:
    """Arm A: the mid of best YES bid and best YES ask, or None when a side is empty."""
    book = books.at_or_before(when)
    if book is None or book.best_bid is None or book.best_ask is None:
        return None
    return Decimal(book.best_bid + book.best_ask) / 200


def reference_from_prints(prints: list[Print], upto: int) -> Decimal | None:
    """Arm B: the volume-weighted YES price of the last 20 non-block prints before index `upto`."""
    volume = ZERO
    weighted = ZERO
    seen = 0
    for i in range(upto - 1, -1, -1):
        p = prints[i]
        if p.is_block or p.count <= 0:
            continue
        volume += p.count
        weighted += Decimal(p.price_cents) / 100 * p.count
        seen += 1
        if seen == REFERENCE_PRINTS:
            break
    return weighted / volume if volume > 0 else None


def money(value: Decimal) -> str:
    """Four decimals in artifacts; averages otherwise carry 28 digits."""
    return str(value.quantize(Decimal("0.0001")))


def maker_fee(contracts: Decimal, price_cents: int) -> Decimal:
    """ceil(0.0175 * C * P * (1 - P)) to the cent, in dollars."""
    price = Decimal(price_cents) / 100
    return (MAKER_RATE * contracts * price * (ONE - price)).quantize(CENT, rounding=ROUND_CEILING)


def taker_fee(contracts: Decimal, price_cents: int) -> Decimal:
    """gap_radar's taker ceiling: ceil(0.07 * P * (1 - P)) to the cent, per contract."""
    price = Decimal(price_cents) / 100
    per_contract = (TAKER_RATE * price * (ONE - price)).quantize(CENT, rounding=ROUND_CEILING)
    return per_contract * contracts


def quote_prices(reference: Decimal, half_spread_cents: int) -> tuple[int, int]:
    """Bid and ask in cents: reference +/- s, rounded to the cent, clipped to [1, 99]."""
    centre = int((reference * 100).quantize(ONE, rounding=ROUND_HALF_UP))
    bid = min(99, max(1, centre - half_spread_cents))
    ask = min(99, max(1, centre + half_spread_cents))
    return bid, ask


@dataclass(frozen=True)
class Fill:
    at: datetime
    side: str  # "bid" (we bought YES) or "ask" (we sold YES)
    price_cents: int
    quantity: Decimal
    print_id: str
    fee: Decimal


@dataclass
class Position:
    """Net YES position with average-cost bases, so collateral can be marked."""

    long_qty: Decimal = ZERO
    long_cost: Decimal = ZERO  # sum of price * qty
    short_qty: Decimal = ZERO
    short_cost: Decimal = ZERO  # sum of (1 - price) * qty, what Kalshi locks

    @property
    def net(self) -> Decimal:
        return self.long_qty - self.short_qty

    @property
    def basis(self) -> Decimal:
        return self.long_cost + self.short_cost

    def buy(self, qty: Decimal, price: Decimal) -> None:
        close = min(qty, self.short_qty)
        if close > 0:
            self.short_cost -= self.short_cost / self.short_qty * close
            self.short_qty -= close
        rest = qty - close
        if rest > 0:
            self.long_qty += rest
            self.long_cost += price * rest

    def sell(self, qty: Decimal, price: Decimal) -> None:
        close = min(qty, self.long_qty)
        if close > 0:
            self.long_cost -= self.long_cost / self.long_qty * close
            self.long_qty -= close
        rest = qty - close
        if rest > 0:
            self.short_qty += rest
            self.short_cost += (ONE - price) * rest


@dataclass
class MarketResult:
    market_id: str
    arm: str
    params: str
    window_start: datetime
    window_end: datetime
    prints_in_window: int  # non-block
    settled: bool
    result: str | None  # "yes" / "no" / None
    fills: list[Fill] = field(default_factory=list)
    contracts_filled: Decimal = ZERO
    fees: Decimal = ZERO
    trading_cash: Decimal = ZERO  # cash from fills, before fees
    settlement_cash: Decimal = ZERO
    peak_collateral: Decimal = ZERO
    quotes_placed: int = 0

    @property
    def net_pnl(self) -> Decimal:
        return self.trading_cash + self.settlement_cash - self.fees

    @property
    def duration_seconds(self) -> float:
        return (self.window_end - self.window_start).total_seconds()

    def to_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "arm": self.arm,
            "params": self.params,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "duration_seconds": self.duration_seconds,
            "prints_in_window": self.prints_in_window,
            "settled": self.settled,
            "result": self.result,
            "quotes_placed": self.quotes_placed,
            "fills": [
                {"at": f.at.isoformat(), "side": f.side, "price_cents": f.price_cents,
                 "quantity": money(f.quantity), "print_id": f.print_id, "fee": money(f.fee)}
                for f in self.fills
            ],
            "contracts_filled": money(self.contracts_filled),
            "fees": money(self.fees),
            "trading_cash": money(self.trading_cash),
            "settlement_cash": money(self.settlement_cash),
            "net_pnl": money(self.net_pnl),
            "peak_collateral": money(self.peak_collateral),
        }


def replay(
    market_id: str,
    arm: str,
    prints: list[Print],
    window_start: datetime,
    window_end: datetime,
    result: str | None,
    params: Params = PRIMARY,
    books: list[Book] | None = None,
) -> MarketResult:
    """Replay the rule forward through one market's tape (section 4).

    Arm A needs `books`; its fill model is FIFO behind displayed size. Arm B has
    no books and uses strict crossing. Both place quotes at t, live from t + L,
    cancelled and replaced every delta; unfilled quantity never carries over.
    """
    if arm == "A" and books is None:
        raise ValueError("Arm A needs order-book snapshots")
    index = BookIndex(books or [])
    fee_of = maker_fee if params.fee == "maker" else taker_fee
    q = Decimal(params.quantity)
    cap = Decimal(params.inventory_cap)
    latency = timedelta(seconds=params.latency_seconds)
    delta = timedelta(seconds=params.requote_seconds)
    tape = [p for p in prints if window_start <= p.at <= window_end]
    out = MarketResult(
        market_id=market_id, arm=arm, params=params.label(), window_start=window_start,
        window_end=window_end, prints_in_window=sum(1 for p in tape if not p.is_block),
        settled=result in ("yes", "no"), result=result,
    )
    position = Position()
    cursor = 0  # index into tape of the first print not yet consumed by an interval
    t = window_start
    while t < window_end:
        interval_end = min(t + delta, window_end)
        live_from = t + latency
        # Reference at placement time.
        while cursor < len(tape) and tape[cursor].at < t:
            cursor += 1
        reference = (reference_from_book(index, t) if arm == "A"
                     else reference_from_prints(tape, cursor))
        locked = ZERO
        if reference is not None:
            bid, ask = quote_prices(reference, params.half_spread_cents)
            quote_bid = position.net + q <= cap
            quote_ask = position.net - q >= -cap
            out.quotes_placed += int(quote_bid) + int(quote_ask)
            remaining = {"bid": q if quote_bid else ZERO, "ask": q if quote_ask else ZERO}
            locked = ((Decimal(bid) / 100 * q if quote_bid else ZERO)
                      + ((ONE - Decimal(ask) / 100) * q if quote_ask else ZERO))
            if arm == "A":
                book = index.at_or_before(t)
                ahead = {
                    "bid": ZERO if (book is None or book.best_bid is None or bid > book.best_bid)
                    else book.displayed("bid", bid),
                    "ask": ZERO if (book is None or book.best_ask is None or ask < book.best_ask)
                    else book.displayed("ask", ask),
                }
                seen = {"bid": ZERO, "ask": ZERO}
                filled = {"bid": ZERO, "ask": ZERO}
            i = cursor
            while i < len(tape) and tape[i].at < interval_end:
                p = tape[i]
                i += 1
                if p.is_block or p.at < live_from or p.count <= 0:
                    continue
                if arm == "A":
                    # Opposing taker flow at-or-through our price counts toward the queue.
                    if remaining["bid"] > 0 and not p.taker_bought_yes and p.price_cents <= bid:
                        seen["bid"] += p.count
                        excess = seen["bid"] - ahead["bid"] - filled["bid"]
                        take = min(remaining["bid"], excess)
                        if take > 0:
                            _fill(out, position, "bid", bid, take, p, fee_of)
                            remaining["bid"] -= take
                            filled["bid"] += take
                    if remaining["ask"] > 0 and p.taker_bought_yes and p.price_cents >= ask:
                        seen["ask"] += p.count
                        excess = seen["ask"] - ahead["ask"] - filled["ask"]
                        take = min(remaining["ask"], excess)
                        if take > 0:
                            _fill(out, position, "ask", ask, take, p, fee_of)
                            remaining["ask"] -= take
                            filled["ask"] += take
                else:
                    # Strict crossing: a print AT our price never fills us.
                    if remaining["bid"] > 0 and not p.taker_bought_yes and p.price_cents < bid:
                        take = min(remaining["bid"], p.count)
                        _fill(out, position, "bid", bid, take, p, fee_of)
                        remaining["bid"] -= take
                    if remaining["ask"] > 0 and p.taker_bought_yes and p.price_cents > ask:
                        take = min(remaining["ask"], p.count)
                        _fill(out, position, "ask", ask, take, p, fee_of)
                        remaining["ask"] -= take
        out.peak_collateral = max(out.peak_collateral, locked + position.basis)
        t = interval_end
    if out.settled:
        payout = ONE if result == "yes" else ZERO
        out.settlement_cash = position.net * payout
    return out


def _fill(out: MarketResult, position: Position, side: str, price_cents: int,
          qty: Decimal, p: Print, fee_of) -> None:
    price = Decimal(price_cents) / 100
    fee = fee_of(qty, price_cents)
    if side == "bid":
        position.buy(qty, price)
        out.trading_cash -= price * qty
    else:
        position.sell(qty, price)
        out.trading_cash += price * qty
    out.fees += fee
    out.contracts_filled += qty
    out.fills.append(Fill(at=p.at, side=side, price_cents=price_cents, quantity=qty,
                          print_id=p.trade_id, fee=fee))


# ------------------------------------------------------------------ evaluation


def adequate(arm: str, results: list[MarketResult]) -> tuple[bool, str]:
    """Section 5. Only settled markets count; Arm A also needs 500 non-block prints each."""
    settled = [r for r in results if r.settled]
    if arm == "A":
        eligible = [r for r in settled if r.prints_in_window >= ARM_A_MIN_PRINTS]
        ok = len(eligible) >= ARM_A_MIN_MARKETS
        return ok, (f"{len(eligible)} settled markets with >= {ARM_A_MIN_PRINTS} prints "
                    f"(need {ARM_A_MIN_MARKETS})")
    ok = len(settled) >= ARM_B_MIN_GAMES
    return ok, f"{len(settled)} settled games (need {ARM_B_MIN_GAMES})"


def criteria(results: list[MarketResult]) -> dict:
    """Section 6, criteria 1 to 3 on one parameter set, over settled markets."""
    settled = [r for r in results if r.settled]
    total = sum((r.net_pnl for r in settled), ZERO)
    contracts = sum((r.contracts_filled for r in settled), ZERO)
    positive = sum(1 for r in settled if r.net_pnl > 0)
    share = Decimal(positive) / len(settled) if settled else ZERO
    per_contract = total / contracts if contracts > 0 else None
    return {
        "markets": len(settled),
        "net_pnl": money(total),
        "contracts_filled": money(contracts),
        "positive_markets": positive,
        "positive_share": str(share.quantize(Decimal("0.001"))),
        "net_per_contract": None if per_contract is None else str(per_contract.quantize(Decimal("0.0001"))),
        "c1_earns": total > 0,
        "c2_not_one_market": share >= MIN_POSITIVE_SHARE,
        "c3_clears_noise": per_contract is not None and per_contract >= MIN_NET_PER_CONTRACT,
    }


def verdict(arm: str, primary: list[MarketResult], slow: list[MarketResult]) -> dict:
    """PASS needs criteria 1 to 3 at L = 1 s and again at L = 5 s; FAIL is any miss on an
    adequate sample; INCONCLUSIVE is a sample below section 5's floors."""
    ok, sample_note = adequate(arm, primary)
    fast = criteria(primary)
    at_slow = criteria(slow)
    c4 = at_slow["c1_earns"] and at_slow["c2_not_one_market"] and at_slow["c3_clears_noise"]
    passed = fast["c1_earns"] and fast["c2_not_one_market"] and fast["c3_clears_noise"] and c4
    return {
        "arm": arm,
        "adequate_sample": ok,
        "sample": sample_note,
        "primary": fast,
        "slow_latency": at_slow,
        "c4_survives_slow": c4,
        "verdict": "INCONCLUSIVE" if not ok else ("PASS" if passed else "FAIL"),
    }


def grid() -> list[Params]:
    out = []
    for fee in GRID_FEES:
        for s in GRID_HALF_SPREADS:
            for d in GRID_REQUOTES:
                for lat in GRID_LATENCIES:
                    out.append(Params(half_spread_cents=s, latency_seconds=lat,
                                      requote_seconds=d, fee=fee))
    return out


def fee_examples() -> list[dict]:
    """The maker formula on round numbers, for the artifact's own audit trail."""
    return [
        {"contracts": "10", "price_cents": 50, "fee": str(maker_fee(Decimal(10), 50))},
        {"contracts": "10", "price_cents": 90, "fee": str(maker_fee(Decimal(10), 90))},
        {"contracts": "1", "price_cents": 50, "fee": str(maker_fee(Decimal(1), 50))},
    ]


def ceil_cents(dollars: Decimal) -> int:
    return math.ceil(dollars * 100)
