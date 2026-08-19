from datetime import UTC, datetime, timedelta

import pytest

from atlas.learning import record_verified_pair
from atlas.models import MarketStatus
from atlas.storage import AtlasStore
from atlas.validation import (
    _apply_terminal_settlement,
    _pending_evidence_reasons,
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


class TerminalEvidenceVenue(SettledVenue):
    def __init__(self, market, settlement):
        super().__init__(market)
        self.settlement = settlement

    async def get_terminal_settlement_evidence(self, market_id):
        return {"source": "terminal-test", "settlement": self.settlement}


class PendingEvidenceVenue(SettledVenue):
    def __init__(self, market, evidence):
        super().__init__(market)
        self.evidence = evidence

    async def get_terminal_settlement_evidence(self, market_id):
        return dict(self.evidence)


class ExplodingVenue:
    async def get_market(self, market_id):
        raise AssertionError("venue was polled for a pair the planner deferred")


async def _save_case(store, pair, *, pair_id=None, max_retries=None):
    case = {
        "pair_id": pair_id or pair.pair_id,
        "source_kind": "APPROVED",
        "decision_status": pair.status.value,
        "guarantee_a": "GUARANTEED",
        "guarantee_b": "GUARANTEED",
        "payload": {"pair": pair.model_dump(mode="json")},
    }
    if max_retries is not None:
        case["max_retries"] = max_retries
    await store.save_validation_case(case)


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
async def test_terminal_evidence_refreshes_both_validation_legs(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    markets = fixture_markets()
    pair = verify_equivalence(
        markets["kalshi"][0], markets["polymarket_us"][0], "terminal-both-legs"
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
    left = pair.market_a.model_copy(deep=True)
    right = pair.market_b.model_copy(deep=True)
    left.status = MarketStatus.CLOSED
    right.status = MarketStatus.CLOSED
    left.raw_market_json.pop("result", None)
    right.raw_market_json.pop("outcomePrices", None)
    result = await reconcile_validation_cases(
        store,
        TerminalEvidenceVenue(left, "1"),
        TerminalEvidenceVenue(right, "1"),
        now=datetime(2030, 1, 1, tzinfo=UTC),
    )
    assert result["resolved"] == 1
    assert result["labeled"] == 1
    outcome = (await store.validation_summary())
    assert outcome["trusted_labels"] == 1


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


NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _fixture_pair(pair_id, close=None, resolution=None):
    markets = fixture_markets()
    pair = verify_equivalence(markets["kalshi"][0], markets["polymarket_us"][0], pair_id)
    for market in (pair.market_a, pair.market_b):
        market.close_time = close
        market.resolution_time = resolution
    return pair


@pytest.mark.asyncio
async def test_apply_terminal_settlement_ignores_unusable_payloads():
    market = fixture_markets()["kalshi"][0].model_copy(deep=True)
    market.status = MarketStatus.CLOSED
    market.raw_market_json.pop("result", None)

    class UnusableVenue:
        def __init__(self, payload):
            self.payload = payload

        async def get_terminal_settlement_evidence(self, market_id):
            return self.payload

    for payload in ({"settlement": "0.5"}, {"unrelated": "field"}):
        updated = await _apply_terminal_settlement(market, UnusableVenue(payload))
        assert updated.status is MarketStatus.CLOSED
        assert "settlement_evidence" not in updated.raw_market_json
        assert "settlement_value" not in updated.raw_market_json


def test_pending_evidence_reasons_report_only_pending_reasons():
    with_reason = fixture_markets()["kalshi"][0].model_copy(deep=True)
    with_reason.raw_market_json["settlement_evidence"] = {
        "status": "pending",
        "reason": "not_terminal",
    }
    settled = fixture_markets()["polymarket_us"][0].model_copy(deep=True)
    settled.raw_market_json["settlement_evidence"] = {"status": "settled"}
    reasonless = fixture_markets()["polymarket_us"][0].model_copy(deep=True)
    reasonless.raw_market_json["settlement_evidence"] = {"status": "pending"}

    assert _pending_evidence_reasons(with_reason, settled, reasonless) == [
        "kalshi:not_terminal"
    ]


@pytest.mark.asyncio
async def test_planner_pending_case_records_reason_and_next_poll_without_polling(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    pair = _fixture_pair(
        "not-closed-pair",
        close=NOW + timedelta(days=30),
        resolution=NOW + timedelta(days=31),
    )
    await _save_case(store, pair)

    result = await reconcile_validation_cases(
        store, ExplodingVenue(), ExplodingVenue(), now=NOW
    )

    assert result["checked"] == 0
    case = (await store.pending_validation_cases())[0]
    assert case["pending_reason"] == "NOT_CLOSED"
    assert datetime.fromisoformat(str(case["next_poll_at"])) == NOW + timedelta(hours=1)


@pytest.mark.asyncio
async def test_case_missing_from_lookup_is_counted_as_error_not_crash(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    pair = _fixture_pair("payload-pair-id")
    # The stored row key and the payload pair id diverge, so the planner's
    # decision has no matching case row.
    await _save_case(store, pair, pair_id="row-key-that-differs")

    result = await reconcile_validation_cases(
        store, ExplodingVenue(), ExplodingVenue(), now=NOW
    )

    assert result["errors"] == 1
    assert result["checked"] == 0


@pytest.mark.asyncio
async def test_pair_with_past_settlement_timing_flows_through_ready_plan(tmp_path):
    """A pair with real close/resolution timing must reach the venues via the
    planner's READY path, not the MISSING_SETTLEMENT_TIMING escape hatch."""
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    pair = _fixture_pair(
        "ready-pair",
        close=NOW - timedelta(days=2),
        resolution=NOW - timedelta(days=1),
    )
    await _save_case(store, pair)
    settled_a = pair.market_a.model_copy(deep=True)
    settled_b = pair.market_b.model_copy(deep=True)
    settled_a.status = MarketStatus.SETTLED
    settled_b.status = MarketStatus.SETTLED
    settled_a.raw_market_json["result"] = "yes"
    settled_b.raw_market_json["result"] = "yes"

    result = await reconcile_validation_cases(
        store, SettledVenue(settled_a), SettledVenue(settled_b), now=NOW
    )

    assert result["checked"] == 1
    assert result["resolved"] == 1


@pytest.mark.asyncio
async def test_transient_evidence_failure_increments_retry_then_clean_answer_resets(
    tmp_path,
):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    pair = _fixture_pair("retry-pair")
    await _save_case(store, pair)
    settled_a = pair.market_a.model_copy(deep=True)
    settled_a.status = MarketStatus.SETTLED
    settled_a.raw_market_json["result"] = "yes"

    def closed_b():
        market = pair.market_b.model_copy(deep=True)
        market.status = MarketStatus.CLOSED
        market.raw_market_json.pop("outcomePrices", None)
        return market

    failing = PendingEvidenceVenue(
        closed_b(),
        {"source": "test", "status": "pending", "reason": "venue_server_error",
         "retryable": True},
    )
    result = await reconcile_validation_cases(
        store, SettledVenue(settled_a), failing, now=NOW
    )
    case = (await store.pending_validation_cases())[0]
    assert result["pending"] == 1
    assert case["retry_count"] == 1
    assert case["pending_reason"] == "polymarket_us:venue_server_error"
    assert datetime.fromisoformat(str(case["next_poll_at"])) == NOW + timedelta(hours=1)

    clean = PendingEvidenceVenue(
        closed_b(),
        {"source": "test", "status": "pending", "reason": "not_terminal",
         "retryable": True},
    )
    later = NOW + timedelta(hours=2)
    await reconcile_validation_cases(store, SettledVenue(settled_a), clean, now=later)
    case = (await store.pending_validation_cases())[0]
    assert case["retry_count"] == 0
    assert case["pending_reason"] == "polymarket_us:not_terminal"


@pytest.mark.asyncio
async def test_non_retryable_evidence_failures_exhaust_the_case(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    pair = _fixture_pair("exhaust-pair")
    await _save_case(store, pair, max_retries=1)
    settled_a = pair.market_a.model_copy(deep=True)
    settled_a.status = MarketStatus.SETTLED
    settled_a.raw_market_json["result"] = "yes"
    closed_b = pair.market_b.model_copy(deep=True)
    closed_b.status = MarketStatus.CLOSED
    closed_b.raw_market_json.pop("outcomePrices", None)

    hard_failure = PendingEvidenceVenue(
        closed_b,
        {"source": "test", "status": "pending", "reason": "venue_client_error",
         "retryable": False, "http_status": 410},
    )
    result = await reconcile_validation_cases(
        store, SettledVenue(settled_a), hard_failure, now=NOW
    )

    assert result["pending"] == 1
    assert await store.pending_validation_cases() == []
    assert (await store.validation_summary())["retry_exhausted"] == 1
