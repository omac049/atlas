import functools
import json
import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

from atlas import __version__
from atlas.frontier import approval_frontier
from atlas.gap_radar import paper_bankroll_summary
from atlas.watchlist import build_watchlist

app = FastAPI(title="Atlas", version=__version__)
dashboard_path = Path(__file__).resolve().parents[2] / "apps" / "dashboard" / "index.html"
dashboard_dir = dashboard_path.parent
# CWD-relative like AtlasStore's default "data/atlas.sqlite3": the launchd
# services and CLI both run from the repo root.
study_dir = Path("data") / "study"


@app.get("/health")
def health() -> dict[str, str | bool]:
    # trading_enabled is intentionally hardcoded, never settings-driven: no config can flip it.
    return {"service": "atlas", "version": __version__, "trading_enabled": False, "status": "ok"}


def _backfill_run_summary(run: dict) -> dict:
    """Compact one persisted historical-backfill run for the overview payload."""
    venue_coverage = run.get("venue_coverage") or {}
    tag_scopes = sorted(
        {
            str(coverage.get("catalog_scope"))
            for coverage in venue_coverage.values()
            if isinstance(coverage, dict)
            and str(coverage.get("catalog_scope", "")).startswith("tagged:")
        }
    )
    return {
        "status": run.get("status"),
        "completed_at": run.get("completed_at"),
        "new_labels": run.get("new_labels"),
        "resolved_pairs": run.get("resolved_pairs"),
        "inconclusive_pairs": run.get("inconclusive_pairs"),
        "kalshi_series_tickers": run.get("kalshi_series_tickers") or [],
        "kalshi_series_event_counts": run.get("kalshi_series_event_counts") or {},
        "tag_scopes": tag_scopes,
    }


@functools.lru_cache(maxsize=1)
def _fixture_demo() -> tuple:
    """Deterministic fixture demo (markets/books/pair/opportunity/research), once per process."""
    from atlas.arbitrage import calculate_opportunity
    from atlas.fees import DEMO_BASKET_FEES, DEMO_BASKET_SLIPPAGE
    from atlas.simulation import run_fixture_research
    from atlas.venues.fixtures import fixture_books, fixture_markets
    from atlas.verification import verify_equivalence

    markets, books = fixture_markets(), fixture_books()
    pair = verify_equivalence(
        markets["kalshi"][0], markets["polymarket_us"][0], "fixture-fed-sep26"
    )
    opportunity = calculate_opportunity(
        pair,
        books["kalshi:KALSHI-FED-SEP26"],
        books["polymarket_us:PM-FED-SEP26"],
        Decimal(100),
        fees=DEMO_BASKET_FEES,
        slippage=DEMO_BASKET_SLIPPAGE,
    )
    research = run_fixture_research(
        pair,
        {"a": books["kalshi:KALSHI-FED-SEP26"], "b": books["polymarket_us:PM-FED-SEP26"]},
    )
    return markets, books, pair, opportunity, research


# The dashboard polls every 15s, and the gap-observation trio JSON-parses tens of
# thousands of stored rows per read — cache it briefly instead of per request.
_GAP_SNAPSHOT_TTL_SECONDS = 30.0
_gap_snapshot: tuple[float, tuple] | None = None


async def _gap_observation_snapshot(store) -> tuple:
    global _gap_snapshot
    now = time.monotonic()
    if _gap_snapshot is not None and now < _gap_snapshot[0]:
        return _gap_snapshot[1]
    value = (
        await store.all_gap_observations(limit=50000),
        await store.gap_observation_count(),
        await store.gap_subject_aggregates(),
    )
    _gap_snapshot = (now + _GAP_SNAPSHOT_TTL_SECONDS, value)
    return value


@app.get("/api/study")
def study() -> dict:
    """Newest persisted 90-day-study report, verbatim.

    Reports are written weekly by `atlas gaps study` (atlas/cli.py); this route
    only reads what already exists on disk and NEVER recomputes the study —
    regenerating in-request would walk tens of thousands of observations and
    break the "regenerable bit-for-bit, tamper-evident trail" property.
    """
    no_report = {"paper_only": True, "status": "NO_REPORT"}
    try:
        reports = sorted(study_dir.glob("study-report-*.json"))
    except OSError:
        return no_report
    if not reports:
        return no_report
    newest = reports[-1]
    try:
        report = json.loads(newest.read_text())
        generated_at = datetime.fromtimestamp(newest.stat().st_mtime, tz=UTC).isoformat()
    except (OSError, ValueError):
        return no_report
    return {
        "paper_only": True,
        "source": newest.name,
        "generated_at": generated_at,
        "report": report,
    }


