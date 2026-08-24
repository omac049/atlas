import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from atlas.learning import record_verified_pair
from atlas.models import MarketStatus, VenueName
from atlas.storage import AtlasStore
from atlas.validation import (
    _apply_terminal_settlement,
    _pending_evidence_reasons,
    capture_validation_universe,
    market_evidence_snapshot,
    reconcile_validation_cases,
    settlement_lag_observation,
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


class TimedEvidenceVenue(SettledVenue):
    """Venue whose terminal evidence carries a normalized settlement time."""

    def __init__(self, market, settlement, settled_at):
        super().__init__(market)
        self.settlement = settlement
        self.settled_at = settled_at

    async def get_terminal_settlement_evidence(self, market_id):
        return {
            "source": "timed-test",
            "status": "settled",
            "settlement": self.settlement,
            "settled_at": self.settled_at,
        }


def _stored_outcome(store, pair_id):
    with sqlite3.connect(store.path) as db:
        row = db.execute(
            "SELECT payload_json FROM validation_outcomes WHERE pair_id = ?",
            (pair_id,),
        ).fetchone()
    return json.loads(row[0])


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


def _settled_leg_pair(pair_id, settled_at_a, settled_at_b):
    markets = fixture_markets()
    pair = verify_equivalence(markets["kalshi"][0], markets["polymarket_us"][0], pair_id)
    left = pair.market_a.model_copy(deep=True)
    right = pair.market_b.model_copy(deep=True)
    left.raw_market_json["settlement_evidence"] = {"settled_at": settled_at_a}
    right.raw_market_json["settlement_evidence"] = {"settled_at": settled_at_b}
    return pair, left, right


def test_settlement_lag_is_positive_when_the_kalshi_leg_settles_first():
    """Sign convention: positive lag == first (Kalshi) leg settled earlier."""
    _, left, right = _settled_leg_pair(
        "lag-sign",
        "2026-11-04T02:00:00+00:00",
        "2026-11-06T02:00:00+00:00",
    )

    observation = settlement_lag_observation(left, right)

    assert observation["kalshi_settled_at"] == "2026-11-04T02:00:00+00:00"
    assert observation["polymarket_settled_at"] == "2026-11-06T02:00:00+00:00"
    assert observation["settlement_lag_seconds"] == 172800.0
    assert observation["first_settled_venue"] == left.venue.value
    assert observation["settled_same_day"] is False


def test_settlement_lag_is_negative_when_the_polymarket_leg_settles_first():
    _, left, right = _settled_leg_pair(
        "lag-sign-inverse",
        "2026-11-04T12:00:00+00:00",
        "2026-11-04T06:00:00+00:00",
    )

    observation = settlement_lag_observation(left, right)

    assert observation["settlement_lag_seconds"] == -21600.0
    assert observation["first_settled_venue"] == right.venue.value
    assert observation["settled_same_day"] is True


@pytest.mark.parametrize(
    ("left_ts", "right_ts"),
    [(None, "2026-11-06T02:00:00+00:00"), ("2026-11-04T02:00:00+00:00", None), (None, None)],
)
def test_missing_settlement_timestamp_yields_no_lag_without_raising(left_ts, right_ts):
    _, left, right = _settled_leg_pair("lag-missing", left_ts, right_ts)

    observation = settlement_lag_observation(left, right)

    assert observation["settlement_lag_seconds"] is None
    assert observation["first_settled_venue"] is None
    assert observation["settled_same_day"] is None


def test_settlement_lag_falls_back_to_venue_specific_timestamp_keys():
    """Evidence recorded before `settled_at` existed still carries raw stamps."""
    _, left, right = _settled_leg_pair("lag-legacy", None, None)
    left.raw_market_json["settlement_evidence"] = {
        "settlement_ts": "2026-11-04T02:00:00.155125Z"
    }
    right.raw_market_json["settlement_evidence"] = {"closed_time": "2026-11-04 03:00:00+00"}

    observation = settlement_lag_observation(left, right)

    assert observation["kalshi_settled_at"] == "2026-11-04T02:00:00.155125+00:00"
    assert observation["polymarket_settled_at"] == "2026-11-04T03:00:00+00:00"
    assert observation["settlement_lag_seconds"] == pytest.approx(3599.844875)


@pytest.mark.asyncio
async def test_resolved_outcome_records_both_settlement_times_and_signed_lag(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    pair, left, right = _settled_leg_pair("lag-outcome", None, None)
    await _save_case(store, pair)
    left.status = MarketStatus.CLOSED
    right.status = MarketStatus.CLOSED
    left.raw_market_json.pop("result", None)
    right.raw_market_json.pop("outcomePrices", None)
    left.raw_market_json.pop("settlement_evidence", None)
    right.raw_market_json.pop("settlement_evidence", None)

    result = await reconcile_validation_cases(
        store,
        TimedEvidenceVenue(left, "1", "2026-11-04T02:00:00Z"),
        TimedEvidenceVenue(right, "1", "2026-11-19T18:30:00Z"),
        now=datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert result["resolved"] == 1
    stored = _stored_outcome(store, pair.pair_id)
    assert stored["kalshi_settled_at"] == "2026-11-04T02:00:00+00:00"
    assert stored["polymarket_settled_at"] == "2026-11-19T18:30:00+00:00"
    assert stored["settlement_lag_seconds"] == 1355400.0
    assert stored["first_settled_venue"] == left.venue.value
    # Observability only: the existing label fields are untouched.
    assert stored["relationship_status"] == "CONFIRMED"
    assert stored["trusted_label"] == "APPROVED_EQUIVALENT"
    assert stored["evidence"]["settlement_lag_seconds"] == 1355400.0
    lag = (await store.validation_summary())["settlement_lag"]
    assert lag["pairs_with_lag"] == 1
    assert lag["median_lag_seconds"] == 1355400.0
    assert lag["different_day_pairs"] == 1


@pytest.mark.asyncio
async def test_resolved_outcome_without_timestamps_records_null_lag(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    pair, left, right = _settled_leg_pair("lag-outcome-missing", None, None)
    await _save_case(store, pair)
    left.status = MarketStatus.CLOSED
    right.status = MarketStatus.CLOSED
    left.raw_market_json.pop("result", None)
    right.raw_market_json.pop("outcomePrices", None)
    left.raw_market_json.pop("settlement_evidence", None)
    right.raw_market_json.pop("settlement_evidence", None)

    result = await reconcile_validation_cases(
        store,
        TerminalEvidenceVenue(left, "1"),
        TerminalEvidenceVenue(right, "1"),
        now=datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert result["resolved"] == 1
    stored = _stored_outcome(store, pair.pair_id)
    assert stored["settlement_lag_seconds"] is None
    assert stored["first_settled_venue"] is None
    assert (await store.validation_summary())["settlement_lag"]["pairs_with_lag"] == 0


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


class EvidenceOnlyGlobalVenue:
    """Mirrors PolymarketGlobalHistoricalVenue: evidence methods, no get_market."""

    def __init__(self, evidence):
        self.evidence = evidence
        self.asked_for = []

    async def get_terminal_settlement_evidence(self, market_id):
        self.asked_for.append(market_id)
        return self.evidence


@pytest.mark.asyncio
async def test_a_global_leg_is_reconciled_through_its_own_venue_adapter(tmp_path):
    """Every approved pair on record is Kalshi <-> polymarket_global.

    Routing those through the US gateway returns 404 for a Global slug, which
    the caller records as VENUE_EVIDENCE_UNAVAILABLE and retries forever — a
    permanent stall that reads like a venue outage. The leg's own venue decides
    the adapter, and an adapter without `get_market` still reconciles, because
    terminal evidence is what reconciliation adjudicates on.
    """
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    markets = fixture_markets()
    global_leg = markets["polymarket_us"][0].model_copy(deep=True)
    global_leg.venue = VenueName.POLYMARKET_GLOBAL
    pair = verify_equivalence(markets["kalshi"][0], global_leg, "global-pair")
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

    global_venue = EvidenceOnlyGlobalVenue(
        {"status": "settled", "outcome": "yes", "source": "global-final-book"}
    )
    result = await reconcile_validation_cases(
        store,
        SettledVenue(settled_a),
        SettledVenue(settled_a),  # the US adapter: must NOT be used for this leg
        now=datetime(2030, 1, 1, tzinfo=UTC),
        extra_polymarket_venues={VenueName.POLYMARKET_GLOBAL.value: global_venue},
    )
    assert global_venue.asked_for == [pair.market_b.venue_market_id]
    assert result["checked"] == 1
    assert result["errors"] == 0


@pytest.mark.asyncio
async def test_a_leg_with_no_adapter_is_pending_not_an_error(tmp_path):
    """Never asked is not the same as asked-and-failed.

    VENUE_EVIDENCE_UNAVAILABLE means the adapter could not answer and retrying
    may help. A missing adapter means retrying changes nothing until a caller
    wires the venue in, so it gets its own reason and does not burn the retry
    budget.
    """
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    markets = fixture_markets()
    global_leg = markets["polymarket_us"][0].model_copy(deep=True)
    global_leg.venue = VenueName.POLYMARKET_GLOBAL
    pair = verify_equivalence(markets["kalshi"][0], global_leg, "unrouted-pair")
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
    result = await reconcile_validation_cases(
        store,
        SettledVenue(pair.market_a),
        SettledVenue(pair.market_b),  # US adapter only — no Global route supplied
        now=datetime(2030, 1, 1, tzinfo=UTC),
    )
    assert result["errors"] == 0
    assert result["checked"] == 0
    assert result["pending"] == 1
    pending = await store.pending_validation_cases(limit=5, due_only=False)
    assert pending[0]["pending_reason"] == "VENUE_ADAPTER_MISSING"
