from decimal import Decimal
from uuid import uuid4

from atlas.models import ContractPair, Opportunity, OrderBook


def _fill(levels, contracts: Decimal) -> tuple[Decimal, Decimal]:
    remaining, cost, filled = contracts, Decimal(0), Decimal(0)
    for level in sorted(levels, key=lambda x: x.price):
        amount = min(remaining, level.quantity)
        cost += amount * level.price
        filled += amount
        remaining -= amount
        if remaining <= 0:
            break
    return filled, cost


def calculate_opportunity(
    pair: ContractPair,
    book_a: OrderBook,
    book_b: OrderBook,
    contracts: Decimal = Decimal(100),
    fees: Decimal = Decimal(0),
    slippage: Decimal = Decimal(0),
) -> Opportunity | None:
    if pair.status.value not in {"APPROVED_EQUIVALENT", "APPROVED_INVERSE"}:
        return None
    side_b = "YES" if pair.status.value == "APPROVED_INVERSE" else "NO"
    levels_a, levels_b = book_a.asks_for("YES"), book_b.asks_for(side_b)
    filled_a, cost_a = _fill(levels_a, contracts)
    filled_b, cost_b = _fill(levels_b, contracts)
    filled = min(filled_a, filled_b)
    if filled <= 0:
        return None
    if filled_a != filled:
        _, cost_a = _fill(levels_a, filled)
    if filled_b != filled:
        _, cost_b = _fill(levels_b, filled)
    gross_cost = cost_a + cost_b
    net_cost = gross_cost + fees + slippage
    payout = filled
    profit = payout - net_cost
    roi = (profit / net_cost) if net_cost else Decimal(0)
    return Opportunity(
        opportunity_id=str(uuid4()),
        pair_id=pair.pair_id,
        leg_a_venue=book_a.venue,
        leg_a_market_id=book_a.market_id,
        leg_a_side="YES",
        leg_a_average_price=cost_a / filled,
        leg_b_venue=book_b.venue,
        leg_b_market_id=book_b.market_id,
        leg_b_side=side_b,
        leg_b_average_price=cost_b / filled,
        contracts=filled,
        gross_cost=gross_cost,
        fees=fees,
        slippage=slippage,
        net_cost=net_cost,
        guaranteed_payout=payout,
        expected_profit=profit.quantize(Decimal("0.0001")),
        expected_roi=roi.quantize(Decimal("0.0001")),
        rule_check="PASS",
        status="EXECUTE" if profit > 0 else "REJECT",
    )
