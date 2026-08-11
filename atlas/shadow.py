from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from atlas.fingerprints import build_fingerprint
from atlas.models import ContractPair, Market, OrderBook
from atlas.verification import verify_equivalence


def find_shadow_pairs(
    market_a: list[Market], market_b: list[Market], limit: int = 10
) -> list[ContractPair]:
    """Find executable-side complements that remain blocked from approval."""
    right_by_event: dict[tuple[str, str | None], list[Market]] = {}
    for market in market_b:
        fingerprint = build_fingerprint(market)
        right_by_event.setdefault(
            (fingerprint.event_subject, fingerprint.market_type), []
        ).append(market)

    pairs: list[ContractPair] = []
    for left in market_a:
        left_fingerprint = build_fingerprint(left)
        event_key = (left_fingerprint.event_subject, left_fingerprint.market_type)
        for right in right_by_event.get(event_key, []):
            right_fingerprint = build_fingerprint(right)
            if not _is_shadow_complement(left_fingerprint, right_fingerprint):
                continue
            pair = verify_equivalence(
                left, right, f"shadow:{left.market_id}::{right.market_id}"
            )
            if pair.differences:
                pairs.append(pair)
    pairs.sort(key=lambda pair: (len(pair.differences), pair.pair_id))
    return pairs[:limit]


def observe_shadow_pair(
    pair: ContractPair,
    book_a: OrderBook,
    book_b: OrderBook,
    contracts: Decimal = Decimal(10),
) -> dict[str, object] | None:
    directions = [
        _direction_quote(book_a, "YES", book_b, "YES", contracts),
        _direction_quote(book_a, "NO", book_b, "NO", contracts),
    ]
    valid = [quote for quote in directions if quote is not None]
    if not valid:
        return None
    best = min(valid, key=lambda quote: Decimal(str(quote["gross_cost"])))
    expected_inverse = {
        "AFFIRMATIVE_OUTCOME_MISMATCH",
        "SIGNED_LINE_MISMATCH",
    }
    blockers = [code for code in pair.differences if code not in expected_inverse]
    policy = pair.decision.fingerprint_a.settlement_policy if pair.decision else None
    if (
        policy
        and "fair_price" in policy
        and "NON_GUARANTEED_SETTLEMENT" not in blockers
    ):
        blockers.append("NON_GUARANTEED_SETTLEMENT")
    return {
        "observation_id": str(uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "pair_id": pair.pair_id,
        "kalshi_market_id": pair.market_a.market_id,
        "polymarket_market_id": pair.market_b.market_id,
        "kalshi_title": pair.market_a.title,
        "polymarket_title": pair.market_b.title,
        "best_direction": best,
        "directions": valid,
        "relationship_codes": [
            "AFFIRMATIVE_OUTCOME_COMPLEMENT",
            "SIGNED_LINE_COMPLEMENT",
        ],
        "blockers": blockers,
        "rule_status": "BLOCKED",
        "guaranteed_payout": False,
        "paper_trade_created": False,
    }


def _is_shadow_complement(left: object, right: object) -> bool:
    same_fields = (
        "event_subject",
        "event_action",
        "contract_scope",
        "threshold",
        "threshold_operator",
        "threshold_unit",
        "settlement_policy",
        "market_type",
    )
    if any(getattr(left, field) != getattr(right, field) for field in same_fields):
        return False
    if not left.affirmative_outcome or not right.affirmative_outcome:
        return False
    if left.affirmative_outcome == right.affirmative_outcome:
        return False
    if left.signed_line is None or right.signed_line is None:
        return False
    return left.signed_line == -right.signed_line


def _direction_quote(
    book_a: OrderBook,
    side_a: str,
    book_b: OrderBook,
    side_b: str,
    contracts: Decimal,
) -> dict[str, object] | None:
    fill_a, cost_a = _fill(book_a.asks_for(side_a), contracts)
    fill_b, cost_b = _fill(book_b.asks_for(side_b), contracts)
    filled = min(fill_a, fill_b)
    if filled <= 0:
        return None
    if fill_a != filled:
        _, cost_a = _fill(book_a.asks_for(side_a), filled)
    if fill_b != filled:
        _, cost_b = _fill(book_b.asks_for(side_b), filled)
    gross_cost = cost_a + cost_b
    return {
        "kalshi_side": side_a,
        "polymarket_side": side_b,
        "contracts": str(filled),
        "gross_cost": str(gross_cost),
        "raw_edge_if_complementary": str(filled - gross_cost),
    }


def _fill(levels: list, contracts: Decimal) -> tuple[Decimal, Decimal]:
    remaining, cost, filled = contracts, Decimal(0), Decimal(0)
    for level in sorted(levels, key=lambda item: item.price):
        amount = min(remaining, level.quantity)
        cost += amount * level.price
        filled += amount
        remaining -= amount
        if remaining <= 0:
            break
    return filled, cost
