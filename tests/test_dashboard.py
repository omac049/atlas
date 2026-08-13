from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_has_historical_provenance_slot():
    html = (ROOT / "apps" / "dashboard" / "index.html").read_text()

    assert 'id="validation-provenance"' in html
    assert 'id="validation-safety"' in html
    assert 'id="milestone-alert"' in html
    assert 'id="settlement-evidence-complete"' in html
    assert 'id="shared-rule-audit"' in html
    for element_id in (
        "validation-coverage",
        "validation-precision",
        "validation-inconclusive",
        "validation-readiness",
        "validation-provenance-state",
    ):
        assert f'id="{element_id}"' in html


def test_dashboard_surfaces_existing_global_scope_and_scan_bounds():
    script = (ROOT / "apps" / "dashboard" / "dashboard.js").read_text()

    for field in (
        "catalog_scope",
        "historical_candidate_events_found",
        "market_pairs_reviewed",
        "max_market_pairs",
        "max_resolved_pairs",
    ):
        assert field in script
    assert "GLOBAL TAG SCOPE" in script
    assert "SCAN BOUNDS" in script
    for field in ("confirmed", "diverged", "inconclusive", "training.status", "completed_at"):
        assert field in script
    assert "const rate" in script
    assert "training.loop?.execution_enabled" in script
    assert "milestone_alerts" in script
    assert "kalshi_resolution_time" in script
    assert "polymarket_resolution_time" in script
    assert "settlementTime" in script
    assert "settlement_ready_at" in script
    assert "READY BY" in script
    assert "kalshi_evidence" in script
    assert "polymarket_evidence" in script
    assert "EVIDENCE BLOCKED" in script
    assert "EVIDENCE NOT REFRESHED" in script
    assert "evidence_complete_shared_events" in script
    assert "shared_rule_enrichment" in script
    assert "shared_events_skipped_non_guaranteed" in script


def test_dashboard_styles_provenance_as_research_metadata():
    styles = (ROOT / "apps" / "dashboard" / "dashboard-research.css").read_text()

    assert ".validation-provenance" in styles
    assert ".validation-signal" in styles
    assert ".validation-safety" in styles
    assert ".validation-alert" in styles
    assert ".settlement-time" in styles
    assert ".settlement-evidence" in styles


def test_dashboard_surfaces_kalshi_series_scan_and_recent_backfill_runs():
    html = (ROOT / "apps" / "dashboard" / "index.html").read_text()
    script = (ROOT / "apps" / "dashboard" / "dashboard.js").read_text()

    assert 'id="backfill-series"' in html
    assert 'id="backfill-runs"' in html
    for field in (
        "kalshi_series_tickers",
        "kalshi_series_event_counts",
        "historical_backfill_runs",
        "tag_scopes",
    ):
        assert field in script
    assert "KALSHI SERIES SCAN" in script
    assert "RECENT BACKFILL RUNS" in script


def test_dashboard_renders_guarantee_reason_codes_beside_guarantee_pills():
    script = (ROOT / "apps" / "dashboard" / "dashboard.js").read_text()
    styles = (ROOT / "apps" / "dashboard" / "dashboard-research.css").read_text()

    assert "item.kalshi_guarantee?.reason_codes" in script
    assert "item.polymarket_guarantee?.reason_codes" in script
    assert "settlement-guarantee-reason" in script
    assert ".settlement-guarantee-reason" in styles


def test_dashboard_surfaces_certified_twins_and_alert_feed():
    html = (ROOT / "apps" / "dashboard" / "index.html").read_text()
    script = (ROOT / "apps" / "dashboard" / "dashboard.js").read_text()
    styles = (ROOT / "apps" / "dashboard" / "dashboard-research.css").read_text()

    for element_id in (
        "certified-twins",
        "certified-status",
        "certified-detail",
        "certified-count",
        "alert-feed",
        "alert-feed-status",
    ):
        assert f'id="{element_id}"' in html
    assert "Certified twins" in html
    assert "Alert feed" in html
    for field in ("trusted_labels_recent", "relationship_status", "source_kind"):
        assert field in script
    assert "EXECUTABLE GAP" in script
    assert "DETERMINISTIC RULE GATE CLEARED" in script
    for selector in (".certified-row", ".certified-twins", ".alert-feed", ".alert-item"):
        assert selector in styles


def test_dashboard_keeps_paper_only_safety_messaging_visible():
    html = (ROOT / "apps" / "dashboard" / "index.html").read_text()

    assert "LIVE ORDERS DISABLED" in html
    assert "LIVE SHADOW TEST / NEVER EXECUTED" in html
    assert "no execution module installed" in html
