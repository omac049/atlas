from decimal import Decimal

from atlas.models import OrderBook, OrderBookLevel, VenueName
from atlas.shadow import find_shadow_pairs, observe_shadow_pair
from atlas.venues.fixtures import fixture_markets


def test_shadow_pair_requires_opposite_signed_execution_sides():
    markets = fixture_markets()
    kalshi = markets["kalshi"][0]
    polymarket = markets["polymarket_us"][0]
    common_rules = (
        "Across the full match. Cancellation resolves to fair market price. "
        "Postponed within two weeks. Retirement markets that can be unconditionally "
        "settled resolve accordingly; otherwise fair market price."
    )
    for market in (kalshi, polymarket):
        market.market_type = "spread"
        market.threshold = Decimal("1.5")
        market.threshold_operator = ">"
        market.participants = ["Arthur Fils", "Rafael Jodar"]
        market.raw_rules_text = common_rules
    kalshi.title = "Will Arthur Fils win at least 1.5 more games than Rafael Jodar?"
    kalshi.raw_market_json = {"yes_sub_title": "Arthur Fils -1.5 games"}
    kalshi.measurement_period = "2026-08-10T19:30:00Z"
    polymarket.title = "Arthur Fils wins by over 1.5 games"
    polymarket.outcome_yes_label = "Rafael Jodar"
    polymarket.raw_market_json = {
        "marketSides": [{"long": True, "description": "+1.50"}]
    }
    polymarket.measurement_period = "2026-08-10T22:00:00Z"
    pairs = find_shadow_pairs([kalshi], [polymarket])
    assert len(pairs) == 1
    assert "AFFIRMATIVE_OUTCOME_MISMATCH" in pairs[0].differences
    assert "SIGNED_LINE_MISMATCH" in pairs[0].differences
    book_a = OrderBook(
        venue=VenueName.KALSHI,
        market_id="k",
        yes_asks=[OrderBookLevel(price=Decimal("0.45"), quantity=Decimal(1))],
        no_asks=[OrderBookLevel(price=Decimal("0.59"), quantity=Decimal(1))],
    )
    book_b = OrderBook(
        venue=VenueName.POLYMARKET_US,
        market_id="p",
        yes_asks=[OrderBookLevel(price=Decimal("0.59"), quantity=Decimal(1))],
        no_asks=[OrderBookLevel(price=Decimal("0.45"), quantity=Decimal(1))],
    )
    observation = observe_shadow_pair(pairs[0], book_a, book_b, Decimal(1))
    assert observation["blockers"].count("NON_GUARANTEED_SETTLEMENT") == 1
    assert "NON_GUARANTEED_SETTLEMENT_POLICY" not in observation["blockers"]
    assert "AFFIRMATIVE_OUTCOME_MISMATCH" not in observation["blockers"]


def test_shadow_observation_never_creates_guaranteed_trade():
    markets = fixture_markets()
    from atlas.verification import verify_equivalence

    pair = verify_equivalence(markets["kalshi"][0], markets["polymarket_us"][0])
    pair.differences = ["RULE_MISMATCH"]
    book_a = OrderBook(
        venue=VenueName.KALSHI,
        market_id="k",
        yes_asks=[OrderBookLevel(price=Decimal("0.45"), quantity=Decimal(10))],
        no_asks=[OrderBookLevel(price=Decimal("0.59"), quantity=Decimal(10))],
    )
    book_b = OrderBook(
        venue=VenueName.POLYMARKET_US,
        market_id="p",
        yes_asks=[OrderBookLevel(price=Decimal("0.59"), quantity=Decimal(10))],
        no_asks=[OrderBookLevel(price=Decimal("0.45"), quantity=Decimal(10))],
    )
    observation = observe_shadow_pair(pair, book_a, book_b)
    assert observation["best_direction"]["gross_cost"] == "10.40"
    assert observation["guaranteed_payout"] is False
    assert observation["paper_trade_created"] is False
