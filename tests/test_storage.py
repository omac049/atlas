import json

import aiosqlite
import pytest

from atlas.storage import AtlasStore
from atlas.venues.fixtures import fixture_books


@pytest.mark.asyncio
async def test_store_persists_orderbook(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    book = fixture_books()["kalshi:KALSHI-FED-SEP26"]
    await store.save_orderbook(book)
    assert (tmp_path / "atlas.sqlite3").exists()
    assert len(await store.latest_orderbooks()) == 1


@pytest.mark.asyncio
async def test_store_persists_latest_discovery_scan(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_discovery_scan({
        "kalshi_active": 2, "polymarket_active": 1, "comparisons": 2,
        "approved": 1, "review": 1,
    })
    scan = await store.latest_discovery_scan()
    assert scan["approved"] == 1
    assert scan["polymarket_active"] == 1


@pytest.mark.asyncio
async def test_learning_export_contains_only_labeled_examples(tmp_path):
    from atlas.learning import export_training_jsonl

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_learning_example(
        "one",
        "APPROVED_EQUIVALENT",
        {
            "market_a": {},
            "market_b": {},
            "evidence": {"settlement_verified": True},
        },
    )
    output = tmp_path / "training.jsonl"
    assert await export_training_jsonl(store, str(output)) == 1
    assert '"APPROVED_EQUIVALENT"' in output.read_text()


@pytest.mark.asyncio
async def test_learning_export_creates_deterministic_holdout_split(tmp_path):
    from atlas.learning import export_learning_splits

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    for label in ("APPROVED_EQUIVALENT", "REJECTED"):
        for index in range(3):
            await store.save_learning_example(
                f"{label}-{index}",
                label,
                {
                    "market_a": {"id": index},
                    "market_b": {"id": index + 10},
                    "evidence": {"settlement_verified": True},
                },
            )
    training = tmp_path / "training.jsonl"
    evaluation = tmp_path / "evaluation.jsonl"
    counts = await export_learning_splits(
        store, str(training), str(evaluation), evaluation_ratio=0.2
    )
    assert counts == {"training": 4, "evaluation": 2}
    assert len(training.read_text().splitlines()) == 4
    assert len(evaluation.read_text().splitlines()) == 2


@pytest.mark.asyncio
async def test_candidate_observations_are_stored_but_not_exported(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_candidate_proposals([{
        "kalshi_market_id": "k1", "polymarket_market_id": "p1",
        "kalshi_title": "K", "polymarket_title": "P", "score": 0.5,
        "shared_terms": ["x"], "status": "REVIEW_REQUIRED",
    }])
    assert (await store.learning_counts())["UNLABELED"] == 1
    assert await store.labeled_learning_examples() == []


@pytest.mark.asyncio
async def test_settlement_candidate_queue_persists_lifecycle_without_labeling(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    candidate = {
        "kalshi_market_id": "kalshi:k1",
        "polymarket_market_id": "polymarket_us:p1",
        "lifecycle_status": "OPEN_AWAITING_SETTLEMENT",
        "guarantee_status": "UNKNOWN",
        "pair_status": "REVIEW_REQUIRED",
        "mismatch_codes": ["SETTLEMENT_GUARANTEE_UNKNOWN"],
    }

    await store.save_settlement_candidates([candidate])

    stored = await store.latest_settlement_candidates()
    assert stored[0]["kalshi_market_id"] == candidate["kalshi_market_id"]
    assert stored[0]["ranking_position"] == 1
    assert await store.trusted_learning_counts() == {}
    await store.save_settlement_candidates([])
    assert await store.latest_settlement_candidates() == []


@pytest.mark.asyncio
async def test_milestone_alert_is_emitted_once_per_queue_transition(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    candidate = {
        "kalshi_market_id": "kalshi:k1",
        "polymarket_market_id": "polymarket_us:p1",
        "lifecycle_status": "OPEN_AWAITING_SETTLEMENT",
        "guarantee_status": "GUARANTEED",
        "pair_status": "APPROVED_EQUIVALENT",
        "queue_status": "AWAITING_SETTLEMENT",
        "next_gate": "WAIT_FOR_BOTH_TERMINAL_OUTCOMES",
    }

    await store.save_settlement_candidates([candidate])
    await store.save_settlement_candidates([candidate])
    alerts = await store.latest_milestone_alerts()
    assert len(alerts) == 2
    assert alerts[0]["queue_status"] == "AWAITING_SETTLEMENT"
    assert alerts[1]["queue_status"] == "DETERMINISTIC"
    assert alerts[1]["transition_kind"] == "DETERMINISTIC_RULE_GATE"

    settled = {**candidate, "lifecycle_status": "SETTLED", "queue_status": "SETTLED"}
    await store.save_settlement_candidates([settled])
    assert [alert["queue_status"] for alert in await store.latest_milestone_alerts()] == [
        "SETTLED",
        "AWAITING_SETTLEMENT",
        "DETERMINISTIC",
    ]


@pytest.mark.asyncio
async def test_unverified_label_is_never_exported(tmp_path):
    from atlas.learning import export_training_jsonl

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_learning_example(
        "unsafe", "APPROVED_EQUIVALENT", {"market_a": {}, "market_b": {}}
    )
    output = tmp_path / "training.jsonl"
    assert await export_training_jsonl(store, str(output)) == 0
    assert output.read_text() == ""


@pytest.mark.asyncio
async def test_training_bundle_writes_provenance_and_excludes_observations(tmp_path):
    from atlas.learning import export_training_bundle

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_learning_example(
        "observation", "UNLABELED", {"market_a": {}, "market_b": {}}
    )
    result = await export_training_bundle(store, str(tmp_path / "training"))
    manifest = __import__("json").loads(
        (tmp_path / "training" / "manifest.json").read_text()
    )

    assert result["training"] == 0
    assert result["evaluation"] == 0
    assert manifest["paper_only"] is True
    assert manifest["execution_enabled"] is False
    assert manifest["split_counts"] == {"training": 0, "evaluation": 0}
    assert manifest["learning_loop"]["status"] == "LABEL_MIX_BLOCKED"


@pytest.mark.asyncio
async def test_learning_readiness_blocks_without_balanced_labels(tmp_path):
    from atlas.evaluation import learning_loop_status, learning_readiness

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    result = await learning_readiness(store)
    assert result["ready"] is False
    assert result["reasons"]
    assert result["status"] == "LABEL_MIX_BLOCKED"
    assert result["loop"]["paper_only"] is True
    assert result["loop"]["execution_enabled"] is False
    assert await learning_loop_status(store) == result["loop"]


@pytest.mark.asyncio
async def test_learning_loop_status_is_ready_only_for_balanced_trusted_labels(tmp_path):
    from atlas.evaluation import learning_loop_status

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    evidence = {"settlement_verified": True}
    await store.save_learning_example(
        "approved", "APPROVED_EQUIVALENT", {"evidence": evidence}
    )
    await store.save_learning_example("rejected", "REJECTED", {"evidence": evidence})

    result = await learning_loop_status(store, minimum_labels=2)

    assert result["status"] == "READY"
    assert result["ready"] is True
    assert result["trusted_labels"] == 2
    assert result["label_counts"] == {
        "APPROVED_EQUIVALENT": 1,
        "REJECTED": 1,
    }
    assert result["blockers"] == []
    assert result["paper_only"] is True
    assert result["execution_enabled"] is False


@pytest.mark.asyncio
async def test_learning_loop_status_rejects_invalid_minimum(tmp_path):
    from atlas.evaluation import learning_loop_status

    with pytest.raises(ValueError, match="minimum_labels"):
        await learning_loop_status(
            AtlasStore(str(tmp_path / "atlas.sqlite3")), minimum_labels=0
        )


@pytest.mark.asyncio
async def test_paper_trade_outcome_round_trip(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_paper_trade_outcome("trade-1", "CONFIRMED", "yes", "yes")
    async with __import__("aiosqlite").connect(tmp_path / "atlas.sqlite3") as db:
        row = await (await db.execute("SELECT status, outcome_a, outcome_b FROM paper_trade_outcomes")).fetchone()
    assert row == ("CONFIRMED", "yes", "yes")


@pytest.mark.asyncio
async def test_shadow_observation_round_trip(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    observation = {
        "observation_id": "shadow-1",
        "created_at": "2026-08-10T00:00:00+00:00",
        "rule_status": "BLOCKED",
    }
    await store.save_shadow_observation(observation)
    assert await store.latest_shadow_observation() == observation


@pytest.mark.asyncio
async def test_shadow_validation_summary_aggregates_pairs_and_edges(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    for index, edge in enumerate(("-0.20", "0.10")):
        await store.save_shadow_observation(
            {
                "observation_id": f"shadow-{index}",
                "created_at": f"2026-08-10T00:0{index}:00+00:00",
                "pair_id": f"pair-{index}",
                "blockers": ["RULE_BLOCKER"],
                "best_direction": {
                    "contracts": "10",
                    "gross_cost": str(10 - float(edge)),
                    "raw_edge_if_complementary": edge,
                },
            }
        )
    summary = await store.shadow_validation_summary()
    assert summary["observation_count"] == 2
    assert summary["unique_pairs"] == 2
    assert summary["positive_edge_count"] == 1
    assert summary["positive_edge_rate"] == "0.5"


@pytest.mark.asyncio
async def test_recent_trusted_labels_are_compact_and_newest_first(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_learning_example(
        "observation", "UNLABELED", {"market_a": {}, "market_b": {}}
    )
    rows = [
        (
            "pair-old:REJECTED",
            "REJECTED",
            "2026-08-10T00:00:00+00:00",
            json.dumps(
                {
                    "pair_id": "pair-old",
                    "market_a": {"title": "Kalshi CPI >4.1%", "venue": "kalshi"},
                    "market_b": {"title": "PM inflation <b>", "venue": "polymarket_global"},
                    "evidence": {
                        "settlement_verified": True,
                        "source_kind": "HISTORICAL_BACKFILL",
                        "relationship_status": "DIVERGED",
                        "outcome_a": "no",
                        "outcome_b": "yes",
                    },
                }
            ),
        ),
        (
            "pair-new:APPROVED",
            "APPROVED_EQUIVALENT",
            "2026-08-11T00:00:00+00:00",
            json.dumps(
                {
                    "pair_id": "pair-new",
                    "market_a": {"title": "Kalshi Fed cut", "venue": "kalshi"},
                    "market_b": {"title": "PM Fed cut", "venue": "polymarket_global"},
                    "evidence": {
                        "settlement_verified": True,
                        "source_kind": "HISTORICAL_BACKFILL",
                        "relationship_status": "CONFIRMED",
                        "outcome_a": "no",
                        "outcome_b": "no",
                    },
                }
            ),
        ),
    ]
    async with aiosqlite.connect(tmp_path / "atlas.sqlite3") as db:
        await db.executemany(
            "INSERT OR REPLACE INTO learning_examples VALUES (?, ?, ?, ?)", rows
        )
        await db.commit()

    recent = await store.recent_trusted_labels(limit=5)

    assert [row["pair_id"] for row in recent] == ["pair-new", "pair-old"]
    assert recent[0]["label"] == "APPROVED_EQUIVALENT"
    assert recent[0]["relationship_status"] == "CONFIRMED"
    assert recent[1]["outcome_b"] == "yes"
    assert recent[1]["title_b"] == "PM inflation <b>"
    assert all(row["settlement_verified"] is True for row in recent)
    for row in recent:
        assert "UNLABELED" != row["label"]
        assert "market_a" not in row and "market_b" not in row
        assert "payload" not in row and "payload_json" not in row


@pytest.mark.asyncio
async def test_recent_trusted_labels_bound_the_requested_limit(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_learning_example(
        "one", "REJECTED", {"pair_id": "pair-1", "evidence": {"settlement_verified": True}}
    )
    assert len(await store.recent_trusted_labels(limit=0)) == 1
    assert len(await store.recent_trusted_labels(limit=500)) == 1
    fallback = (await store.recent_trusted_labels(limit=1))[0]
    assert fallback["pair_id"] == "pair-1"
    assert fallback["title_a"] is None


@pytest.mark.asyncio
async def test_recent_historical_backfills_return_newest_first(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    for index in range(3):
        await store.save_historical_backfill(
            {
                "status": "EXTERNAL_EVIDENCE_BLOCKED",
                "completed_at": f"2026-08-1{index}T00:00:00+00:00",
                "new_labels": index,
                "kalshi_series_tickers": ["KXFEDDECISION"],
                "kalshi_series_event_counts": {"KXFEDDECISION": index},
            }
        )
    runs = await store.recent_historical_backfills(limit=2)
    assert [run["new_labels"] for run in runs] == [2, 1]
    assert runs[0]["kalshi_series_event_counts"] == {"KXFEDDECISION": 2}


@pytest.mark.asyncio
async def test_recent_historical_backfills_bound_the_requested_limit(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_historical_backfill({"status": "NO_NEW_TRUSTED_LABELS"})
    assert len(await store.recent_historical_backfills(limit=0)) == 1
    assert len(await store.recent_historical_backfills(limit=500)) == 1


@pytest.mark.asyncio
async def test_all_gap_observations_keeps_the_newest_rows_under_the_cap(tmp_path):
    """A plain `ORDER BY created_at ASC LIMIT n` keeps the OLDEST n and drops
    everything after, which would have silently frozen the watch board on stale
    data once the cap was reached (~25 days out at the observed rate)."""
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    for index in range(5):
        await store.save_gap_observation(
            {
                "observation_id": f"obs-{index}",
                "observed_at": f"2026-08-{10 + index:02d}T00:00:00+00:00",
                "best_gap": f"-0.0{index}",
            }
        )

    loaded = await store.all_gap_observations(limit=3)

    assert [row["observation_id"] for row in loaded] == ["obs-2", "obs-3", "obs-4"]
    # Still ascending: the bankroll meter compounds chronologically.
    assert [row["observed_at"] for row in loaded] == sorted(
        row["observed_at"] for row in loaded
    )
    assert await store.gap_observation_count() == 5


@pytest.mark.asyncio
async def test_gap_subject_aggregates_cover_every_row_and_survive_the_load_cap(tmp_path):
    """Aggregates read promoted columns over the whole table, so the ALL window
    stays true even when all_gap_observations returns only its newest slice."""
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    for index in range(6):
        await store.save_gap_observation(
            {
                "observation_id": f"obs-{index}",
                "observed_at": f"2026-08-{10 + index:02d}T00:00:00+00:00",
                "event_subject": "a|2026-08",
                "best_gap": f"-0.0{index}",
                "executable_gap": index == 0,
            }
        )

    aggregates = await store.gap_subject_aggregates()

    assert aggregates["a|2026-08"]["observations"] == 6
    assert aggregates["a|2026-08"]["executable_observations"] == 1
    assert aggregates["a|2026-08"]["high"] == 0.0
    assert aggregates["a|2026-08"]["low"] == -0.05
    # The capped load only sees the newest rows; the aggregate still sees all six.
    assert len(await store.all_gap_observations(limit=2)) == 2


@pytest.mark.asyncio
async def test_gap_columns_backfill_from_existing_json_payloads(tmp_path):
    """Rows written before the columns existed must become queryable, or the
    aggregate would silently ignore all the history recorded so far."""
    path = tmp_path / "atlas.sqlite3"
    store = AtlasStore(str(path))
    await store.initialize()
    async with aiosqlite.connect(path) as db:
        await db.execute("DROP INDEX IF EXISTS idx_gap_observations_subject")
        await db.execute(
            "INSERT INTO gap_observations (observation_id, created_at, payload_json) VALUES (?, ?, ?)",
            (
                "legacy-1",
                "2026-08-01T00:00:00+00:00",
                json.dumps(
                    {
                        "observation_id": "legacy-1",
                        "event_subject": "legacy|2026-08",
                        "best_gap": "-0.07",
                        "executable_gap": True,
                    }
                ),
            ),
        )
        await db.commit()

    # The legacy row predates the promoted columns; the backfill runs during
    # initialization, so simulate a fresh process by clearing the
    # initialize-once guard for this path before re-initializing.
    AtlasStore._initialized_paths.discard(str(path.resolve()))
    await store.initialize()
    aggregates = await store.gap_subject_aggregates()

    assert aggregates["legacy|2026-08"]["observations"] == 1
    assert aggregates["legacy|2026-08"]["low"] == -0.07
    assert aggregates["legacy|2026-08"]["executable_observations"] == 1


async def _save_validation_case(store, pair_id="case-1", **overrides):
    case = {
        "pair_id": pair_id,
        "source_kind": "APPROVED",
        "decision_status": "APPROVED_EQUIVALENT",
        "guarantee_a": "GUARANTEED",
        "guarantee_b": "GUARANTEED",
        "payload": {"pair": {}},
    }
    case.update(overrides)
    await store.save_validation_case(case)


async def _case_row(store, pair_id="case-1"):
    return next(
        case
        for case in await store.pending_validation_cases(limit=50)
        if case["pair_id"] == pair_id
    )


@pytest.mark.asyncio
async def test_mark_validation_checked_default_leaves_retry_count_untouched(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await _save_validation_case(store)

    await store.mark_validation_checked("case-1", pending_reason="not_terminal")
    await store.mark_validation_checked("case-1", pending_reason="not_terminal")

    case = await _case_row(store)
    assert case["retry_count"] == 0
    assert case["last_retry_at"] is None
    assert case["last_checked_at"] is not None


@pytest.mark.asyncio
async def test_mark_validation_checked_increment_retry_caps_at_max_retries(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await _save_validation_case(store, max_retries=2)

    for _ in range(3):
        await store.mark_validation_checked("case-1", increment_retry=True)

    case = await _case_row(store)
    assert case["retry_count"] == 2
    assert case["last_retry_at"] is not None


@pytest.mark.asyncio
async def test_mark_validation_checked_explicit_zero_resets_retry_count(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await _save_validation_case(store)
    await store.mark_validation_checked("case-1", increment_retry=True)
    assert (await _case_row(store))["retry_count"] == 1

    await store.mark_validation_checked("case-1", retry_count=0)

    assert (await _case_row(store))["retry_count"] == 0


@pytest.mark.asyncio
async def test_evidence_exhausted_status_leaves_pending_pool_and_summary_counts_it(
    tmp_path,
):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await _save_validation_case(store, max_retries=1)

    await store.mark_validation_checked(
        "case-1",
        "EVIDENCE_EXHAUSTED",
        pending_reason="polymarket_us:venue_client_error",
        increment_retry=True,
    )

    assert await store.pending_validation_cases() == []
    summary = await store.validation_summary()
    assert summary["retry_exhausted"] == 1
    assert summary["awaiting_settlement"] == 0


async def _save_lag_outcome(store, pair_id, lag_seconds, left, right, first_venue=None):
    await store.save_validation_outcome(
        {
            "pair_id": pair_id,
            "resolved_at": "2026-12-01T00:00:00+00:00",
            "relationship_status": "CONFIRMED",
            "outcome_a": "yes",
            "outcome_b": "yes",
            "trusted_label": None,
            "kalshi_settled_at": left,
            "polymarket_settled_at": right,
            "settlement_lag_seconds": lag_seconds,
            "first_settled_venue": first_venue,
            "settled_same_day": None if left is None or right is None else left[:10] == right[:10],
        }
    )


@pytest.mark.asyncio
async def test_settlement_lag_stats_aggregate_median_max_and_different_days(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    # Two same-day lags and one multi-day lag, plus a Polymarket-first negative.
    await _save_lag_outcome(
        store, "lag-1", 3600.0,
        "2026-11-04T02:00:00+00:00", "2026-11-04T03:00:00+00:00", "kalshi",
    )
    await _save_lag_outcome(
        store, "lag-2", 7200.0,
        "2026-11-04T02:00:00+00:00", "2026-11-04T04:00:00+00:00", "kalshi",
    )
    await _save_lag_outcome(
        store, "lag-3", 1355400.0,
        "2026-11-04T02:00:00+00:00", "2026-11-19T18:30:00+00:00", "kalshi",
    )
    await _save_lag_outcome(
        store, "lag-4", -86400.0,
        "2026-11-05T02:00:00+00:00", "2026-11-04T02:00:00+00:00", "polymarket_us",
    )

    stats = await store.settlement_lag_stats()

    assert stats["pairs_with_lag"] == 4
    # Signed median of (-86400, 3600, 7200, 1355400).
    assert stats["median_lag_seconds"] == 5400.0
    # Magnitude, so the 15-day Kalshi-first gap wins over the 1-day negative.
    assert stats["max_lag_seconds"] == 1355400.0
    assert stats["different_day_pairs"] == 2
    assert stats["first_settled_venue_counts"] == {"kalshi": 3, "polymarket_us": 1}


@pytest.mark.asyncio
async def test_settlement_lag_stats_skip_outcomes_without_a_computable_lag(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await _save_lag_outcome(store, "no-lag", None, None, "2026-11-04T03:00:00+00:00")
    await _save_lag_outcome(
        store, "has-lag", 60.0,
        "2026-11-04T02:00:00+00:00", "2026-11-04T02:01:00+00:00", "kalshi",
    )

    stats = await store.settlement_lag_stats()

    assert stats["pairs_with_lag"] == 1
    assert stats["median_lag_seconds"] == 60.0
    assert stats["max_lag_seconds"] == 60.0
    assert stats["different_day_pairs"] == 0


@pytest.mark.asyncio
async def test_validation_summary_keeps_existing_keys_and_adds_settlement_lag(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))

    summary = await store.validation_summary()

    pinned = {
        "evidence_versions", "markets_tracked", "rule_changes", "cases",
        "awaiting_settlement", "resolved_cases", "poll_eligible", "retry_exhausted",
        "confirmed", "diverged", "inconclusive", "trusted_labels",
    }
    assert pinned <= set(summary)
    assert summary["settlement_lag"] == {
        "pairs_with_lag": 0,
        "median_lag_seconds": None,
        "max_lag_seconds": None,
        "different_day_pairs": 0,
        "first_settled_venue_counts": {},
    }


@pytest.mark.asyncio
async def test_settlement_lag_stats_read_legacy_outcomes_without_same_day_flag(tmp_path):
    """Outcomes stored before the flag existed still carry both timestamps."""
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_validation_outcome(
        {
            "pair_id": "legacy",
            "resolved_at": "2026-12-01T00:00:00+00:00",
            "relationship_status": "CONFIRMED",
            "outcome_a": "yes",
            "outcome_b": "yes",
            "trusted_label": None,
            "kalshi_settled_at": "2026-11-04T02:00:00+00:00",
            "polymarket_settled_at": "2026-11-19T18:30:00+00:00",
            "settlement_lag_seconds": 1355400.0,
        }
    )

    stats = await store.settlement_lag_stats()

    assert stats["pairs_with_lag"] == 1
    assert stats["different_day_pairs"] == 1


@pytest.mark.asyncio
async def test_due_only_pending_cases_respect_persisted_retry_delay(tmp_path):
    from datetime import UTC, datetime, timedelta

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await _save_validation_case(store)
    now = datetime(2030, 1, 1, tzinfo=UTC)
    await store.mark_validation_checked(
        "case-1",
        pending_reason="not_terminal",
        next_poll_at=(now + timedelta(hours=4)).isoformat(),
        increment_retry=True,
    )

    assert await store.pending_validation_cases(due_only=True, now=now) == []
    assert len(await store.pending_validation_cases(now=now)) == 1
    later = now + timedelta(hours=5)
    assert len(await store.pending_validation_cases(due_only=True, now=later)) == 1


@pytest.mark.asyncio
async def test_initialize_runs_schema_once_per_path_and_per_path_independently(
    tmp_path, monkeypatch
):
    original = aiosqlite.Connection.executescript
    calls: list[str] = []

    def spy(self, sql_script):
        calls.append(sql_script)
        return original(self, sql_script)

    monkeypatch.setattr(aiosqlite.Connection, "executescript", spy)

    path_a = str(tmp_path / "a.sqlite3")
    store_one = AtlasStore(path_a)
    await store_one.initialize()
    await store_one.initialize()
    await AtlasStore(path_a).initialize()
    assert len(calls) == 1

    # A second path is a different database and must initialize on its own.
    await AtlasStore(str(tmp_path / "b.sqlite3")).initialize()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_initialize_creates_hot_path_indexes(tmp_path):
    path = tmp_path / "atlas.sqlite3"
    await AtlasStore(str(path)).initialize()
    async with aiosqlite.connect(path) as db:
        index_names = set()
        for table in ("validation_cases", "market_evidence_snapshots", "orderbook_snapshots"):
            rows = await (await db.execute(f"PRAGMA index_list({table})")).fetchall()
            index_names.update(row[1] for row in rows)
    assert "idx_validation_cases_tracking_poll" in index_names
    assert "idx_market_evidence_market_rules" in index_names
    assert "idx_orderbook_snapshots_timestamp" in index_names


@pytest.mark.asyncio
async def test_prune_deletes_old_orderbook_snapshots_and_keeps_recent(tmp_path):
    from datetime import UTC, datetime, timedelta

    path = tmp_path / "atlas.sqlite3"
    store = AtlasStore(str(path))
    await store.initialize()
    now = datetime.now(UTC)
    async with aiosqlite.connect(path) as db:
        for market_id, age_days in (("m-old", 40), ("m-edge", 31), ("m-recent", 1)):
            await db.execute(
                "INSERT INTO orderbook_snapshots (market_id, venue, timestamp, payload_json)"
                " VALUES (?, ?, ?, ?)",
                (market_id, "kalshi", (now - timedelta(days=age_days)).isoformat(), "{}"),
            )
        await db.commit()

    deleted = await store.prune()

    assert deleted["orderbook_snapshots"] == 2
    async with aiosqlite.connect(path) as db:
        rows = await (await db.execute("SELECT market_id FROM orderbook_snapshots")).fetchall()
    assert [row[0] for row in rows] == ["m-recent"]


@pytest.mark.asyncio
async def test_prune_keeps_only_the_newest_20_report_rows(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    for index in range(25):
        await store.save_catalog_report({"sequence": index})
        await store.save_discovery_scan(
            {
                "kalshi_active": index, "polymarket_active": 0, "comparisons": 0,
                "approved": 0, "review": 0,
            }
        )
        await store.save_agent_run({"sequence": index})

    deleted = await store.prune()

    assert deleted["catalog_reports"] == 5
    assert deleted["discovery_scans"] == 5
    assert deleted["agent_runs"] == 5
    # The newest row — the only one anything reads — survives.
    assert (await store.latest_catalog_report())["sequence"] == 24
    assert (await store.latest_discovery_scan())["kalshi_active"] == 24
    assert (await store.latest_agent_run())["sequence"] == 24
    async with aiosqlite.connect(store.path) as db:
        for table in ("catalog_reports", "discovery_scans", "agent_runs"):
            count = (await (await db.execute(f"SELECT COUNT(*) FROM {table}")).fetchone())[0]
            assert count == 20


@pytest.mark.asyncio
async def test_prune_below_thresholds_deletes_nothing(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    book = fixture_books()["kalshi:KALSHI-FED-SEP26"]
    await store.save_orderbook(book)
    await store.save_catalog_report({"sequence": 0})

    from datetime import UTC, datetime

    deleted = await store.prune(now=datetime.now(UTC))
    assert all(count == 0 for count in deleted.values())
    assert len(await store.latest_orderbooks()) == 1


@pytest.mark.asyncio
async def test_prune_never_touches_the_evidence_or_label_chain(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    old = "2020-01-01T00:00:00+00:00"
    await _save_validation_case(store, pair_id="protected-case")
    await store.save_market_evidence_snapshots(
        [
            {
                "market_id": "kalshi:k1", "venue": "kalshi", "observed_at": old,
                "evidence_hash": "hash-1", "rules_hash": "rules-1",
                "status": "OPEN", "outcome": None, "reason": "captured",
                "payload": {"rules": "text"},
            }
        ]
    )
    await store.save_learning_example(
        "protected-example", "REJECTED", {"evidence": {"settlement_verified": True}}
    )
    await store.save_validation_outcome(
        {
            "pair_id": "protected-case", "resolved_at": old,
            "relationship_status": "CONFIRMED", "outcome_a": "yes", "outcome_b": "yes",
            "trusted_label": None,
        }
    )
    await store.save_paper_trade_outcome("protected-trade", "CONFIRMED", "yes", "yes")

    deleted = await store.prune()

    protected = {
        "market_evidence_snapshots", "validation_cases", "validation_outcomes",
        "learning_examples", "paper_trades", "paper_trade_outcomes", "opportunities",
    }
    assert protected.isdisjoint(deleted)
    summary = await store.validation_summary()
    assert summary["cases"] == 1
    assert summary["evidence_versions"] == 1
    assert summary["confirmed"] == 1
    assert (await store.learning_counts())["REJECTED"] == 1
    async with aiosqlite.connect(store.path) as db:
        row = await (await db.execute("SELECT COUNT(*) FROM paper_trade_outcomes")).fetchone()
    assert row[0] == 1