def _recent_gap_row(observation: dict) -> dict:
    """Compact one gap observation for the overview feed, fees included."""
    row = {
        "observed_at": observation.get("observed_at"),
        "event_subject": observation.get("event_subject"),
        "shape": observation.get("shape"),
        "best_gap": observation.get("best_gap"),
        "executable_gap": observation.get("executable_gap"),
        "verification_status": observation.get("verification_status"),
    }
    # The per-venue fees live on the winning basket (gap_radar._baskets); older
    # rows predate the venue-published fee model, so the keys are optional.
    best = next(
        (
            basket
            for basket in observation.get("baskets") or []
            if basket.get("legs") == observation.get("best_basket")
        ),
        {},
    )
    for key in ("kalshi_fee", "polymarket_fee", "polymarket_fee_basis", "kalshi_size"):
        if key in best:
            row[key] = best[key]
    return row


@app.get("/api/overview")
async def overview() -> dict:
    from atlas.agent import AtlasAgent
    from atlas.evaluation import learning_readiness
    from atlas.live_monitor import REQUIRED_STREAM_CREDENTIALS
    from atlas.storage import AtlasStore

    # Presence check only — the values themselves are never read into the
    # payload (hard invariant: credentials are never logged or served).
    missing_credentials = [
        name for name in REQUIRED_STREAM_CREDENTIALS if not os.getenv(name)
    ]

    markets, books, pair, opportunity, research = _fixture_demo()
    store = AtlasStore()
    stored_books = await store.latest_orderbooks(limit=2)
    stored_opportunity = await store.latest_opportunity()
    stored_pair = await store.latest_pair()
    paper_trades = await store.paper_trade_summary()
    latest_scan = await store.latest_discovery_scan()
    candidates = await store.latest_candidate_proposals()
    learning = await store.learning_counts()
    trusted_labels_recent = await store.recent_trusted_labels(limit=8)
    catalog = await store.latest_catalog_report()
    settlement_candidates = await store.latest_settlement_candidates()
    frontier = await approval_frontier(store)
    milestone_alerts = await store.latest_milestone_alerts()
    shadow = await store.latest_shadow_observation()
    shadow_validation = await store.shadow_validation_summary()
    validation = await store.validation_summary()
    pending_validation_cases = await store.pending_validation_cases(limit=20)
    validation["pending_cases"] = [
        {
            key: case[key]
            for key in (
                "pair_id",
                "source_kind",
                "decision_status",
                "tracking_status",
                "pending_reason",
                "next_poll_at",
                "retry_count",
                "max_retries",
                "last_retry_at",
                "last_checked_at",
                "poll_eligible",
            )
        }
        for case in pending_validation_cases
    ]
    historical_backfill = await store.latest_historical_backfill()
    gap_observations, gap_observation_total, gap_subject_aggregates = (
        await _gap_observation_snapshot(store)
    )
    recent_gaps = await store.recent_gap_observations(6)
    recent_backfills = await store.recent_historical_backfills(limit=6)
    training_readiness = await learning_readiness(store)
    agent_payload = await store.latest_agent_run()
    if agent_payload is None:
        # In-memory fallback only: a GET must never write, so nothing is persisted.
        agent_run = await AtlasAgent(
            {"kalshi": markets["kalshi"], "polymarket_us": markets["polymarket_us"]},
            books=books,
        ).run()
        agent_payload = agent_run.model_dump()
    active_pair = stored_pair or pair
    return {
        "paper_only": True,
        "evidence": {
            "mode": "LIVE_DISCOVERY_FIXTURE_EXECUTION",
            "live_pair_count": latest_scan.get("approved", 0) if latest_scan else 0,
            "demo_opportunity": not (latest_scan and latest_scan.get("approved", 0) > 0),
        },
        "last_updated": (
            stored_books[0].timestamp
            if stored_books
            else books["kalshi:KALSHI-FED-SEP26"].timestamp
        ).isoformat(),
        "venues": {
            "kalshi": "stored" if stored_books else "fixture",
            "polymarket_us": "stored" if stored_books else "fixture",
        },
        "opportunity": (stored_opportunity or opportunity).model_dump(mode="json")
        if (stored_opportunity or opportunity)
        else None,
        "pair": {
            "id": active_pair.pair_id,
            "status": active_pair.status.value,
            "confidence": str(active_pair.match_confidence),
            "differences": active_pair.differences,
            "approved_by": active_pair.approved_by,
            "source": "registry" if stored_pair else "fixture",
            "decision": active_pair.decision.model_dump(mode="json") if active_pair.decision else None,
        },
        "paper_trades": paper_trades,
        "discovery_scan": latest_scan,
        "candidate_proposals": candidates,
        "learning": learning,
        "trusted_labels_recent": trusted_labels_recent,
        "training_readiness": training_readiness,
        "catalog_compatibility": catalog,
        "settlement_candidates": settlement_candidates,
        "approval_frontier": frontier,
        "milestone_alerts": milestone_alerts,
        "shadow_observation": shadow,
        "shadow_validation": shadow_validation,
        "validation": validation,
        "historical_backfill": historical_backfill,
        "historical_backfill_runs": [_backfill_run_summary(run) for run in recent_backfills],
        # Same observation list the bankroll summary already consumes — no extra I/O.
        # The recorded count comes along so a capped load reports itself instead of
        # presenting a partial window as the full history.
        "watchlist": build_watchlist(
            gap_observations,
            total_observations=gap_observation_total,
            subject_aggregates=gap_subject_aggregates,
        ),
        "gap_radar": {
            "summary": paper_bankroll_summary(gap_observations),
            "recent": [_recent_gap_row(observation) for observation in recent_gaps],
        },
        "live_stream_credentials": {
            "missing": missing_credentials,
            "complete": not missing_credentials,
        },
        "research": {
            "detected_opportunities": research.detected_opportunities,
            "executable_opportunities": research.executable_opportunities,
            "phantom_rate": str(research.phantom_rate),
            "net_pnl": str(research.net_pnl),
            "failed_hedge_losses": str(research.failed_hedge_losses),
            "median_lifetime_ms": research.median_lifetime_ms,
        },
        "agent": agent_payload,
    }


