from atlas.models import ContractPair, MatchStatus
from atlas.storage import AtlasStore
from atlas.verification import verify_equivalence


async def approve_pair(store: AtlasStore, market_a, market_b, approved_by: str) -> ContractPair:
    pair = verify_equivalence(market_a, market_b, f"{market_a.market_id}::{market_b.market_id}")
    if pair.status not in {
        MatchStatus.APPROVED_EQUIVALENT,
        MatchStatus.APPROVED_INVERSE,
    }:
        raise ValueError(f"pair failed deterministic verification: {pair.differences}")
    pair.approved_by = approved_by
    await store.save_pair(pair)
    return pair
