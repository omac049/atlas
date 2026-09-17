"""Ground-truth Kalshi order books for the fifth charter's Arm A. Paper-only, read-only.

The websocket recorder Arm A was designed around never stored a real book: it
dropped the feed's opening snapshot and treated each signed size change as the
level's whole size (charter note of 2026-09-17). This recorder asks Kalshi's
public REST endpoint for the book instead — the venue's own statement of it, no
credentials, nothing to reconstruct — and writes it to a database of its own, so
the frozen runner can be pointed at exactly these markets and nothing else:

    python docs/proof/run_making.py --arm A --settle-by 2026-10-28 \\
        --db data/making/books.sqlite3

What a row means: the book as Kalshi published it at ``timestamp``, cut to the
best ``DEPTH`` levels a side. A row is written when the book differs from the
last one written, and at least every ``HEARTBEAT_SECONDS`` while it does not. A
gap between rows longer than the heartbeat therefore always means the recorder
was not polling — never "nothing changed" — which is what lets a later audit
state the coverage of a replay window instead of assuming it.

Nothing here places an order or reads a credential.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx

from atlas.latency import retry_after_seconds
from atlas.models import OrderBook, OrderBookLevel

DEPTH = 10  # the rule quotes within 5c of the mid; ten levels a side always covers it
INTERVAL_SECONDS = 5.0  # five markets at this cadence is one public read a second
HEARTBEAT_SECONDS = 60.0
STATUS_SECONDS = 300.0
PENDING_LIMIT = 2000  # books held in memory while the database cannot be written
CLOSE_GRACE = timedelta(minutes=5)


def _best(levels: list[OrderBookLevel], highest: bool, depth: int) -> list[OrderBookLevel]:
    return sorted(levels, key=lambda level: level.price, reverse=highest)[:depth]


def trim(book: OrderBook, depth: int = DEPTH) -> OrderBook:
    """The best ``depth`` levels a side — highest bids, lowest asks — whatever
    order the venue sent them in."""
    return book.model_copy(
        update={
            "yes_bids": _best(book.yes_bids, True, depth),
            "no_bids": _best(book.no_bids, True, depth),
            "yes_asks": _best(book.yes_asks, False, depth),
            "no_asks": _best(book.no_asks, False, depth),
        }
    )


def signature(book: OrderBook) -> tuple:
    """What "the same book" means: both bid sides, level for level. The ask
    sides are their mirrors and add nothing."""
    return tuple(
        tuple((level.price, level.quantity) for level in side)
        for side in (book.yes_bids, book.no_bids)
    )


@dataclass
class MarketState:
    ticker: str
    last_signature: tuple | None = None
    last_written_at: float | None = None
    polls: int = 0
    written: int = 0
    unchanged: int = 0
    empty: int = 0
    errors: int = 0
    error_types: dict[str, int] = field(default_factory=dict)
    pending: list[OrderBook] = field(default_factory=list)

    def count(self, kind: str) -> None:
        self.errors += 1
        self.error_types[kind] = self.error_types.get(kind, 0) + 1


async def flush(store, state: MarketState) -> None:
    """Write what is waiting, oldest first. A locked or missing database costs
    nothing but time: the books wait in memory, bounded, and go out in order."""
    while state.pending:
        try:
            await store.save_orderbook(state.pending[0])
        except (sqlite3.Error, OSError) as exc:
            state.count(f"db_{type(exc).__name__}")
            del state.pending[:-PENDING_LIMIT]
            return
        state.pending.pop(0)
        state.written += 1


async def poll_once(
    venue, store, state: MarketState, *, depth: int, heartbeat: float,
    clock: Callable[[], float],
) -> float:
    """One read of one market. Returns how long the venue asked us to stay away
    (0.0 unless it answered 429)."""
    state.polls += 1
    backoff = 0.0
    try:
        book = trim(await venue.get_orderbook(state.ticker), depth)
    except httpx.HTTPStatusError as exc:
        state.count(f"http_{exc.response.status_code}")
        if exc.response.status_code == 429:
            backoff = retry_after_seconds(exc.response.headers.get("retry-after"), 0.0)
    except Exception as exc:  # noqa: BLE001 - one bad read must never stop the recorder
        state.count(type(exc).__name__)
    else:
        now = clock()
        mark = signature(book)
        due = state.last_written_at is None or now - state.last_written_at >= heartbeat
        if mark != state.last_signature or due:
            state.pending.append(book)
            state.last_signature, state.last_written_at = mark, now
            state.empty += int(not book.yes_bids and not book.no_bids)
        else:
            state.unchanged += 1
    if state.pending:
        await flush(store, state)
    return backoff


async def record_market(
    venue, store, state: MarketState, *, interval: float, depth: int, heartbeat: float,
    until: datetime, clock: Callable[[], float], sleep: Callable[[float], Awaitable[None]],
    wall: Callable[[], datetime],
) -> None:
    while wall() < until:
        started = clock()
        backoff = await poll_once(
            venue, store, state, depth=depth, heartbeat=heartbeat, clock=clock
        )
        await sleep(max(0.0, max(interval, backoff) - (clock() - started)))


def status_line(states: list[MarketState], wall: Callable[[], datetime]) -> str:
    parts = []
    for state in states:
        short = state.ticker.rsplit("-", 1)[-1]
        part = f"{short} polls={state.polls} written={state.written} errors={state.errors}"
        if state.empty:
            part += f" empty={state.empty}"
        if state.pending:
            part += f" waiting={len(state.pending)}"
        if state.error_types:
            part += f" {state.error_types}"
        parts.append(part)
    return f"book_recorder {wall().isoformat(timespec='seconds')}: " + "; ".join(parts)


async def record(
    venue, store, tickers: list[str], *, until: datetime,
    interval: float = INTERVAL_SECONDS, depth: int = DEPTH,
    heartbeat: float = HEARTBEAT_SECONDS, status_every: float = STATUS_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    wall: Callable[[], datetime] = lambda: datetime.now(UTC),
    emit: Callable[[str], None] = print,
) -> list[MarketState]:
    """Record every ticker until ``until``, reads spread evenly across the interval."""
    states = [MarketState(ticker) for ticker in tickers]

    async def one(position: int, state: MarketState) -> None:
        await sleep(position * interval / max(len(states), 1))
        await record_market(
            venue, store, state, interval=interval, depth=depth, heartbeat=heartbeat,
            until=until, clock=clock, sleep=sleep, wall=wall,
        )

    async def report() -> None:
        # Real time on purpose: an injected test sleep returns at once and would
        # never let the market loops run.
        while True:
            await asyncio.sleep(status_every)
            emit(status_line(states, wall))

    reporter = asyncio.create_task(report())
    try:
        await asyncio.gather(*(one(i, state) for i, state in enumerate(states)))
    finally:
        reporter.cancel()
    emit(status_line(states, wall))
    return states


def _when(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))  # 3.11+ reads a trailing "Z" as UTC
    except ValueError:
        return None


def recording_window(markets: list[dict]) -> tuple[list[str], datetime | None]:
    """The tickers to record and when to stop: a little after the last one closes."""
    tickers = sorted(str(m["ticker"]) for m in markets if m.get("ticker"))
    closes = [c for c in (_when(m.get("close_time")) for m in markets) if c is not None]
    return tickers, (max(closes) + CLOSE_GRACE if closes else None)
