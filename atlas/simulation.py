from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from atlas.arbitrage import calculate_opportunity
from atlas.models import ContractPair, OrderBook


@dataclass(frozen=True)
class SimulationResult:
    scenario: str
    detected: bool
    executable: bool
    outcome: str
    net_profit: Decimal
    lifetime_ms: int


@dataclass(frozen=True)
class ResearchMetrics:
    detected_opportunities: int
    executable_opportunities: int
    phantom_rate: Decimal
    net_pnl: Decimal
    failed_hedge_losses: Decimal
    median_lifetime_ms: int
    scenarios: tuple[SimulationResult, ...]


def run_fixture_research(pair: ContractPair, books: dict[str, OrderBook]) -> ResearchMetrics:
    baseline = calculate_opportunity(
        pair, books["a"], books["b"], Decimal(50), fees=Decimal("0.20"), slippage=Decimal("0.05")
    )
    results = (
        SimulationResult(
            "stable_books",
            baseline is not None,
            baseline is not None,
            "FILLED" if baseline else "NO_EDGE",
            baseline.expected_profit if baseline else Decimal(0),
            250,
        ),
        SimulationResult(
            "price_moved_before_fill", baseline is not None, False, "PRICE_MOVED", Decimal(0), 75
        ),
        SimulationResult(
            "leg_b_failed", baseline is not None, False, "LEG_B_FAILED", Decimal("-2.50"), 120
        ),
        SimulationResult("stale_book", baseline is not None, False, "DATA_STALE", Decimal(0), 0),
    )
    detected = sum(1 for result in results if result.detected)
    executable = sum(1 for result in results if result.executable)
    phantom_rate = Decimal(detected - executable) / Decimal(detected) if detected else Decimal(0)
    return ResearchMetrics(
        detected_opportunities=detected,
        executable_opportunities=executable,
        phantom_rate=phantom_rate,
        net_pnl=sum((result.net_profit for result in results), Decimal(0)),
        failed_hedge_losses=sum(
            (abs(result.net_profit) for result in results if result.outcome == "LEG_B_FAILED"),
            Decimal(0),
        ),
        median_lifetime_ms=int(
            median(result.lifetime_ms for result in results if result.lifetime_ms)
        ),
        scenarios=results,
    )
