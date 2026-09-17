"""The Arm A book recorder, on synthetic books and a fake clock."""

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from atlas import book_recorder
from atlas.models import OrderBook, OrderBookLevel, VenueName
from atlas.storage import AtlasStore

T0 = datetime(2026, 9, 17, 20, 0, tzinfo=UTC)


class FakeTime:
    """A clock that only moves when something sleeps on it."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def wall(self) -> datetime:
        return T0 + timedelta(seconds=self.now)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def levels(*pairs) -> list[OrderBookLevel]:
    return [OrderBookLevel(price=Decimal(str(p)), quantity=Decimal(str(q))) for p, q in pairs]


def kalshi_book(ticker: str, at: datetime, yes_bids=(), no_bids=()) -> OrderBook:
    """A book the way the REST adapter builds it: two bid sides and their mirrors."""
    yes, no = levels(*yes_bids), levels(*no_bids)

    def mirror(side: list[OrderBookLevel]) -> list[OrderBookLevel]:
        return [OrderBookLevel(price=Decimal(1) - lv.price, quantity=lv.quantity) for lv in side]

    return OrderBook(venue=VenueName.KALSHI, market_id=f"kalshi:{ticker}", timestamp=at,
                     yes_bids=yes, no_bids=no, yes_asks=mirror(no), no_asks=mirror(yes))


class ScriptedVenue:
    """Answers each read from a script; the last entry repeats. An entry is a
    (yes_bids, no_bids) pair or an exception to raise."""

    def __init__(self, fake: FakeTime, script: list) -> None:
        self.fake, self.script, self.calls = fake, script, 0

    async def get_orderbook(self, ticker: str) -> OrderBook:
        entry = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(entry, Exception):
            raise entry
        return kalshi_book(ticker, self.fake.wall(), *entry)


BOOK_A = ([(0.51, 3108), (0.50, 2668)], [(0.48, 8630), (0.47, 17125)])
BOOK_B = ([(0.51, 2900), (0.50, 2668)], [(0.48, 8630), (0.47, 17125)])


def too_many_requests(retry_after: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/orderbook")
    response = httpx.Response(429, headers={"retry-after": retry_after}, request=request)
    return httpx.HTTPStatusError("429", request=request, response=response)


async def run_market(tmp_path, script, seconds):
    fake = FakeTime()
    store = AtlasStore(str(tmp_path / "books.sqlite3"))
    state = book_recorder.MarketState("KXFEDDECISION-26OCT-H0")
    await book_recorder.record_market(
        ScriptedVenue(fake, script), store, state, interval=5.0, depth=10, heartbeat=60.0,
        until=T0 + timedelta(seconds=seconds), clock=fake.clock, sleep=fake.sleep, wall=fake.wall,
    )
    return fake, store, state


def test_trim_keeps_the_best_levels_whatever_order_they_arrive_in():
    # Kalshi's REST book lists each side from the worst price up.
    book = kalshi_book("X", T0, yes_bids=[(0.01, 5), (0.49, 7), (0.50, 8), (0.51, 9)],
                       no_bids=[(0.02, 1), (0.47, 2), (0.48, 3)])
    cut = book_recorder.trim(book, depth=2)
    assert [lv.price for lv in cut.yes_bids] == [Decimal("0.51"), Decimal("0.50")]
    assert [lv.price for lv in cut.no_bids] == [Decimal("0.48"), Decimal("0.47")]
    assert [lv.price for lv in cut.yes_asks] == [Decimal("0.52"), Decimal("0.53")]  # 1 - no bid
    assert [lv.price for lv in cut.no_asks] == [Decimal("0.49"), Decimal("0.50")]
    assert len(book.yes_bids) == 4  # the original is untouched


async def test_an_unchanged_book_is_written_once_and_then_only_at_the_heartbeat(tmp_path):
    _, store, state = await run_market(tmp_path, [BOOK_A], seconds=125)
    # 25 reads at 5 s; rows at 0 s, 60 s and 120 s.
    assert state.polls == 25 and state.written == 3 and state.unchanged == 22
    assert state.errors == 0 and len(await store.latest_orderbooks(50)) == 3


async def test_a_changed_book_is_written_at_once(tmp_path):
    _, _, state = await run_market(tmp_path, [BOOK_A, BOOK_A, BOOK_B, BOOK_B, BOOK_A], seconds=25)
    assert state.polls == 5 and state.written == 3 and state.unchanged == 2


async def test_a_429_is_obeyed_for_its_retry_after_and_recording_resumes(tmp_path):
    fake, _, state = await run_market(tmp_path, [too_many_requests("30"), BOOK_A], seconds=60)
    assert fake.sleeps[0] == 30.0 and fake.sleeps[1] == 5.0
    assert state.error_types == {"http_429": 1} and state.written == 1


async def test_a_bad_read_is_counted_and_never_stops_the_recorder(tmp_path):
    _, _, state = await run_market(tmp_path, [ValueError("bad payload"), BOOK_A], seconds=15)
    assert state.error_types == {"ValueError": 1} and state.polls == 3 and state.written == 1


class LockedAtFirst:
    """A store whose first writes fail the way a locked SQLite file does."""

    def __init__(self, inner: AtlasStore, failures: int) -> None:
        self.inner, self.failures = inner, failures

    async def save_orderbook(self, book: OrderBook) -> None:
        if self.failures > 0:
            self.failures -= 1
            raise sqlite3.OperationalError("database is locked")
        await self.inner.save_orderbook(book)


async def test_books_wait_in_memory_while_the_database_is_locked_and_go_out_in_order(tmp_path):
    fake = FakeTime()
    inner = AtlasStore(str(tmp_path / "books.sqlite3"))
    state = book_recorder.MarketState("KXFEDDECISION-26OCT-H0")
    await book_recorder.record_market(
        ScriptedVenue(fake, [BOOK_A, BOOK_B, BOOK_A]), LockedAtFirst(inner, failures=2), state,
        interval=5.0, depth=10, heartbeat=60.0, until=T0 + timedelta(seconds=15),
        clock=fake.clock, sleep=fake.sleep, wall=fake.wall,
    )
    assert state.error_types == {"db_OperationalError": 2}
    assert state.written == 3 and state.pending == []
    saved = sorted(await inner.latest_orderbooks(10), key=lambda b: b.timestamp)
    assert [b.yes_bids[0].quantity for b in saved] == [Decimal(3108), Decimal(2900), Decimal(3108)]
    assert [b.timestamp for b in saved] == [T0 + timedelta(seconds=s) for s in (0, 5, 10)]


async def test_recorded_books_are_readable_by_the_frozen_arm_a_runner(tmp_path):
    """The point of the recorder: `run_making.py --db <this file>` must see these
    markets, and the instrument must read the venue's real top of book."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "docs" / "proof"))
    import run_making

    fake = FakeTime()
    db = tmp_path / "books.sqlite3"
    lines: list[str] = []
    states = await book_recorder.record(
        ScriptedVenue(fake, [BOOK_A, BOOK_B]), AtlasStore(str(db)),
        ["KXFEDDECISION-26OCT-H0", "KXFEDDECISION-26OCT-H25"],
        until=T0 + timedelta(seconds=20), interval=5.0, status_every=3600.0,
        clock=fake.clock, sleep=fake.sleep, wall=fake.wall, emit=lines.append,
    )
    assert [s.ticker for s in states] == ["KXFEDDECISION-26OCT-H0", "KXFEDDECISION-26OCT-H25"]
    assert all(s.written >= 1 and s.errors == 0 for s in states)
    assert lines and lines[-1].startswith("book_recorder ") and "H25 polls=" in lines[-1]

    markets = run_making.snapshot_markets(db)
    assert set(markets) == {"kalshi:KXFEDDECISION-26OCT-H0", "kalshi:KXFEDDECISION-26OCT-H25"}
    books = run_making.load_books(db, "kalshi:KXFEDDECISION-26OCT-H0", T0, T0 + timedelta(hours=1))
    assert books and books[0].best_bid == 51 and books[0].best_ask == 52  # never crossed
    assert books[0].displayed("bid", 51) in (Decimal(3108), Decimal(2900))


def test_recording_stops_a_little_after_the_last_market_closes():
    tickers, until = book_recorder.recording_window([
        {"ticker": "KXFEDDECISION-26OCT-H25", "close_time": "2026-10-28T17:59:00Z"},
        {"ticker": "KXFEDDECISION-26OCT-C25", "close_time": "2026-10-28T17:59:00Z"},
        {"close_time": "2026-10-28T17:59:00Z"},
    ])
    assert tickers == ["KXFEDDECISION-26OCT-C25", "KXFEDDECISION-26OCT-H25"]
    assert until == datetime(2026, 10, 28, 18, 4, tzinfo=UTC)
    assert book_recorder.recording_window([]) == ([], None)
