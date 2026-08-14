import json

import pytest

from atlas.backfill import backfill_historical_validation
from atlas.learning import example_family, export_training_bundle
from atlas.models import MarketStatus
from atlas.storage import AtlasStore
from atlas.venues.fixtures import fixture_markets


class _HistoricalKalshiVenue:
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


class _HistoricalPolymarketVenue:
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


@pytest.mark.asyncio
async def test_completed_backfill_exports_provenance_bundle_without_live_io(tmp_path):
    markets = fixture_markets()
    kalshi = markets["kalshi"][0].model_copy(deep=True)
    kalshi.status = MarketStatus.SETTLED
    kalshi.raw_market_json.update({"result": "yes", "event_ticker": "KXFED-SEP26"})
    polymarket = markets["polymarket_us"][0].model_copy(deep=True)
    polymarket.status = MarketStatus.CLOSED
    polymarket.raw_market_json["question"] = polymarket.title
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))

    report = await backfill_historical_validation(
        store,
        _HistoricalKalshiVenue([kalshi]),
        _HistoricalPolymarketVenue([polymarket]),
        target_labels=1,
        training_output_dir=str(tmp_path / "training"),
    )

    artifacts = report["training_artifacts"]
    manifest_path = tmp_path / "training" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert artifacts["paper_only"] is True
    assert artifacts["execution_enabled"] is False
    assert manifest["source"]["kind"] == "HISTORICAL_BACKFILL"
    assert manifest["source"]["status"] == report["status"]
    assert manifest["source"]["started_at"] == report["started_at"]
    assert manifest["source"]["completed_at"] == report["completed_at"]
    assert manifest["label_mix"]["APPROVED_EQUIVALENT"] == 1
    assert manifest["counts"]["trusted_labels"] == 1
    assert manifest["trust_policy"]["review_and_inconclusive_exported"] is False
    # The fixture pair is a generic binary (market_type "binary"), so its
    # honest family is "other"; real macro rows carry "economic".
    assert manifest["label_families"] == {"other": {"APPROVED_EQUIVALENT": 1}}
    assert (tmp_path / "training" / "atlas.jsonl").exists()
    assert (tmp_path / "training" / "atlas-eval.jsonl").exists()
    exported_rows = [
        json.loads(line)
        for path in ("atlas.jsonl", "atlas-eval.jsonl")
        for line in (tmp_path / "training" / path).read_text().splitlines()
    ]
    assert all(row["family"] == "other" for row in exported_rows)


def test_example_family_slices_hard_negative_families():
    """Sports/crypto rejections are curriculum families, not discards: the
    family tag is derived only from the recorded fingerprint so exports can be
    weighted and evaluated per family (the 2026-08-14 tennis rejections are
    hard negatives — same-subject pairs the candidate matcher itself chose)."""

    def example(market_type, subject):
        return {
            "label": "REJECTED",
            "payload": {
                "decision": {
                    "fingerprint_a": {"market_type": market_type, "event_subject": subject}
                }
            },
        }

    assert example_family(example("economic", "us_cpi_yoy|2026-07")) == "economic"
    assert (
        example_family(example("spread", "alexander shevchenko|botic van de zandschulp|2026-08-14"))
        == "sports"
    )
    assert example_family(example(None, "crypto_price|btc|2026-08-13T16:00Z")) == "crypto"
    assert example_family(example("weather", "ksfo_high_temp|2026-08-14")) == "weather"
    assert example_family(example(None, "something else")) == "other"
    assert example_family({"label": "REJECTED", "payload": {}}) == "other"


@pytest.mark.asyncio
async def test_training_bundle_excludes_untrusted_and_inconclusive_rows(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    trusted_payload = {
        "market_a": {"id": "a"},
        "market_b": {"id": "b"},
        "evidence": {"settlement_verified": True},
    }
    await store.save_learning_example("approved", "APPROVED_EQUIVALENT", trusted_payload)
    await store.save_learning_example("rejected", "REJECTED", trusted_payload)
    await store.save_learning_example("review", "REVIEW_REQUIRED", trusted_payload)
    await store.save_learning_example("observation", "UNLABELED", trusted_payload)
    await store.save_learning_example(
        "unverified", "APPROVED_EQUIVALENT", {**trusted_payload, "evidence": {}}
    )

    await export_training_bundle(
        store,
        str(tmp_path / "training"),
        backfill_report={
            "status": "LABEL_MIX_BLOCKED",
            "started_at": "2026-08-10T00:00:00+00:00",
            "completed_at": "2026-08-10T00:01:00+00:00",
        },
    )

    rows = []
    for filename in ("atlas.jsonl", "atlas-eval.jsonl"):
        rows.extend(
            json.loads(line)
            for line in (tmp_path / "training" / filename).read_text().splitlines()
        )
    labels = {row["messages"][-1]["content"] for row in rows}
    manifest = json.loads((tmp_path / "training" / "manifest.json").read_text())
    assert len(rows) == 2
    assert labels == {"APPROVED_EQUIVALENT", "REJECTED"}
    assert manifest["counts"]["trusted_labels"] == 2
    assert manifest["counts"]["excluded_rows"] == 3
    assert manifest["excluded_rows_by_label"] == {
        "APPROVED_EQUIVALENT:UNVERIFIED": 1,
        "REVIEW_REQUIRED": 1,
        "UNLABELED": 1,
    }
