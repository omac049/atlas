import json

import aiosqlite
import pytest

from atlas.storage import AtlasStore
from atlas.venues.fixtures import fixture_books, fixture_markets


@pytest.mark.asyncio
async def test_store_persists_market_and_orderbook(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    market = fixture_markets()["kalshi"][0]
    book = fixture_books()["kalshi:KALSHI-FED-SEP26"]
    await store.save_markets([market])
    await store.save_orderbook(book)
    assert (tmp_path / "atlas.sqlite3").exists()


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
