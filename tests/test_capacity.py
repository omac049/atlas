from decimal import Decimal

from atlas.capacity import (
    STOP_BOOK_EXHAUSTED,
    STOP_EDGE_EXHAUSTED,
    best_capacity,
    capacity_curve,
)
from atlas.models import OrderBook, OrderBookLevel, VenueName

D = "kalshi_yes+polymarket_no"


def _book(**sides) -> OrderBook:
    kwargs = {"yes_bids": [], "yes_asks": [], "no_bids": [], "no_asks": []}
    for side, levels in sides.items():
        kwargs[side] = [
            OrderBookLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in levels
        ]
    return OrderBook(venue=VenueName.KALSHI, market_id="m", **kwargs)


def test_walk_takes_the_cheapest_rungs_even_when_they_arrive_last():
    # Kalshi publishes bids only; its ask ladder is DERIVED and arrives
    # worst-first. Arrival order would charge 0.60 before 0.40 and understate
    # capacity, so the walker must sort.
    kalshi = _book(yes_asks=[("0.60", "10"), ("0.40", "10")])
    polymarket = _book(no_asks=[("0.30", "20")])
    result = capacity_curve(kalshi, polymarket, D)
    assert result["curve"][0]["kalshi_price"] == "0.40"
    assert Decimal(result["profitable_contracts"]) == Decimal(20)


def test_curve_stops_at_the_last_contract_whose_edge_is_positive():
    # rung 1: 1 - 0.40 - 0.30 - fees > 0. rung 2: 1 - 0.80 - 0.30 < 0.
    kalshi = _book(yes_asks=[("0.40", "5"), ("0.80", "500")])
    polymarket = _book(no_asks=[("0.30", "1000")])
    result = capacity_curve(kalshi, polymarket, D)
    assert Decimal(result["profitable_contracts"]) == Decimal(5)
    assert result["stop_reason"] == STOP_EDGE_EXHAUSTED
    assert Decimal(result["final_marginal_edge"]) > 0


def test_thin_touch_then_deep_size_is_the_capacity_story():
    # The live shape on 2026-08-28: a dust order at a great price, real size
    # nine cents worse. Top-of-book reports 0.05 contracts; the ladder shows
    # what is actually takeable.
    kalshi = _book(yes_asks=[("0.05", "5000")])
    polymarket = _book(no_asks=[("0.14", "0.05"), ("0.30", "500")])
    result = capacity_curve(kalshi, polymarket, D)
    assert result["top_of_book_contracts"] == "0.05"
    assert Decimal(result["profitable_contracts"]) > Decimal("0.05")
    assert Decimal(result["total_profit_usd"]) > Decimal(0)


def test_book_exhaustion_is_reported_separately_from_edge_exhaustion():
    kalshi = _book(yes_asks=[("0.10", "7")])
    polymarket = _book(no_asks=[("0.20", "7")])
    result = capacity_curve(kalshi, polymarket, D)
    assert Decimal(result["profitable_contracts"]) == Decimal(7)
    assert result["stop_reason"] == STOP_BOOK_EXHAUSTED


def test_basket_is_capped_by_the_thinner_leg():
    kalshi = _book(yes_asks=[("0.10", "1000")])
    polymarket = _book(no_asks=[("0.20", "4")])
    result = capacity_curve(kalshi, polymarket, D)
    assert Decimal(result["profitable_contracts"]) == Decimal(4)


def test_no_profitable_rung_yields_zero_capacity_not_an_error():
    kalshi = _book(yes_asks=[("0.90", "100")])
    polymarket = _book(no_asks=[("0.90", "100")])
    result = capacity_curve(kalshi, polymarket, D)
    assert result["supported"] is True
    assert result["profitable_contracts"] == "0"
    assert result["total_profit_usd"] == "0"


def test_empty_ladder_is_unsupported_not_infinite():
    result = capacity_curve(_book(), _book(no_asks=[("0.20", "5")]), D)
    assert result["supported"] is False
    assert result["reason"] == "empty_ladder"


def test_unknown_basket_direction_is_rejected():
    kalshi = _book(yes_asks=[("0.10", "6")])
    polymarket = _book(no_asks=[("0.20", "6")])
    result = capacity_curve(kalshi, polymarket, "kalshi_maybe+polymarket_maybe")
    assert result["supported"] is False
    assert result["reason"] == "unknown_basket_direction"


def test_fees_are_charged_at_each_rung_not_at_the_touch():
    # Kalshi's quadratic fee peaks near 0.50, so a walk that reaches deeper,
    # mid-priced rungs must charge more per contract than the touch did.
    kalshi = _book(yes_asks=[("0.05", "10"), ("0.45", "10")])
    polymarket = _book(no_asks=[("0.05", "100")])
    result = capacity_curve(kalshi, polymarket, D)
    edges = [Decimal(point["marginal_edge"]) for point in result["curve"]]
    assert edges[0] > edges[1]


def test_best_capacity_picks_the_richer_direction():
    kalshi = _book(yes_asks=[("0.90", "100")], no_asks=[("0.10", "100")])
    polymarket = _book(no_asks=[("0.90", "100")], yes_asks=[("0.10", "100")])
    result = best_capacity(
        kalshi, polymarket, ("kalshi_yes+polymarket_no", "kalshi_no+polymarket_yes")
    )
    assert result["supported"] is True
    assert result["best"]["legs"] == "kalshi_no+polymarket_yes"
