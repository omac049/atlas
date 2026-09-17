"""Study phase 2: burst sampling and the latency replay, on synthetic books only."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from atlas import latency
from atlas.models import OrderBook, OrderBookLevel, VenueName
from atlas.storage import AtlasStore
from atlas.venues.kalshi import KalshiVenue
from atlas.venues.polymarket_us import PolymarketUSVenue

T0 = datetime(2026, 9, 15, 20, 0, tzinfo=UTC)


def book(venue: str, market_id: str, seconds: float, yes_asks=(), no_asks=()) -> OrderBook:
    return OrderBook(
        venue=VenueName(venue), market_id=market_id, timestamp=T0 + timedelta(seconds=seconds),
        yes_asks=[OrderBookLevel(price=str(p), quantity=str(q)) for p, q in yes_asks],
        no_asks=[OrderBookLevel(price=str(p), quantity=str(q)) for p, q in no_asks],
    )


def observation(**overrides) -> dict:
    base = {
        "observation_id": "obs-1",
        "event_subject": "us_cpi_yoy|2026-09",
        "observed_at": T0.isoformat(),
        "shape": "equivalent_shape",
        "best_basket": "kalshi_no+polymarket_yes",
        "best_gap": "0.0200",
        "best_basket_size": "10",
        "baskets": [],
        "tradeable_venue_pair": True,
        "executable_gap": True,
        "polymarket_venue": "polymarket_us",
        "kalshi_market_id": "kalshi:KXCPIYOY-26SEP-T3.6",
        "polymarket_market_id": "polymarket_us:cpic-3pt6",
        "polymarket_fee_terms": {"feeCoefficient": "0.06"},
        "settlement_timing": {"days_to_settlement": "100"},
        "meets_tick_floor": True,
        "meets_size_floor": True,
    }
    base.update(overrides)
    return base


def test_only_tradeable_executable_polymarket_us_gaps_are_burst_eligible():
    assert latency.burst_eligible(observation()) is True
    assert latency.burst_eligible(observation(tradeable_venue_pair=False)) is False
    assert latency.burst_eligible(observation(executable_gap=False)) is False
    assert latency.burst_eligible(observation(polymarket_venue="polymarket_global")) is False


async def test_burst_samples_both_legs_through_the_ordinary_snapshot_path(tmp_path):
    """Wall-clock run; asserts only what holds on the slowest CI runner: each
    leg's first read happens before any sleep, so both legs land at least once."""
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    target = observation(kalshi_market_id="kalshi:KALSHI-FED-SEP26",
                         polymarket_market_id="polymarket_us:PM-FED-SEP26")
    counts = await latency.burst_books(
        KalshiVenue(fixture=True), PolymarketUSVenue(fixture=True), store, target,
        seconds=0.3, intervals={"kalshi": 0.05, "polymarket_us": 0.1},
    )
    assert counts["errors"] == 0 and counts["error_types"] == {}
    assert counts["kalshi"] >= 1 and counts["polymarket_us"] >= 1
    saved = await store.latest_orderbooks(200)
    assert {b.market_id for b in saved} == {"kalshi:KALSHI-FED-SEP26", "polymarket_us:PM-FED-SEP26"}
    kalshi_books = await store.orderbooks_between(
        "kalshi:KALSHI-FED-SEP26", T0, datetime.now(UTC) + timedelta(seconds=1)
    )
    assert len(kalshi_books) == counts["kalshi"]


