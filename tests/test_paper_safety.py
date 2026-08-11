from decimal import Decimal

import pytest

from atlas.agent import AgentPolicy
from atlas.models import Opportunity
from atlas.paper import PaperExecutor


def test_agent_policy_rejects_execution_mode():
    with pytest.raises(ValueError, match="paper_only"):
        AgentPolicy(execution_mode="live").validate()


def test_paper_executor_returns_simulation_record_only():
    opportunity = Opportunity(
        opportunity_id="paper-safety",
        pair_id="pair-1",
        detected_at="2026-08-10T00:00:00Z",
        leg_a_venue="kalshi",
        leg_a_market_id="kalshi:one",
        leg_a_side="YES",
        leg_a_average_price=Decimal("0.40"),
        leg_b_venue="polymarket_us",
        leg_b_market_id="polymarket_us:two",
        leg_b_side="NO",
        leg_b_average_price=Decimal("0.50"),
        contracts=Decimal(1),
        gross_cost=Decimal("0.90"),
        fees=Decimal(0),
        slippage=Decimal(0),
        net_cost=Decimal("0.90"),
        guaranteed_payout=Decimal(1),
        expected_profit=Decimal("0.10"),
        expected_roi=Decimal("0.1111"),
        rule_check="PASS",
        status="PAPER_ONLY",
    )

    trade = PaperExecutor().execute(opportunity)

    assert trade.status == "PAPER_ONLY"
    assert trade.simulated_profit == Decimal("0.10")
    assert not hasattr(trade, "order_id")
    assert not hasattr(trade, "venue_order_id")