# The dashboard shell carries the asset version query strings, so a cached copy
# pins the browser to old CSS/JS and the page silently stops matching the code.
# Assets stay cacheable; only the shell must always be revalidated.
_NO_STORE = {"Cache-Control": "no-store, must-revalidate"}

_DASHBOARD_ASSETS = (
    "dashboard.css",
    "dashboard-research.css",
    "dashboard-agent.css",
    "dashboard.js",
    "dashboard-panes.js",
    "favicon.svg",
)


def _asset_version() -> str:
    """Cache-busting token: newest mtime across the dashboard assets.

    Computed per request — six stat calls against a 15s poll cycle is noise,
    and it means an edited asset takes effect on the next shell load with no
    process restart or manual version bump.
    """
    stamps = [
        stat.st_mtime
        for name in _DASHBOARD_ASSETS
        if (stat := _safe_stat(dashboard_dir / name)) is not None
    ]
    return str(int(max(stamps))) if stamps else "0"


def _safe_stat(path: Path):
    try:
        return path.stat()
    except OSError:
        return None


@app.get("/", include_in_schema=False)
def dashboard() -> HTMLResponse:
    # `__ATLAS_ASSETS__` in index.html becomes the asset version token; when
    # the markup still carries literal versions the replace is a no-op.
    html = dashboard_path.read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__ATLAS_ASSETS__", _asset_version()), headers=_NO_STORE)


@app.get("/dashboard.css", include_in_schema=False)
def dashboard_css() -> FileResponse:
    return FileResponse(dashboard_dir / "dashboard.css")


@app.get("/dashboard-research.css", include_in_schema=False)
def dashboard_research_css() -> FileResponse:
    return FileResponse(dashboard_dir / "dashboard-research.css")


@app.get("/dashboard-agent.css", include_in_schema=False)
def dashboard_agent_css() -> FileResponse:
    return FileResponse(dashboard_dir / "dashboard-agent.css")


@app.get("/dashboard.js", include_in_schema=False)
def dashboard_js() -> FileResponse:
    return FileResponse(dashboard_dir / "dashboard.js")


@app.get("/dashboard-panes.js", include_in_schema=False)
def dashboard_panes_js() -> FileResponse:
    return FileResponse(dashboard_dir / "dashboard-panes.js")


@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(dashboard_dir / "favicon.svg")