class FakeTime:
    """A clock that only moves when something sleeps on it."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _RateLimitedOnce:
    """A venue that answers 429 with a Retry-After on its first read only."""

    def __init__(self, inner, retry_after: str):
        self.inner, self.retry_after, self.calls = inner, retry_after, 0

    async def get_orderbook(self, market_id: str):
        self.calls += 1
        if self.calls == 1:
            request = httpx.Request("GET", "https://example.test/book")
            response = httpx.Response(429, headers={"retry-after": self.retry_after},
                                      request=request)
            raise httpx.HTTPStatusError("429", request=request, response=response)
        return await self.inner.get_orderbook(market_id)


async def test_sample_leg_keeps_its_cadence(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    fake = FakeTime()
    counts = {"kalshi": 0, "polymarket_us": 0, "errors": 0, "error_types": {}}
    await latency._sample_leg(
        "kalshi", KalshiVenue(fixture=True).get_orderbook, "KALSHI-FED-SEP26", 0.25,
        deadline=2.0, store=store, counts=counts, clock=fake.clock, sleep=fake.sleep,
    )
    assert counts == {"kalshi": 8, "polymarket_us": 0, "errors": 0, "error_types": {}}
    assert fake.sleeps == [0.25] * 8
    assert len(await store.latest_orderbooks(50)) == 8


async def test_sample_leg_obeys_retry_after_then_resumes(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    fake = FakeTime()
    counts = {"kalshi": 0, "polymarket_us": 0, "errors": 0, "error_types": {}}
    pmus = _RateLimitedOnce(PolymarketUSVenue(fixture=True), retry_after="5")
    await latency._sample_leg(
        "polymarket_us", pmus.get_orderbook, "PM-FED-SEP26", 0.5,
        deadline=8.0, store=store, counts=counts, clock=fake.clock, sleep=fake.sleep,
    )
    assert counts["error_types"] == {"polymarket_us:http_429": 1} and counts["errors"] == 1
    assert fake.sleeps[0] == 5.0  # the Retry-After, not the 0.5 s cadence
    assert counts["polymarket_us"] == 6  # 5.0, 5.5, ... 7.5


async def test_burst_runs_the_legs_independently(tmp_path):
    """The Polymarket leg stalling on a 429 never slows the Kalshi leg."""
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    target = observation(kalshi_market_id="kalshi:KALSHI-FED-SEP26",
                         polymarket_market_id="polymarket_us:PM-FED-SEP26")
    pmus = _RateLimitedOnce(PolymarketUSVenue(fixture=True), retry_after="0.2")
    counts = await latency.burst_books(
        KalshiVenue(fixture=True), pmus, store, target, seconds=0.3,
        intervals={"kalshi": 0.02, "polymarket_us": 0.05},
    )
    assert counts["error_types"] == {"polymarket_us:http_429": 1}
    assert counts["kalshi"] >= 1 and counts["polymarket_us"] >= 0
    assert pmus.calls >= 1


def test_retry_after_parsing_falls_back_to_the_cadence():
    assert latency.retry_after_seconds("9", 2.5) == 9.0
    assert latency.retry_after_seconds(None, 2.5) == 2.5
    assert latency.retry_after_seconds("soon", 2.5) == 2.5
    assert latency.retry_after_seconds("-1", 2.5) == 0.0


async def test_pacer_spaces_reads_of_one_venue():
    now = [100.0]
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    pacer = latency.VenuePacer(2.5, clock=lambda: now[0], sleep=fake_sleep)
    await pacer.wait()          # first read goes straight through
    now[0] += 0.4
    await pacer.wait()          # 0.4 s later: wait the remaining 2.1 s
    now[0] += 3.0
    await pacer.wait()          # 3 s later: nothing to wait for
    assert [round(x, 6) for x in sleeps] == [2.1, 0.0]


def test_book_quotes_take_the_best_ask_on_each_side():
    quotes = latency.book_quotes(book("kalshi", "kalshi:x", 0, yes_asks=[(0.62, 5), (0.60, 3)],
                                      no_asks=[(0.41, 7)]))
    assert quotes == {"yes_ask": Decimal("0.60"), "no_ask": Decimal("0.41"),
                      "yes_size": Decimal(3), "no_size": Decimal(7)}
    assert latency.book_quotes(book("kalshi", "kalshi:x", 0)) is None


def test_replay_measures_survival_partial_fills_and_one_leg_exposure():
    kalshi_id, pmus_id = "kalshi:KXCPIYOY-26SEP-T3.6", "polymarket_us:cpic-3pt6"
    kalshi_books = [
        book("kalshi", kalshi_id, 0.3, no_asks=[(0.40, 10)]),
        book("kalshi", kalshi_id, 1.5, no_asks=[(0.40, 10)]),
    ]
    pmus_books = [
        book("polymarket_us", pmus_id, 0.3, yes_asks=[(0.55, 4)]),
        book("polymarket_us", pmus_id, 1.5),  # nothing offered any more
    ]
    out = latency.replay_observation(observation(), kalshi_books, pmus_books)
    assert out["measurable"] is True
    assert out["delays"]["0.25s"] == {"measurable": False, "reason": "no_quote_within_delay"}
    half = out["delays"]["0.5s"]
    # cost 0.95; Kalshi fee ceil(0.07 * 0.4 * 0.6) = 0.02; Polymarket 0.06 * 0.55 * 0.45 = 0.01485
    assert half["survived"] is True and half["both_legs_quoted"] is True
    assert Decimal(half["gap_after"]) == Decimal(1) - Decimal("0.95") - Decimal("0.02") - Decimal("0.01485")
    assert half["basket_size_after"] == "4" and half["partial_fill"] is True
    assert Decimal(half["annualized_return"]) == (
        Decimal(half["gap_after"]) / Decimal("0.95") * Decimal(365) / Decimal(100)
    ).quantize(Decimal("0.0001"))
    two = out["delays"]["2s"]
    assert two["measurable"] is True and two["one_leg_only"] is True and two["survived"] is False


def test_replay_without_fee_terms_is_not_measurable():
    out = latency.replay_observation(observation(polymarket_fee_terms=None), [], [])
    assert out["measurable"] is False and out["reason"] == "no_fee_terms_recorded"


def test_summary_counts_per_delay():
    rows = [
        {"delays": {"0.25s": {"measurable": False}, "0.5s": {"measurable": True, "survived": True,
                    "gap_after": "0.01", "annualized_return": "0.1", "partial_fill": True,
                    "one_leg_only": False}}},
        {"delays": {"0.25s": {"measurable": True, "survived": False, "one_leg_only": True},
                    "0.5s": {"measurable": True, "survived": False, "gap_after": "-0.01",
                    "one_leg_only": False}}},
    ]
    summary = latency.latency_summary(rows, (Decimal("0.25"), Decimal("0.5")))
    assert summary["0.25s"] == {"measurable": 1, "survived": 0, "survival_share": "0.000",
                                "one_leg_only": 1, "partial_fill": 0, "median_gap_after": None,
                                "median_annualized_return": None}
    assert summary["0.5s"]["survived"] == 1 and summary["0.5s"]["survival_share"] == "0.500"
    assert Decimal(summary["0.5s"]["median_gap_after"]) == 0 and summary["0.5s"]["partial_fill"] == 1


def test_fee_terms_are_the_venue_fields_the_fee_function_reads():
    from atlas.gap_radar import polymarket_fee_terms

    raw = {"feeCoefficient": 0.06, "feesEnabled": True, "title": "x", "feeSchedule": None}
    assert polymarket_fee_terms(raw) == {"feeCoefficient": 0.06, "feesEnabled": True, "feeSchedule": None}
    assert polymarket_fee_terms({}) == {}


async def test_the_report_reads_rest_books_only_and_every_executable_observation(tmp_path):
    """Until 2026-09-17 the websocket recorder stored Kalshi "books" that were not
    the venue's book. They carry a stream sequence; REST reads do not."""
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    kalshi_id, pmus_id = "kalshi:KXFEDDECISION-26OCT-H0", "polymarket_us:fed-oct-hold"
    await store.save_gap_observation(observation(kalshi_market_id=kalshi_id,
                                                 polymarket_market_id=pmus_id))
    await store.save_gap_observation(observation(observation_id="not-executable",
                                                 executable_gap=False))
    stream_row = book("kalshi", kalshi_id, 0.2, no_asks=[(0.10, 500)])
    stream_row.sequence = 700
    await store.save_orderbook(stream_row)
    await store.save_orderbook(book("kalshi", kalshi_id, 0.3, no_asks=[(0.40, 10)]))
    await store.save_orderbook(book("polymarket_us", pmus_id, 0.3, yes_asks=[(0.55, 4)]))
    report = await latency.latency_report(store)
    assert report["eligible_observations"] == 1 and report["observations_with_bursts"] == 1
    row = report["observations"][0]["delays"]["0.5s"]
    assert row["kalshi_quote_at"] == (T0 + timedelta(seconds=0.3)).isoformat()
    assert row["survived"] is True and row["basket_size_after"] == "4"
