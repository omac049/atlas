"""Study phase 2: burst sampling and the latency replay, on synthetic books only."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    target = observation(kalshi_market_id="kalshi:KALSHI-FED-SEP26",
                         polymarket_market_id="polymarket_us:PM-FED-SEP26")
    counts = await latency.burst_books(
        KalshiVenue(fixture=True), PolymarketUSVenue(fixture=True), store, target,
        seconds=0.3, interval=0.1,
    )
    assert counts["errors"] == 0 and counts["rounds"] >= 2
    assert counts["kalshi"] == counts["rounds"] == counts["polymarket_us"]
    saved = await store.latest_orderbooks(20)
    assert {b.market_id for b in saved} == {"kalshi:KALSHI-FED-SEP26", "polymarket_us:PM-FED-SEP26"}
    kalshi_books = await store.orderbooks_between(
        "kalshi:KALSHI-FED-SEP26", T0, datetime.now(UTC) + timedelta(seconds=1)
    )
    assert len(kalshi_books) == counts["kalshi"]


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
