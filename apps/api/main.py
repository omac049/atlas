from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from atlas import __version__
from atlas.frontier import approval_frontier
from atlas.gap_radar import paper_bankroll_summary
from atlas.watchlist import build_watchlist

app = FastAPI(title="Atlas", version=__version__)
dashboard_path = Path(__file__).resolve().parents[2] / "apps" / "dashboard" / "index.html"
dashboard_dir = dashboard_path.parent


@app.get("/health")
def health() -> dict[str, str | bool]:
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


@app.get("/api/overview")
async def overview() -> dict:
    from atlas.agent import AtlasAgent
    from atlas.arbitrage import calculate_opportunity
    from atlas.evaluation import learning_readiness
    from atlas.simulation import run_fixture_research
    from atlas.storage import AtlasStore
    from atlas.venues.fixtures import fixture_books, fixture_markets
    from atlas.verification import verify_equivalence

    markets, books = fixture_markets(), fixture_books()
    stored_books = await AtlasStore().latest_orderbooks(limit=2)
    store = AtlasStore()
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
    historical_backfill = await store.latest_historical_backfill()
    gap_observations = await store.all_gap_observations()
    recent_gaps = await store.recent_gap_observations(6)
    recent_backfills = await store.recent_historical_backfills(limit=6)
    training_readiness = await learning_readiness(store)
    pair = verify_equivalence(
        markets["kalshi"][0], markets["polymarket_us"][0], "fixture-fed-sep26"
    )
    opportunity = calculate_opportunity(
        pair,
        books["kalshi:KALSHI-FED-SEP26"],
        books["polymarket_us:PM-FED-SEP26"],
        Decimal(100),
        fees=Decimal("0.83"),
        slippage=Decimal("0.20"),
    )
    research = run_fixture_research(
        pair,
        {"a": books["kalshi:KALSHI-FED-SEP26"], "b": books["polymarket_us:PM-FED-SEP26"]},
    )
    agent_payload = await store.latest_agent_run()
    if agent_payload is None:
        agent_run = await AtlasAgent(
            {"kalshi": markets["kalshi"], "polymarket_us": markets["polymarket_us"]},
            books=books,
        ).run()
        agent_payload = agent_run.model_dump()
        await store.save_agent_run(agent_payload)
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
        "watchlist": build_watchlist(gap_observations),
        "gap_radar": {
            "summary": paper_bankroll_summary(gap_observations),
            "recent": [
                {
                    "observed_at": observation.get("observed_at"),
                    "event_subject": observation.get("event_subject"),
                    "shape": observation.get("shape"),
                    "best_gap": observation.get("best_gap"),
                    "executable_gap": observation.get("executable_gap"),
                    "verification_status": observation.get("verification_status"),
                }
                for observation in recent_gaps
            ],
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


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(dashboard_path, headers=_NO_STORE)


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


@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(dashboard_dir / "favicon.svg")
