from datetime import UTC, datetime

import pytest

from atlas.learning import record_verified_pair
from atlas.models import MarketStatus
from atlas.storage import AtlasStore
from atlas.validation import (
    capture_validation_universe,
    market_evidence_snapshot,
    reconcile_validation_cases,
)
from atlas.venues.fixtures import fixture_markets
from atlas.verification import verify_equivalence


class SettledVenue:
    def __init__(self, market):
        self.market = market

    async def get_market(self, market_id):
        return self.market


class SettlementEndpointVenue(SettledVenue):
    def __init__(self, market, settlement):
        super().__init__(market)
        self.settlement = settlement

    async def get_settlement(self, market_id):
        return {"slug": market_id, "settlement": self.settlement}


@pytest.mark.asyncio
async def test_market_evidence_is_versioned_only_when_content_changes(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    market = fixture_markets()["kalshi"][0]
    snapshot = market_evidence_snapshot(market, "TEST")
    first = await store.save_market_evidence_snapshots([snapshot])
    second = await store.save_market_evidence_snapshots([snapshot])
    market.raw_rules_text += " Rule amendment."
    third = await store.save_market_evidence_snapshots(
        [market_evidence_snapshot(market, "TEST")]
    )
    assert first["new_versions"] == 1
    assert second["new_versions"] == 0
    assert third["new_versions"] == 1
    summary = await store.validation_summary()
    assert summary["markets_tracked"] == 1
    assert summary["rule_changes"] == 1


@pytest.mark.asyncio
async def test_approval_without_settlement_cannot_create_learning_label(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    markets = fixture_markets()
    pair = verify_equivalence(markets["kalshi"][0], markets["polymarket_us"][0])
    with pytest.raises(ValueError, match="settlement-verified"):
        await record_verified_pair(store, pair, "APPROVED_EQUIVALENT")


@pytest.mark.asyncio
async def test_settled_approved_pair_creates_trusted_label(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    markets = fixture_markets()
    pair = verify_equivalence(
        markets["kalshi"][0], markets["polymarket_us"][0], "settled-pair"
    )
    await store.save_validation_case(
        {
            "pair_id": pair.pair_id,
            "source_kind": "APPROVED",
            "decision_status": pair.status.value,
            "guarantee_a": "GUARANTEED",
            "guarantee_b": "GUARANTEED",
            "payload": {"pair": pair.model_dump(mode="json")},
        }
    )
    settled_a = pair.market_a.model_copy(deep=True)
    settled_b = pair.market_b.model_copy(deep=True)
    settled_a.status = MarketStatus.SETTLED
    settled_b.status = MarketStatus.SETTLED
    settled_a.raw_market_json["result"] = "yes"
    settled_b.raw_market_json["result"] = "yes"
    result = await reconcile_validation_cases(
        store,
        SettledVenue(settled_a),
        SettledVenue(settled_b),
        now=datetime(2030, 1, 1, tzinfo=UTC),
    )
    assert result["resolved"] == 1
    assert result["labeled"] == 1
    assert (await store.validation_summary())["trusted_labels"] == 1
    assert (await store.trusted_learning_counts())["APPROVED_EQUIVALENT"] == 1


@pytest.mark.asyncio
async def test_final_settlement_endpoint_closes_polymarket_validation_case(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    markets = fixture_markets()
    pair = verify_equivalence(
        markets["kalshi"][0], markets["polymarket_us"][0], "endpoint-pair"
    )
    await store.save_validation_case(
        {
            "pair_id": pair.pair_id,
            "source_kind": "APPROVED",
            "decision_status": pair.status.value,
            "guarantee_a": "GUARANTEED",
            "guarantee_b": "GUARANTEED",
            "payload": {"pair": pair.model_dump(mode="json")},
        }
    )
    settled_a = pair.market_a.model_copy(deep=True)
    settled_a.status = MarketStatus.SETTLED
    settled_a.raw_market_json["result"] = "yes"
    closed_b = pair.market_b.model_copy(deep=True)
    closed_b.status = MarketStatus.CLOSED
    closed_b.raw_market_json.pop("outcomePrices", None)
    result = await reconcile_validation_cases(
        store,
        SettledVenue(settled_a),
        SettlementEndpointVenue(closed_b, "1.0"),
        now=datetime(2030, 1, 1, tzinfo=UTC),
    )
    assert result["resolved"] == 1
    assert result["labeled"] == 1
    assert await store.pending_validation_cases() == []
    assert market_evidence_snapshot(closed_b, "TEST")["payload"][
        "settlement_evidence"
    ] == {"slug": closed_b.venue_market_id, "settlement": "1.0"}


@pytest.mark.asyncio
async def test_capture_tracks_safe_market_versions_and_approved_case(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    markets = fixture_markets()
    pair = verify_equivalence(
        markets["kalshi"][0], markets["polymarket_us"][0], "approved-pair"
    )
    result = await capture_validation_universe(
        store,
        markets["kalshi"],
        markets["polymarket_us"],
        [pair],
        [],
    )
    assert result["markets_observed"] == 2
    assert result["new_validation_cases"] == 1
    summary = await store.validation_summary()
    assert summary["markets_tracked"] == 2
    assert summary["awaiting_settlement"] == 1
