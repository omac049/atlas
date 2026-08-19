import asyncio
import json

from fastapi.testclient import TestClient

import apps.api.main
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


def test_overview_get_never_persists_an_agent_run(tmp_path, monkeypatch):
    """GET /api/overview must be idempotent: with no stored agent run it computes
    one in-memory for the payload but never writes an agent_runs row."""
    from atlas import storage

    db_path = str(tmp_path / "overview.sqlite3")
    real_store = storage.AtlasStore

    class TempStore(real_store):
        def __init__(self, path: str = db_path):
            super().__init__(path)

    monkeypatch.setattr(storage, "AtlasStore", TempStore)
    # Fresh gap snapshot so the temp store is actually read, and the cached
    # empty snapshot does not leak into later tests.
    monkeypatch.setattr(apps.api.main, "_gap_snapshot", None)

    client = TestClient(app)
    for _ in range(2):
        overview = client.get("/api/overview")
        assert overview.status_code == 200
        payload = overview.json()
        assert payload["paper_only"] is True
        assert payload["agent"] is not None
        assert "watchlist" in payload
        assert "gap_radar" in payload
    assert asyncio.run(TempStore().latest_agent_run()) is None


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


def test_study_reports_no_report_when_directory_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(apps.api.main, "study_dir", tmp_path / "study")
    response = TestClient(app).get("/api/study")
    assert response.status_code == 200
    assert response.json() == {"paper_only": True, "status": "NO_REPORT"}


def test_study_serves_the_newest_report_verbatim(tmp_path, monkeypatch):
    monkeypatch.setattr(apps.api.main, "study_dir", tmp_path)
    older = {"paper_only": True, "study_day": 1, "meets_go_threshold": False}
    newest = {
        "paper_only": True,
        "study_day": 8,
        "phase": 1,
        "verified_opportunities_per_30_days": "63.8",
        "go_threshold_per_30_days": 10,
        "meets_go_threshold": True,
        "weekly": [],
    }
    (tmp_path / "study-report-20260812.json").write_text(json.dumps(older))
    (tmp_path / "study-report-20260819.json").write_text(json.dumps(newest))
    response = TestClient(app).get("/api/study")
    assert response.status_code == 200
    payload = response.json()
    assert payload["paper_only"] is True
    assert payload["source"] == "study-report-20260819.json"
    assert payload["generated_at"]
    assert payload["report"] == newest


def test_study_treats_an_unparseable_report_as_no_report(tmp_path, monkeypatch):
    monkeypatch.setattr(apps.api.main, "study_dir", tmp_path)
    (tmp_path / "study-report-20260819.json").write_text("{not json")
    response = TestClient(app).get("/api/study")
    assert response.status_code == 200
    assert response.json() == {"paper_only": True, "status": "NO_REPORT"}


def test_overview_reports_missing_stream_credentials_by_name_only(monkeypatch):
    from atlas.live_monitor import REQUIRED_STREAM_CREDENTIALS

    for name in REQUIRED_STREAM_CREDENTIALS:
        monkeypatch.delenv(name, raising=False)
    response = TestClient(app).get("/api/overview")
    assert response.status_code == 200
    credentials = response.json()["live_stream_credentials"]
    assert credentials["complete"] is False
    assert credentials["missing"] == list(REQUIRED_STREAM_CREDENTIALS)


def test_overview_never_leaks_credential_values(monkeypatch):
    from atlas.live_monitor import REQUIRED_STREAM_CREDENTIALS

    sentinel = "SECRET-VALUE-MUST-NEVER-APPEAR"
    for name in REQUIRED_STREAM_CREDENTIALS:
        monkeypatch.setenv(name, sentinel)
    response = TestClient(app).get("/api/overview")
    assert response.status_code == 200
    credentials = response.json()["live_stream_credentials"]
    assert credentials == {"missing": [], "complete": True}
    assert sentinel not in response.text


def test_recent_gap_rows_carry_per_venue_fees(tmp_path, monkeypatch):
    from atlas import storage

    db_path = str(tmp_path / "gaps.sqlite3")
    real_store = storage.AtlasStore

    class TempStore(real_store):
        def __init__(self, path: str = db_path):
            super().__init__(path)

    monkeypatch.setattr(storage, "AtlasStore", TempStore)
    monkeypatch.setattr(apps.api.main, "_gap_snapshot", None)
    observation = {
        "observation_id": "obs-fee-1",
        "observed_at": "2026-08-19T12:00:00+00:00",
        "paper_only": True,
        "event_subject": "us_fomc_rate_decision|2026-09",
        "shape": "equivalent_shape",
        "kalshi_market_id": "kalshi:K-FEE",
        "polymarket_market_id": "polymarket_us:P-FEE",
        "verification_status": "REVIEW_REQUIRED",
        "best_gap": "0.03",
        "best_basket": "kalshi_yes+polymarket_no",
        "executable_gap": True,
        "baskets": [
            {
                "legs": "kalshi_yes+polymarket_no",
                "cost": "0.94",
                "kalshi_fee": "0.02",
                "polymarket_fee": "0.0125",
                "polymarket_fee_basis": "venue_published_schedule",
                "gap": "0.03",
                "kalshi_size": "33",
            }
        ],
    }
    asyncio.run(TempStore().save_gap_observation(observation))
    response = TestClient(app).get("/api/overview")
    assert response.status_code == 200
    recent = response.json()["gap_radar"]["recent"]
    row = next(r for r in recent if r["event_subject"] == "us_fomc_rate_decision|2026-09")
    assert row["kalshi_fee"] == "0.02"
    assert row["polymarket_fee"] == "0.0125"
    assert row["polymarket_fee_basis"] == "venue_published_schedule"
    assert row["kalshi_size"] == "33"


def test_dashboard_panes_js_is_served():
    response = TestClient(app).get("/dashboard-panes.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "atlas:overview" in response.text


def test_dashboard_shell_substitutes_the_asset_version_token():
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert "__ATLAS_ASSETS__" not in response.text
    assert response.headers["cache-control"] == "no-store, must-revalidate"
