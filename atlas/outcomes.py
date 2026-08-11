from enum import StrEnum

from atlas.models import ContractPair, Market, MarketStatus


class OutcomeStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    DIVERGED = "DIVERGED"


def reconcile_pair(pair: ContractPair) -> OutcomeStatus:
    outcome_a = settled_outcome(pair.market_a)
    outcome_b = settled_outcome(pair.market_b)
    if outcome_a is None or outcome_b is None:
        return OutcomeStatus.PENDING
    return OutcomeStatus.CONFIRMED if outcome_a == outcome_b else OutcomeStatus.DIVERGED


def settled_outcome(market: Market) -> str | None:
    if market.status is not MarketStatus.SETTLED:
        return None
    raw = market.raw_market_json
    direct = raw.get("result") or raw.get("settlement_value") or raw.get("expiration_value")
    if str(direct).lower() in {"yes", "no"}:
        return str(direct).lower()
    prices = raw.get("outcomePrices")
    if isinstance(prices, str):
        prices = prices.strip("[]").replace('"', "").split(",")
    if isinstance(prices, list) and prices:
        try:
            return "yes" if float(prices[0]) >= 0.99 else "no" if float(prices[0]) <= 0.01 else None
        except (TypeError, ValueError):
            return None
    return None
