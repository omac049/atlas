from decimal import Decimal

from atlas.simulation import run_fixture_research
from atlas.venues.fixtures import fixture_books, fixture_markets
from atlas.verification import verify_equivalence


def test_fixture_research_measures_phantom_rate_and_failed_hedge_loss():
    markets, books = fixture_markets(), fixture_books()
    pair = verify_equivalence(markets["kalshi"][0], markets["polymarket_us"][0])
    metrics = run_fixture_research(
        pair, {"a": books["kalshi:KALSHI-FED-SEP26"], "b": books["polymarket_us:PM-FED-SEP26"]}
    )
    assert metrics.detected_opportunities == 4
    assert metrics.executable_opportunities == 1
    assert metrics.phantom_rate == Decimal("0.75")
    assert metrics.failed_hedge_losses == Decimal("2.50")
