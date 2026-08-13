from fastapi.testclient import TestClient

from apps.api.main import _backfill_run_summary, app


def test_health_is_paper_only():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["trading_enabled"] is False


def test_dashboard_and_overview_are_available():
    client = TestClient(app)
    assert client.get("/").status_code == 200
    overview = client.get("/api/overview")
    assert overview.status_code == 200
    assert overview.json()["paper_only"] is True
    assert "evidence" in overview.json()
    assert overview.json()["evidence"]["mode"] == "LIVE_DISCOVERY_FIXTURE_EXECUTION"
    assert "shadow_observation" in overview.json()
    assert "training_readiness" in overview.json()
    assert "validation" in overview.json()
    assert "trusted_labels" in overview.json()["validation"]


def test_overview_exposes_compact_recent_trusted_labels():
    overview = TestClient(app).get("/api/overview")
    assert overview.status_code == 200
    recent = overview.json()["trusted_labels_recent"]
    assert isinstance(recent, list)
    assert len(recent) <= 20
    for row in recent:
        assert row["label"] != "UNLABELED"
        assert {"pair_id", "label", "created_at"} <= set(row)
        # compact summaries only — never the stored payload or raw market blobs
        for oversized in ("market_a", "market_b", "payload", "payload_json", "raw_market_json"):
            assert oversized not in row


def test_overview_exposes_recent_historical_backfill_runs():
    overview = TestClient(app).get("/api/overview")
    assert overview.status_code == 200
    runs = overview.json()["historical_backfill_runs"]
    assert isinstance(runs, list)
    for run in runs:
        assert "status" in run
        assert isinstance(run["kalshi_series_tickers"], list)
        assert isinstance(run["kalshi_series_event_counts"], dict)
        assert isinstance(run["tag_scopes"], list)


def test_backfill_run_summary_extracts_series_counts_and_tag_scopes():
    summary = _backfill_run_summary(
        {
            "status": "MILESTONE_IN_PROGRESS",
            "completed_at": "2026-08-12T00:00:00+00:00",
            "new_labels": 2,
            "resolved_pairs": 3,
            "inconclusive_pairs": 1,
            "kalshi_series_tickers": ["KXFEDDECISION", "KXFED"],
            "kalshi_series_event_counts": {"KXFEDDECISION": 14, "KXFED": 5},
            "venue_coverage": {
                "polymarket_us": {"catalog_scope": "all"},
                "polymarket_global": {"catalog_scope": "tagged:101701"},
            },
            "training_artifacts": {"should": "not leak into the summary"},
        }
    )
    assert summary["status"] == "MILESTONE_IN_PROGRESS"
    assert summary["kalshi_series_tickers"] == ["KXFEDDECISION", "KXFED"]
    assert summary["kalshi_series_event_counts"] == {"KXFEDDECISION": 14, "KXFED": 5}
    assert summary["tag_scopes"] == ["tagged:101701"]
    assert "training_artifacts" not in summary


def test_backfill_run_summary_tolerates_missing_fields():
    summary = _backfill_run_summary({"status": "EXTERNAL_EVIDENCE_BLOCKED"})
    assert summary["kalshi_series_tickers"] == []
    assert summary["kalshi_series_event_counts"] == {}
    assert summary["tag_scopes"] == []
