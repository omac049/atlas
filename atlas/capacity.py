"""Capacity curve: how many baskets survive once you walk down the book.

Every gap number Atlas records is a TOP-OF-BOOK measurement — one price on
each venue, and whatever size happens to rest there. That is why the study's
median basket is three contracts: the gap exists precisely because someone
left a dust-sized order at a good price. The open question is what happens
when you try to take more than the touch: size grows, the average price
worsens, and at some depth the edge is gone.

This module answers that by walking both legs of a basket level by level.
For each additional contract it charges the true marginal prices and the
venue-published quadratic fees, and it stops at the last contract whose
marginal edge is still positive. The result is a curve — contracts against
surviving edge — plus the only number that matters for a capacity question:
total dollars available in this basket, right now.

MEASUREMENT ONLY. It reads books and reports arithmetic. It never places,
simulates placing, or scaffolds placing an order, and nothing here feeds an
approval label or the trading gate.

Two correctness details, both learned from live data on 2026-08-28:

- **Levels are sorted here, defensively.** Kalshi publishes bids only; its
  ask ladder is derived as ``1 - no_bid`` and arrives WORST-first. Consuming
  it in arrival order would charge the most expensive contracts first and
  understate capacity. Asks are sorted ascending before any walk.
- **Fees are charged per contract at that contract's own price**, not at the
  touch price, because both venues price fees quadratically in the level.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from atlas.gap_radar import kalshi_taker_fee_per_contract, polymarket_taker_fee_per_share
from atlas.models import OrderBook

# A basket is only a basket if both legs fill, so the walk stops at the first
# exhausted ladder. Reported explicitly: a curve that ends because the book
# ran out is a different fact from one that ends because the edge ran out.
STOP_EDGE_EXHAUSTED = "edge_exhausted"
STOP_BOOK_EXHAUSTED = "book_exhausted"

# Basket direction -> (kalshi book side, polymarket book side). Both legs are
# BUYS, so both consume the ask ladder of their respective side.
BASKET_SIDES = {
    "kalshi_yes+polymarket_no": ("yes_asks", "no_asks"),
    "kalshi_no+polymarket_yes": ("no_asks", "yes_asks"),
    "kalshi_yes+polymarket_yes": ("yes_asks", "yes_asks"),
    "kalshi_no+polymarket_no": ("no_asks", "no_asks"),
}


def _decimal(raw: object) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _ask_ladder(book: OrderBook, side: str) -> list[tuple[Decimal, Decimal]]:
    """(price, quantity) rungs for one side, cheapest first.

    Sorting is not cosmetic: Kalshi's derived ask ladder arrives worst-first,
    so arrival order would price the expensive contracts first.
    """
    levels = getattr(book, side, None) or []
    rungs: list[tuple[Decimal, Decimal]] = []
    for level in levels:
        price = _decimal(getattr(level, "price", None))
        quantity = _decimal(getattr(level, "quantity", None))
        if price is None or quantity is None or quantity <= 0:
            continue
        rungs.append((price, quantity))
    return sorted(rungs, key=lambda rung: rung[0])


def _walk(
    kalshi_rungs: list[tuple[Decimal, Decimal]],
    polymarket_rungs: list[tuple[Decimal, Decimal]],
    polymarket_raw: dict,
) -> tuple[list[dict], str]:
    """Consume both ladders in lockstep while the marginal basket still pays.

    Each step takes the largest block both ladders can fill at their current
    rungs, so the walk is bounded by the number of price levels rather than by
    contract count.
    """
    curve: list[dict] = []
    k_index = p_index = 0
    k_left = kalshi_rungs[0][1] if kalshi_rungs else Decimal(0)
    p_left = polymarket_rungs[0][1] if polymarket_rungs else Decimal(0)
    contracts = Decimal(0)
    profit = Decimal(0)
    stop = STOP_BOOK_EXHAUSTED
    while k_index < len(kalshi_rungs) and p_index < len(polymarket_rungs):
        k_price = kalshi_rungs[k_index][0]
        p_price = polymarket_rungs[p_index][0]
        fees = kalshi_taker_fee_per_contract(k_price) + polymarket_taker_fee_per_share(
            p_price, polymarket_raw
        )[0]
        edge = Decimal(1) - k_price - p_price - fees
        if edge <= 0:
            stop = STOP_EDGE_EXHAUSTED
            break
        block = min(k_left, p_left)
        if block <= 0:
            break
        contracts += block
        profit += edge * block
        curve.append(
            {
                "cumulative_contracts": str(contracts),
                "marginal_edge": str(edge),
                "kalshi_price": str(k_price),
                "polymarket_price": str(p_price),
                "block_contracts": str(block),
                "cumulative_profit_usd": str(profit),
            }
        )
        k_left -= block
        p_left -= block
        if k_left <= 0:
            k_index += 1
            if k_index < len(kalshi_rungs):
                k_left = kalshi_rungs[k_index][1]
        if p_left <= 0:
            p_index += 1
            if p_index < len(polymarket_rungs):
                p_left = polymarket_rungs[p_index][1]
    return curve, stop


def capacity_curve(
    kalshi_book: OrderBook,
    polymarket_book: OrderBook,
    legs: str,
    polymarket_raw: dict | None = None,
) -> dict:
    """Walk one basket direction down both books and report what survives.

    ``top_of_book_contracts`` is what the gap radar records today;
    ``profitable_contracts`` is what the full ladder supports. The pair of
    them is the whole point of this module.
    """
    sides = BASKET_SIDES.get(legs)
    if sides is None:
        return {"legs": legs, "supported": False, "reason": "unknown_basket_direction"}
    kalshi_rungs = _ask_ladder(kalshi_book, sides[0])
    polymarket_rungs = _ask_ladder(polymarket_book, sides[1])
    if not kalshi_rungs or not polymarket_rungs:
        return {"legs": legs, "supported": False, "reason": "empty_ladder"}
    curve, stop = _walk(kalshi_rungs, polymarket_rungs, polymarket_raw or {})
    if not curve:
        return {
            "legs": legs,
            "supported": True,
            "profitable_contracts": "0",
            "total_profit_usd": "0",
            "stop_reason": STOP_EDGE_EXHAUSTED,
            "curve": [],
        }
    top = curve[0]
    last = curve[-1]
    return {
        "legs": legs,
        "supported": True,
        "top_of_book_contracts": top["block_contracts"],
        "top_of_book_edge": top["marginal_edge"],
        "profitable_contracts": last["cumulative_contracts"],
        "final_marginal_edge": last["marginal_edge"],
        "total_profit_usd": last["cumulative_profit_usd"],
        "levels_consumed": len(curve),
        "stop_reason": stop,
        "curve": curve,
    }


def best_capacity(
    kalshi_book: OrderBook,
    polymarket_book: OrderBook,
    shape_legs: tuple[str, ...],
    polymarket_raw: dict | None = None,
) -> dict:
    """The most profitable direction of a pair, by total dollars available."""
    results = [
        capacity_curve(kalshi_book, polymarket_book, legs, polymarket_raw)
        for legs in shape_legs
    ]
    usable = [r for r in results if r.get("supported") and r.get("curve")]
    if not usable:
        return {"supported": False, "directions": results}
    best = max(usable, key=lambda r: Decimal(r["total_profit_usd"]))
    return {"supported": True, "best": best, "directions": results}
