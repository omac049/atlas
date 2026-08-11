from atlas.learning import record_verified_pair
from atlas.outcomes import OutcomeStatus, reconcile_pair, settled_outcome
from atlas.storage import AtlasStore
from atlas.venues.kalshi import KalshiVenue
from atlas.venues.polymarket_us import PolymarketUSVenue


async def reconcile_pending_trades(store: AtlasStore | None = None) -> dict[str, int]:
    store = store or AtlasStore()
    summary = {"pending": 0, "confirmed": 0, "diverged": 0}
    for context in await store.pending_trade_contexts():
        pair = context["pair_json"]
        from atlas.models import ContractPair

        contract_pair = ContractPair.model_validate(pair)
        market_a = await KalshiVenue(fixture=False).get_market(contract_pair.market_a.venue_market_id)
        market_b = await PolymarketUSVenue(fixture=False).get_market(contract_pair.market_b.venue_market_id)
        contract_pair.market_a = market_a
        contract_pair.market_b = market_b
        status = reconcile_pair(contract_pair)
        if status is OutcomeStatus.PENDING:
            summary["pending"] += 1
            continue
        key = "confirmed" if status is OutcomeStatus.CONFIRMED else "diverged"
        summary[key] += 1
        outcome_a = settled_outcome(market_a)
        outcome_b = settled_outcome(market_b)
        await store.save_paper_trade_outcome(context["trade_id"], status.value, outcome_a, outcome_b)
        await record_verified_pair(
            store,
            contract_pair,
            "APPROVED_EQUIVALENT" if status is OutcomeStatus.CONFIRMED else "REJECTED",
            settlement_evidence={
                "settlement_verified": True,
                "settlement_status": "SETTLED",
                "relationship_status": status.value,
                "outcome_a": outcome_a,
                "outcome_b": outcome_b,
            },
        )
    return summary
