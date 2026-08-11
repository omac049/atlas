import json

import pytest

from atlas.backfill import backfill_historical_validation
from atlas.models import MarketStatus
from atlas.storage import AtlasStore
from atlas.venues.fixtures import fixture_markets


class _KalshiHistory:
    def __init__(self, markets):
        self.markets = markets

    async def list_settled_events(self, max_pages=100):
        return [{
            "event_ticker": "KXFED-SEP26",
            "title": "Federal Reserve raises federal funds target September 2026",
            "sub_title": "25 basis points",
        }]

    async def list_settled_event_markets(self, event_ticker):
        return self.markets


class _PolymarketHistory:
    def __init__(self, markets):
        self.markets = markets

    async def list_closed_markets(self, max_pages=20):
        return self.markets

    async def get_terminal_settlement_evidence(self, market_id):
        return {
            "source": "terminal_market_book",
            "settlement": "1",
            "state": "MARKET_STATE_EXPIRED",
        }


def _settled_fixture_markets():
    markets = fixture_markets()
    exact = markets["kalshi"][0].model_copy(deep=True)
    exact.status = MarketStatus.SETTLED
    exact.raw_market_json.update({"result": "yes", "event_ticker": "KXFED-SEP26"})
    mismatch = exact.model_copy(deep=True)
    mismatch.market_id = "kalshi:KXFED-SEP26-T50"
    mismatch.venue_market_id = "KXFED-SEP26-T50"
    mismatch.raw_market_json["result"] = "no"
    polymarket = markets["polymarket_us"][0].model_copy(deep=True)
    polymarket.status = MarketStatus.CLOSED
    polymarket.raw_market_json["question"] = polymarket.title
    return [exact, mismatch], [polymarket]


@pytest.mark.asyncio
async def test_completed_backfill_automatically_exports_provenance_bundle(tmp_path):
    kalshi, polymarket = _settled_fixture_markets()
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))

    report = await backfill_historical_validation(
        store,
        _KalshiHistory(kalshi),
        _PolymarketHistory(polymarket),
        target_labels=2,
    )

    artifact = report["training_artifacts"]
    manifest = json.loads((tmp_path / "training" / "manifest.json").read_text())
    assert artifact["trusted_labels"] == 2
    assert manifest["source"]["kind"] == "HISTORICAL_BACKFILL"
    assert manifest["source"]["started_at"] == report["started_at"]
    assert manifest["source"]["completed_at"] == report["completed_at"]
    assert manifest["counts"]["trusted_labels"] == 2
    assert manifest["label_mix"] == {
        "APPROVED_EQUIVALENT": 1,
        "REJECTED": 1,
    }
    assert manifest["counts"]["untrusted_or_inconclusive_examples_exported"] == 0
    assert manifest["paper_only"] is True
    assert manifest["execution_enabled"] is False
    assert manifest["live_orders_enabled"] is False
    assert (tmp_path / "training" / "atlas.jsonl").exists()
    assert (tmp_path / "training" / "atlas-eval.jsonl").exists()


@pytest.mark.asyncio
async def test_automatic_bundle_excludes_review_and_unverified_rows(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_learning_example(
        "review", "REVIEW_REQUIRED", {"evidence": {"settlement_verified": True}}
    )
    await store.save_learning_example(
        "unverified", "APPROVED_EQUIVALENT", {"evidence": {"settlement_verified": False}}
    )
    await store.save_learning_example(
        "observation", "UNLABELED", {"evidence": {"settlement_verified": True}}
    )

    from atlas.learning import export_training_bundle

    result = await export_training_bundle(
        store,
        str(tmp_path / "training"),
        backfill_report={
            "status": "LABEL_MIX_BLOCKED",
            "started_at": "2026-08-10T00:00:00+00:00",
            "completed_at": "2026-08-10T00:01:00+00:00",
            "venue_coverage": {},
        },
    )
    manifest = json.loads((tmp_path / "training" / "manifest.json").read_text())

    assert result["trusted_labels"] == 0
    assert manifest["counts"]["trusted_labels"] == 0
    assert manifest["counts"]["untrusted_or_inconclusive_examples_exported"] == 0
    assert manifest["trust_policy"]["review_and_inconclusive_exported"] is False
    assert (tmp_path / "training" / "atlas.jsonl").read_text() == ""
    assert (tmp_path / "training" / "atlas-eval.jsonl").read_text() == ""
