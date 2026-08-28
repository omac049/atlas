import argparse
import asyncio
import functools
import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
from dotenv import load_dotenv

from atlas.agent import AtlasAgent
from atlas.arbitrage import calculate_opportunity
from atlas.backfill import (
    SharedBackfillCatalog,
    backfill_historical_validation,
    prefetch_shared_backfill_catalog,
)
from atlas.discovery import (
    compatibility_report,
    filter_live_markets,
    markets_requiring_family_source_enrichment,
    markets_requiring_source_enrichment,
    propose_market_pairs,
    review_market_pairs,
    scan_market_pairs,
    structured_identity_candidates,
)
from atlas.enrichment import enrich_shared_rules, enrich_weather_rules
from atlas.fees import DEMO_BASKET_FEES, DEMO_BASKET_SLIPPAGE
from atlas.frontier import approval_frontier, capture_frontier_rules_evidence
from atlas.learning import export_learning_splits, export_training_jsonl
from atlas.live_monitor import LiveStreamCredentialsMissing, run_pair
from atlas.models import Market, VenueName
from atlas.monitor import run_once
from atlas.paper import PaperExecutor
from atlas.registry import approve_pair
from atlas.replay import read_market_bundle, replay_opportunities, replay_scan, write_market_bundle
from atlas.shadow import find_shadow_pairs, observe_shadow_pair
from atlas.storage import AtlasStore
from atlas.validation import capture_validation_universe, reconcile_validation_cases
from atlas.venues.fixtures import fixture_books, fixture_markets
from atlas.venues.kalshi import KalshiVenue
from atlas.venues.polymarket_global import PolymarketGlobalHistoricalVenue
from atlas.venues.polymarket_us import PolymarketUSVenue
from atlas.verification import verify_equivalence

TARGETED_GLOBAL_TAG_IDS = (
    "2",  # Politics
    "21",  # Crypto
    "84",  # Weather
    "120",  # Finance
    "144",  # Elections
    "309",  # Oil
    "487",  # House of Representatives
    "100196",  # Fed Rates
    "101031",  # Commodities
    "101701",  # CPI
    "103840",  # Midterm
)
MAX_GLOBAL_TAG_IDS = 20
# Default scheduled-batch tags target families with a coded GUARANTEED settlement path
# (chamber-control tiebreak, CPI release policy in atlas/settlement.py). Live probe
# 2026-08-11 (100 most-recent closed markets per tag): Elections 144 = 71% election
# family, Fed Rates 100196 = 15% economic, House 487 = chamber-control scope; the prior
# defaults Crypto 21 / Weather 84 / Commodities 101031 sampled 0% of those families.
# CPI 101701 added 2026-08-12 (verified live: 80 of the 100 most-recent closed markets
# are CPI-family, including the settled July 2026 annual-inflation buckets).
BATCH_DEFAULT_GLOBAL_TAG_IDS = ("144", "487", "100196", "101701")
MAX_KALSHI_SERIES_TICKERS = 10
_KALSHI_SERIES_TICKER_PATTERN = re.compile(r"[A-Z][A-Z0-9]{1,39}")
# Explicit-harvest tool only: settled Polymarket US macro events (e.g. resolved
# CPI ladders) sit under ~400k closed rows, unreachable by the recent-id sweep.
# Operators pass known event slugs; this is never part of scheduled defaults.
MAX_POLYMARKET_US_EVENT_SLUGS = 10
_POLYMARKET_US_EVENT_SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{1,119}")
# Kalshi's recent-first settled-event paging never reaches low-frequency macro
# events (the July 2026 FOMC decision sits below thousands of daily sports and
# hourly settlements), so scheduled batches also scan these series directly.
# KXFEDDECISION = per-meeting rate-change buckets; KXFED = rate upper-bound levels;
# KXCPIYOY = monthly CPI YoY strikes (verified live: KXCPIYOY-26JUL settled).
# KXPAYROLLS / KXPCECORE / KXGDP added 2026-08-14 with their canonical families
# (verified against captured settled July/June/Q2 events before Kalshi's ~6-week
# settled-detail pruning window).
BATCH_DEFAULT_KALSHI_SERIES_TICKERS = (
    "KXFEDDECISION",
    "KXFED",
    "KXCPIYOY",
    "KXCPI",
    "KXCPICORE",
    "KXPAYROLLS",
    "KXPCECORE",
    "KXGDP",
)
# 2026 chamber-control markets (events CONTROLH-2026/-2028, CONTROLS-2026/-2028)
# sit ~36k deep in Kalshi's recent-first open catalog — far beyond the 20-page
# list_markets budget — so the election-discovery panel read Kalshi as 0 until
# they were pulled via the bounded series scan like the macro series above.
# Series tickers verified against the live catalog 2026-08-19.
DISCOVERY_ELECTION_KALSHI_SERIES_TICKERS = (
    "CONTROLH",
    "CONTROLS",
)
BATCH_MAX_TARGET_LABELS = 50
BATCH_MAX_KALSHI_EVENT_PAGES = 100
BATCH_MAX_POLYMARKET_PAGES = 20
BATCH_MAX_GLOBAL_PAGES = 2
BATCH_MAX_CANDIDATE_EVENTS = 50
BATCH_MAX_MARKET_PAIRS = 500
BATCH_MAX_RESOLVED_PAIRS = 100
BATCH_MAX_TAG_SECONDS = 120
# The tag-independent catalog (Polymarket US closed sweep + terminal-evidence
# finalization, Kalshi settled-event scan) is fetched once per batch and shared by
# every tag. Measured live 2026-08-17 at ~110s, which is why folding it into the
# per-tag budget timed out every tag before a single pair was compared.
BATCH_MAX_CATALOG_SECONDS = 300
# Live settlement-candidate discovery also watches the tag-scoped Polymarket Global
# open catalog (e.g. the end-of-2026 fed-funds level event has no US-gateway
# counterpart). Global markets have no order books, so they feed the queue and
# catalog report only — never shadow, approval, or paper-trading paths.
LIVE_GLOBAL_TAG_IDS = BATCH_DEFAULT_GLOBAL_TAG_IDS
LIVE_GLOBAL_OPEN_PAGES = 2
# Gap-radar-only scan scope: every family with a canonical normalizer on both
# venues, so twin shapes can actually form. Radar breadth is deliberately
# decoupled from the scheduled-batch defaults above (label harvesting keeps
# its reviewed scope). All entries verified against the live catalogs
# 2026-08-14: KXU3 4 open events, KXISMPMI 1 (KXUSISMSERV is listed in the
# series catalog with its September event not yet open — a scan of it is one
# bounded empty request until it opens); every Gamma tag below returned open
# markets in the intended family (jobs 993 carries the monthly
# unemployment-rate buckets alongside JOLTS; GDP 370 is mostly foreign-
# jurisdiction contracts, which the normalizers jurisdiction-gate away).
# Elections tag 144 is mostly margin-of-victory spreads, which produce no twin
# shapes — but chamber control is not a spread. It is a CATEGORICAL twin (one
# party, one chamber, one cycle) that the threshold-only matcher could not form
# until `_twin_shape` learned the categorical kind. Added to radar scope
# 2026-08-20, mid-study: these families are quarantined from the go/no-go rate
# in atlas/study.py (POST_START_SCOPE_FAMILIES) so week-to-week comparability
# survives. Verified live the same day: 8 Kalshi chamber-control markets x 9
# Polymarket party-control markets -> 4 twins, all tagged
# SETTLEMENT_TIMING_ASYMMETRIC, which is the only reason the study's
# asymmetric-vs-symmetric split has any eligible population at all.
GAP_RADAR_KALSHI_SERIES_TICKERS = (
    BATCH_DEFAULT_KALSHI_SERIES_TICKERS
    + DISCOVERY_ELECTION_KALSHI_SERIES_TICKERS
    + (
        "KXU3",
        "KXISMPMI",
        "KXUSISMSERV",
    )
)
# Polymarket US gateway categories the radar watches. This is the venue a US
# account can actually trade, and it is the ONLY Polymarket source whose legs
# can be sized from a published book. `macro` carries the FOMC / CPI / GDP /
# payrolls / unemployment ladders. Scope only — it decides what is priced,
# never what is approved.
#
# `politics` is DELIBERATELY EXCLUDED (measured live 2026-08-20). The gateway's
# joint "2026 Midterms: Balance of Power" contracts (`paccc-balpow-*`) settle on
# BOTH chambers at once, yet they normalize to the single-chamber subject
# `us_house_control|2026` with a NON-NULL affirmative outcome — and the wrong
# one: `...-rhou-dsen` ("R House, D Senate") reports `democratic_party`. The
# categorical-twin guard that protects the Global venue relies on joint
# contracts carrying a NULL affirmative outcome, which is true on Gamma and
# false here, so a Kalshi "Will Democrats win the House" leg paired straight
# through and the radar printed phantom gaps of 31.5c and 79.8c. Chamber
# control stays watched on Global tag 144 (already quarantined from the
# go/no-go rate); re-admitting `politics` requires fixing the election
# normalizer's chamber attribution first — see TODO.
GAP_RADAR_PMUS_CATEGORIES = ("macro",)
GAP_RADAR_GLOBAL_TAG_IDS = (
    "100196",  # Fed Rates
    "101701",  # CPI
    "702",  # Inflation
    "993",  # jobs
    "1624",  # unemployment
    "370",  # GDP
    "105113",  # ISM manufacturing + services
    "105533",  # Core PCE
    "144",  # Elections — carries the chamber-control twins (added 2026-08-20)
)


def _parse_global_tag_ids(raw_values: list[str] | None) -> tuple[str, ...]:
    """Parse a bounded, numeric Polymarket Global tag-ID override."""
    if not raw_values:
        return TARGETED_GLOBAL_TAG_IDS

    tag_ids: list[str] = []
    for raw_value in raw_values:
        for value in raw_value.split(","):
            tag_id = value.strip()
            if not tag_id:
                raise ValueError("--global-tag-ids cannot contain empty tag IDs")
            if not tag_id.isdigit() or int(tag_id) < 1:
                raise ValueError(f"invalid Polymarket Global tag ID: {tag_id!r}")
            if tag_id not in tag_ids:
                tag_ids.append(tag_id)

    if len(tag_ids) > MAX_GLOBAL_TAG_IDS:
        raise ValueError(
            f"--global-tag-ids accepts at most {MAX_GLOBAL_TAG_IDS} unique tag IDs"
        )
    return tuple(tag_ids)


def _parse_batch_tag_ids(raw_values: list[str] | None) -> tuple[str, ...]:
    """Return the reproducible default probe set or a validated override."""
    if not raw_values:
        return BATCH_DEFAULT_GLOBAL_TAG_IDS
    return _parse_global_tag_ids(raw_values)


def _parse_kalshi_series_tickers(raw_values: list[str] | None) -> tuple[str, ...]:
    """Parse a bounded Kalshi settled-series ticker list; empty means no series scan."""
    if not raw_values:
        return ()

    tickers: list[str] = []
    for raw_value in raw_values:
        for value in raw_value.split(","):
            ticker = value.strip().upper()
            if not ticker:
                raise ValueError("--kalshi-series-tickers cannot contain empty tickers")
            if not _KALSHI_SERIES_TICKER_PATTERN.fullmatch(ticker):
                raise ValueError(f"invalid Kalshi series ticker: {ticker!r}")
            if ticker not in tickers:
                tickers.append(ticker)

    if len(tickers) > MAX_KALSHI_SERIES_TICKERS:
        raise ValueError(
            f"--kalshi-series-tickers accepts at most {MAX_KALSHI_SERIES_TICKERS} unique tickers"
        )
    return tuple(tickers)


def _parse_batch_series_tickers(raw_values: list[str] | None) -> tuple[str, ...]:
    """Return the reproducible default settled-series scan or a validated override."""
    if not raw_values:
        return BATCH_DEFAULT_KALSHI_SERIES_TICKERS
    return _parse_kalshi_series_tickers(raw_values)


MAX_KALSHI_EVENT_TICKER_FILTER_LENGTH = 80


def _parse_kalshi_event_ticker_filter(raw_value: str | None) -> str | None:
    """Validate the explicit-harvest event-ticker regex; None means no filter.

    Only `learning backfill` accepts this flag — it scopes an explicitly
    requested series scan (e.g. '12$' keeps the noon-ET hourly crypto events)
    and is deliberately absent from batch and scheduled defaults.
    """
    if raw_value is None:
        return None
    value = raw_value.strip()
    if not value:
        raise ValueError("--kalshi-event-ticker-filter cannot be empty")
    if len(value) > MAX_KALSHI_EVENT_TICKER_FILTER_LENGTH:
        raise ValueError(
            "--kalshi-event-ticker-filter accepts at most "
            f"{MAX_KALSHI_EVENT_TICKER_FILTER_LENGTH} characters"
        )
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"invalid --kalshi-event-ticker-filter regex: {exc}") from None
    return value


def _parse_event_slugs(raw_values: list[str] | None, flag: str) -> tuple[str, ...]:
    """Parse a bounded Polymarket event-slug list; empty means no slug harvest."""
    if not raw_values:
        return ()

    slugs: list[str] = []
    for raw_value in raw_values:
        for value in raw_value.split(","):
            slug = value.strip().lower()
            if not slug:
                raise ValueError(f"{flag} cannot contain empty slugs")
            if not _POLYMARKET_US_EVENT_SLUG_PATTERN.fullmatch(slug):
                raise ValueError(f"invalid Polymarket event slug: {slug!r}")
            if slug not in slugs:
                slugs.append(slug)

    if len(slugs) > MAX_POLYMARKET_US_EVENT_SLUGS:
        raise ValueError(
            f"{flag} accepts at most {MAX_POLYMARKET_US_EVENT_SLUGS} unique slugs"
        )
    return tuple(slugs)


def _parse_polymarket_us_event_slugs(raw_values: list[str] | None) -> tuple[str, ...]:
    return _parse_event_slugs(raw_values, "--polymarket-us-event-slugs")


def _parse_polymarket_global_event_slugs(raw_values: list[str] | None) -> tuple[str, ...]:
    return _parse_event_slugs(raw_values, "--polymarket-global-event-slugs")


def _validate_batch_limits(
    *,
    target: int,
    kalshi_event_pages: int,
    polymarket_pages: int,
    global_pages: int,
    candidate_events: int,
    market_pairs: int,
    resolved_pairs: int,
) -> None:
    limits = {
        "target": (target, BATCH_MAX_TARGET_LABELS),
        "kalshi_event_pages": (kalshi_event_pages, BATCH_MAX_KALSHI_EVENT_PAGES),
        "polymarket_pages": (polymarket_pages, BATCH_MAX_POLYMARKET_PAGES),
        "global_pages": (global_pages, BATCH_MAX_GLOBAL_PAGES),
        "candidate_events": (candidate_events, BATCH_MAX_CANDIDATE_EVENTS),
        "market_pairs": (market_pairs, BATCH_MAX_MARKET_PAIRS),
        "resolved_pairs": (resolved_pairs, BATCH_MAX_RESOLVED_PAIRS),
    }
    for name, (value, maximum) in limits.items():
        if value < 1 or value > maximum:
            raise ValueError(f"{name} must be between 1 and {maximum} for a batch scan")


async def markets_sync(fixture: bool = True) -> None:
    # The markets table is legacy/unread (write-only, zero SELECTs anywhere),
    # so this command no longer persists the catalog — it lists it.
    for venue in (KalshiVenue(fixture=fixture), PolymarketUSVenue(fixture=fixture)):
        markets = await venue.list_markets()
        print(f"{venue.name}: listed {len(markets)} market(s) (catalog persistence retired)")
        for market in markets:
            print(f"  {market.market_id} | {market.title}")


async def books_inspect(venue_name: str, market_id: str) -> None:
    venue = KalshiVenue() if venue_name == "kalshi" else PolymarketUSVenue()
    book = await venue.get_orderbook(market_id)
    print(f"{book.venue} {book.market_id} sequence={book.sequence}")
    for side in ("YES", "NO"):
        print(f"{side} asks: {[(str(x.price), str(x.quantity)) for x in book.asks_for(side)]}")


async def opportunities_demo() -> None:
    markets = fixture_markets()
    books = fixture_books()
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
    if opportunity is None:
        print("No approved executable opportunity")
        return
    trade = PaperExecutor().execute(opportunity)
    print("MATCH FOUND")
    print("Kalshi contract ↔ Polymarket US contract")
    print(f"Match confidence: {pair.match_confidence:.1%}")
    print(f"Kalshi YES executable: ${opportunity.leg_a_average_price:.2f}")
    print(f"Polymarket equivalent hedge: ${opportunity.leg_b_average_price:.2f}")
    print(f"Gross cost: ${opportunity.gross_cost:.2f}")
    print(f"Estimated fees/slippage: ${(opportunity.fees + opportunity.slippage):.3f}")
    print(f"Net locked edge: {opportunity.expected_roi:.1%}")
    print(f"Available executable size: ${opportunity.contracts:.2f}")
    print(f"Resolution-rule check: {opportunity.rule_check}")
    print(f"Paper trade: {trade.status}")


async def agent_research(mode: str = "fixture", replay_path: str | None = None) -> None:
    if mode == "live":
        markets = {
            "kalshi": await KalshiVenue(fixture=False).list_markets(),
            "polymarket_us": await PolymarketUSVenue(fixture=False).list_markets(),
        }
        books = {}
    elif mode == "replay":
        if not replay_path:
            raise ValueError("--input is required for replay mode")
        from atlas.models import VenueName

        captured = read_market_bundle(replay_path)
        markets = {
            "kalshi": captured[VenueName.KALSHI],
            "polymarket_us": captured[VenueName.POLYMARKET_US],
        }
        books = {}
    else:
        markets = fixture_markets()
        books = fixture_books()
    run = await AtlasAgent(
        {"kalshi": markets["kalshi"], "polymarket_us": markets["polymarket_us"]},
        books=books,
    ).run()
    await AtlasStore().save_agent_run(run.model_dump())
    print(f"agent_run={run.status} steps={len(run.steps)} opportunities={len(run.opportunities)}")
    for step in run.steps:
        print(f"  {step.action}: {step.reason}")
    for opportunity in run.opportunities:
        print(f"  PAPER EDGE {opportunity.pair_id} roi={opportunity.expected_roi:.1%}")


async def monitor_once() -> None:
    opportunity = await run_once()
    print(
        f"monitor: {'opportunity saved ' + opportunity.opportunity_id if opportunity else 'no opportunity'}"
    )


async def approve_live_pair(kalshi_id: str, polymarket_id: str, approved_by: str) -> None:
    kalshi_markets = filter_live_markets(await KalshiVenue(fixture=False).list_markets())
    polymarket_markets = filter_live_markets(await PolymarketUSVenue(fixture=False).list_markets())
    market_a = next(
        (m for m in kalshi_markets if m.market_id == kalshi_id or m.venue_market_id == kalshi_id),
        None,
    )
    market_b = next(
        (
            m
            for m in polymarket_markets
            if m.market_id == polymarket_id or m.venue_market_id == polymarket_id
        ),
        None,
    )
    if market_a is None or market_b is None:
        raise ValueError(
            f"no active market found for the requested pair; kalshi_active={len(kalshi_markets)} polymarket_active={len(polymarket_markets)}"
        )
    pair = await approve_pair(AtlasStore(), market_a, market_b, approved_by)
    print(f"approved_pair={pair.pair_id}")


async def _safe_global_open_markets() -> list:
    """Tag-scoped Polymarket Global open catalog for settlement discovery only.

    Global markets expose no order books, so they extend the compatibility/queue
    computation and never the shadow, approval, or paper-trading paths. A Gamma
    outage degrades to the US-only catalog instead of failing the scan.
    """
    venue = PolymarketGlobalHistoricalVenue(tag_ids=LIVE_GLOBAL_TAG_IDS)
    try:
        return await venue.list_open_markets(max_pages=LIVE_GLOBAL_OPEN_PAGES)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        print(
            f"global_open_catalog_failed={type(exc).__name__} "
            "continuing_with_us_catalog_only"
        )
        return []


async def scan_pairs(live: bool) -> list:
    kalshi_venue = KalshiVenue(fixture=not live)
    polymarket_venue = PolymarketUSVenue(fixture=not live)
    kalshi_markets = await kalshi_venue.list_markets()
    if live:
        # The recent-first open catalog drowns low-frequency macro series under
        # sports markets — the same reach gap the settled-event scan had. Merge
        # the bounded series scan so open FOMC/CPI markets enter the queue,
        # plus the chamber-control series feeding election discovery.
        series_markets = await kalshi_venue.list_open_series_markets(
            BATCH_DEFAULT_KALSHI_SERIES_TICKERS + DISCOVERY_ELECTION_KALSHI_SERIES_TICKERS
        )
        seen_ids = {market.market_id for market in kalshi_markets}
        kalshi_markets.extend(
            market for market in series_markets if market.market_id not in seen_ids
        )
    polymarket_markets = await polymarket_venue.list_markets()
    global_open_markets = await _safe_global_open_markets() if live else []
    store = AtlasStore()
    kalshi_active = len(filter_live_markets(kalshi_markets))
    polymarket_active = len(filter_live_markets(polymarket_markets))
    previous_scan = await store.latest_discovery_scan()
    healthy, catalog_blockers = _catalog_health(kalshi_active, polymarket_active, previous_scan)
    if live and not healthy:
        print(
            f"scan_rejected: kalshi_active={kalshi_active} "
            f"polymarket_active={polymarket_active} "
            f"blockers={','.join(catalog_blockers)}"
        )
        return []
    if live:
        enriched = await _enrich_candidate_sources(kalshi_venue, kalshi_markets, polymarket_markets)
        print(f"source_enrichment: kalshi_candidates={enriched}")
        weather_enrichment = await enrich_weather_rules(
            store,
            kalshi_venue,
            polymarket_venue,
            kalshi_markets,
            polymarket_markets,
        )
        print(
            "weather_enrichment: "
            f"shared={weather_enrichment['shared_events_considered']} "
            f"refreshed={weather_enrichment['pairs_refreshed']} "
            f"new_versions={weather_enrichment['new_evidence_versions']} "
            f"exact={weather_enrichment['exact_rule_matches']}"
        )
        shared_rule_enrichment = await enrich_shared_rules(
            store,
            kalshi_venue,
            polymarket_venue,
            kalshi_markets,
            polymarket_markets,
            exclude_market_types={"weather"},
        )
        print(
            "shared_rule_enrichment: "
            f"shared={shared_rule_enrichment['shared_events_considered']} "
            f"refreshed={shared_rule_enrichment['pairs_refreshed']} "
            f"new_versions={shared_rule_enrichment['new_evidence_versions']} "
            f"complete={shared_rule_enrichment['complete_policy_pairs']} "
            f"skipped_non_guaranteed={shared_rule_enrichment['shared_events_skipped_non_guaranteed']}"
        )
    else:
        weather_enrichment = None
        shared_rule_enrichment = None
    # THE PAIRING UNIVERSE INCLUDES GLOBAL. Every approved pair on record is
    # Kalshi <-> polymarket_global, yet this scan compared Kalshi against
    # POLYMARKET US ONLY — so `discovery_scans.approved` read 0 across 21
    # consecutive scans while 4 approved pairs sat in the queue, and the live
    # label loop was structurally dead (100% of labels came from backfill.py).
    #
    # Deliberately NOT widened: the enrichment passes above and the shadow
    # observation below are bound to `polymarket_venue` (the US adapter) and
    # would ask it for Global slugs, and shadow additionally needs an order book
    # that the Global adapter does not expose at all.
    polymarket_universe = [*polymarket_markets, *global_open_markets]
    pairs = scan_market_pairs(kalshi_markets, polymarket_universe)
    approved = [
        pair for pair in pairs if pair.status.value in {"APPROVED_EQUIVALENT", "APPROVED_INVERSE"}
    ]
    review = [pair for pair in pairs if pair.status.value == "REVIEW_REQUIRED"]
    catalog_report = compatibility_report(kalshi_markets, polymarket_universe)
    catalog_report["polymarket_global_open_markets"] = len(global_open_markets)
    catalog_report["polymarket_global_open_tag_ids"] = list(
        LIVE_GLOBAL_TAG_IDS if global_open_markets else ()
    )
    if weather_enrichment is not None:
        catalog_report["weather_rule_enrichment"] = weather_enrichment
    if shared_rule_enrichment is not None:
        catalog_report["shared_rule_enrichment"] = shared_rule_enrichment
    await store.save_catalog_report(catalog_report)
    settlement_rankings = catalog_report.get("settlement_discovery", {}).get("rankings", [])
    await store.save_settlement_candidates(settlement_rankings)
    # Blocked frontier legs are precisely the markets the validation universe skips
    # (guarantee unknown, or a Global leg it never receives), so without this pass
    # the pairs we are waiting on have no rules baseline to detect a change against.
    frontier_evidence = await capture_frontier_rules_evidence(
        store,
        settlement_rankings,
        [*kalshi_markets, *polymarket_universe],
    )
    print(
        "frontier_evidence: "
        f"legs={frontier_evidence['frontier_legs_observed']} "
        f"new_versions={frontier_evidence['frontier_new_versions']} "
        f"unavailable={frontier_evidence['frontier_legs_unavailable']}"
    )
    reviews = review_market_pairs(kalshi_markets, polymarket_universe)
    identity_candidates = structured_identity_candidates(kalshi_markets, polymarket_universe)
    review_candidates = _deduplicate_candidates([*reviews, *identity_candidates])
    if not review_candidates:
        review_candidates = propose_market_pairs(kalshi_markets, polymarket_universe)
    await store.save_candidate_proposals(review_candidates[:25])
    validation_capture = await capture_validation_universe(
        store, kalshi_markets, polymarket_universe, approved, review_candidates
    )
    # A Global leg is meaningless to the US gateway, so reconciliation is given
    # an adapter per venue. Without this, every Global case 404s and retries
    # forever under a reason code that reads like a venue outage.
    validation_reconciliation = await reconcile_validation_cases(
        store,
        kalshi_venue,
        polymarket_venue,
        extra_polymarket_venues={
            VenueName.POLYMARKET_GLOBAL.value: PolymarketGlobalHistoricalVenue(
                tag_ids=LIVE_GLOBAL_TAG_IDS
            )
        },
    )
    print(
        "validation: "
        f"markets={validation_capture['markets_observed']} "
        f"new_versions={validation_capture['new_evidence_versions']} "
        f"new_cases={validation_capture['new_validation_cases']} "
        f"checked={validation_reconciliation['checked']} "
        f"resolved={validation_reconciliation['resolved']} "
        f"labels={validation_reconciliation['labeled']}"
    )
    if live:
        await _record_shadow_observation(
            store, kalshi_venue, polymarket_venue, kalshi_markets, polymarket_markets
        )
    for pair in approved:
        pair.approved_by = "auto-deterministic"
        await store.save_pair(pair)
    result = {
        "kalshi_active": kalshi_active,
        "polymarket_active": polymarket_active,
        "comparisons": len(pairs),
        "approved": len(approved),
        "review": len(review),
    }
    await AtlasStore().save_discovery_scan(result)
    print(
        f"scan: kalshi_active={kalshi_active} "
        f"polymarket_active={polymarket_active} "
        f"global_open={len(global_open_markets)} "
        f"comparisons={len(pairs)} approved={len(approved)} review={len(review)}"
    )
    for pair in approved:
        print(f"  APPROVED CANDIDATE {pair.pair_id}")
    return approved


def _deduplicate_candidates(
    candidates: list[dict[str, object]],
) -> list[dict[str, object]]:
    unique: dict[tuple[str, str], dict[str, object]] = {}
    for candidate in candidates:
        key = (
            str(candidate["kalshi_market_id"]),
            str(candidate["polymarket_market_id"]),
        )
        unique.setdefault(key, candidate)
    return list(unique.values())


async def _enrich_candidate_sources(
    kalshi_venue: KalshiVenue,
    kalshi_markets: list,
    polymarket_markets: list,
) -> int:
    candidates_by_id = {
        market.market_id: market
        for market in (
            markets_requiring_source_enrichment(kalshi_markets, polymarket_markets)
            + markets_requiring_family_source_enrichment(kalshi_markets)
        )
    }
    candidates = list(candidates_by_id.values())
    by_event: dict[str, list] = {}
    for market in candidates:
        event_ticker = str(market.raw_market_json.get("event_ticker") or "")
        if event_ticker:
            by_event.setdefault(event_ticker, []).append(market)

    enriched = 0
    for markets in by_event.values():
        try:
            representative = await kalshi_venue.enrich_market_source(markets[0])
        except (httpx.HTTPError, ValueError):
            continue
        source = representative.resolution_source
        if source in {"", "unknown"}:
            continue
        for market in markets:
            market.resolution_source = source
            market.raw_market_json["event_settlement_source"] = source
            enriched += 1
    return enriched


def _catalog_health(
    kalshi_active: int,
    polymarket_active: int,
    previous_scan: dict[str, int | str] | None,
) -> tuple[bool, list[str]]:
    """Reject obviously truncated feed snapshots before they replace trusted state."""
    blockers: list[str] = []
    if kalshi_active <= 1000:
        blockers.append("KALSHI_CATALOG_TRUNCATED")
    if polymarket_active <= 100:
        blockers.append("POLYMARKET_CATALOG_TRUNCATED")
    if previous_scan:
        previous_kalshi = int(previous_scan.get("kalshi_active", 0))
        previous_polymarket = int(previous_scan.get("polymarket_active", 0))
        if previous_kalshi > 1000 and kalshi_active < previous_kalshi // 2:
            blockers.append("KALSHI_CATALOG_DROPPED_OVER_50_PERCENT")
        if previous_polymarket > 100 and polymarket_active < previous_polymarket // 2:
            blockers.append("POLYMARKET_CATALOG_DROPPED_OVER_50_PERCENT")
    return not blockers, blockers


async def _record_shadow_observation(
    store: AtlasStore,
    kalshi_venue: KalshiVenue,
    polymarket_venue: PolymarketUSVenue,
    kalshi_markets: list,
    polymarket_markets: list,
    limit: int = 10,
) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    for pair in find_shadow_pairs(kalshi_markets, polymarket_markets, limit=limit):
        try:
            await kalshi_venue.enrich_market_source(pair.market_a)
            pair = verify_equivalence(pair.market_a, pair.market_b, pair.pair_id)
            book_a, book_b = await asyncio.gather(
                kalshi_venue.get_orderbook(pair.market_a.venue_market_id),
                polymarket_venue.get_orderbook(pair.market_b.venue_market_id),
            )
        except (httpx.HTTPError, ValueError):
            continue
        observation = observe_shadow_pair(pair, book_a, book_b)
        if observation:
            await store.save_orderbook(book_a)
            await store.save_orderbook(book_b)
            await store.save_shadow_observation(observation)
            best = observation["best_direction"]
            print(
                f"shadow: pair={pair.pair_id} cost={best['gross_cost']} "
                f"blockers={','.join(observation['blockers'])}"
            )
            observations.append(observation)
    return observations


async def shadow_watch(interval: int, limit: int) -> None:
    """Continuously observe live shadow pairs without creating paper trades."""
    store = AtlasStore()
    delay = max(interval, 10)
    while True:
        kalshi_venue = KalshiVenue(fixture=False)
        polymarket_venue = PolymarketUSVenue(fixture=False)
        kalshi_markets = await kalshi_venue.list_markets()
        polymarket_markets = await polymarket_venue.list_markets()
        observations = await _record_shadow_observation(
            store,
            kalshi_venue,
            polymarket_venue,
            kalshi_markets,
            polymarket_markets,
            limit=max(limit, 1),
        )
        print(f"shadow_watch: observed={len(observations)} interval={delay}s status=NEVER_EXECUTED")
        await asyncio.sleep(delay)


async def watch_pairs(
    live: bool, interval: int, backfill_interval: int = 86_400, prune_interval: int = 86_400
) -> None:
    monitors: dict[str, asyncio.Task] = {}
    store = AtlasStore()
    last_pruned_at: datetime | None = None
    # Printed BEFORE any network work: the liveness watchdog measures this
    # log's mtime, and the first cycle print otherwise lands only after minutes
    # of venue sweeps. A monitor that started must be distinguishable from one
    # that is wedged, from its very first second.
    print(f"monitor_started: paper_only=true live={live} interval={interval}s")
    while True:
        approved = await _safe_scan_pairs(live)
        if live:
            for pair in approved:
                if pair.pair_id not in monitors:
                    task = asyncio.create_task(run_pair(pair))
                    # Without this callback a failure inside the task is
                    # swallowed and the pair silently never streams, which is
                    # indistinguishable from "no opportunity was found" — the
                    # exact shape of a missing-credential outage. The callback
                    # also pops the pair from `monitors` so the next scan
                    # iteration can respawn it instead of it staying dead.
                    task.add_done_callback(
                        functools.partial(
                            _report_pair_monitor_exit,
                            monitors=monitors,
                            pair_id=pair.pair_id,
                        )
                    )
                    monitors[pair.pair_id] = task
            if await _historical_backfill_due(store, backfill_interval):
                try:
                    report = await _run_scheduled_backfill()
                    print(
                        "scheduled_backfill: "
                        f"status={report['status']} "
                        f"tags={','.join(report['tag_ids'])}"
                    )
                except (httpx.HTTPError, OSError, ValueError) as exc:
                    print(f"scheduled_backfill_failed={type(exc).__name__}")
            if _prune_due(last_pruned_at, prune_interval):
                try:
                    deleted = await store.prune()
                    last_pruned_at = datetime.now(UTC)
                    print(
                        f"scheduled_prune: deleted={sum(deleted.values())} "
                        "note=manual_vacuum_reclaims_disk"
                    )
                except (OSError, sqlite3.Error) as exc:
                    print(f"scheduled_prune_failed={type(exc).__name__}")
            # Gap-radar evidence accrues on the same cadence as the scan so the
            # executable-gap question answers itself while the monitor runs.
            try:
                await gaps_scan(live=True)
            except (httpx.HTTPError, OSError, ValueError) as exc:
                print(f"gap_radar_scan_failed={type(exc).__name__} retry_on_next_interval=true")
            await _burst_aware_sleep(interval)
        else:
            await asyncio.sleep(interval)


async def _burst_aware_sleep(interval: int) -> None:
    """Sleep out one monitor interval, running extra read-only radar scans on
    the burst cadence while a scheduled-release window is open.

    Only the bounded gap radar accelerates; the full pair scan, backfills, and
    everything else stay on the monitor's base interval. Sleeping in short
    slices lets the loop notice a window that opens mid-interval.
    """
    from atlas.release_calendar import radar_delay_seconds

    slept = 0
    in_burst = False
    while slept < interval:
        delay, release = radar_delay_seconds(datetime.now(UTC), interval)
        if release is None:
            in_burst = False
            chunk = min(60, interval - slept)
            await asyncio.sleep(chunk)
            slept += chunk
            continue
        if not in_burst:
            print(f"release_burst: window={release} radar_interval={delay}s")
            in_burst = True
            # One capacity walk per window entry, not per burst scan: the
            # ladder costs two book requests per pair, and the question is
            # whether depth shows up at all, not its second-by-second shape.
            try:
                await record_capacity_samples(release_window=release)
            except (httpx.HTTPError, OSError, ValueError) as exc:
                print(f"capacity_record_failed={type(exc).__name__} window={release}")
        await asyncio.sleep(delay)
        slept += delay
        try:
            await gaps_scan(live=True)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            print(f"gap_radar_scan_failed={type(exc).__name__} retry_on_next_interval=true")


def _prune_due(last_pruned_at: datetime | None, interval: int) -> bool:
    """Daily retention-sweep guard, mirroring `_historical_backfill_due`.

    Prune leaves no persisted marker to read back, so the guard is in-process:
    the first live iteration prunes immediately, then once per interval for
    the life of the monitor process. A restart pruning once more is harmless —
    the sweep is idempotent over anything younger than its cutoffs.
    """
    if last_pruned_at is None:
        return True
    return datetime.now(UTC) - last_pruned_at >= timedelta(seconds=max(interval, 60))


async def prune_stale_data(store: AtlasStore | None = None) -> None:
    """Delete stale operational rows; the evidence/label chain is never touched."""
    deleted = await (store or AtlasStore()).prune()
    for table, count in deleted.items():
        print(f"prune: {table} deleted={count}")
    print(
        f"prune: total_deleted={sum(deleted.values())} "
        "note=DELETE frees pages but never shrinks the file; "
        "run a one-time manual VACUUM to reclaim disk"
    )


async def _historical_backfill_due(store: AtlasStore, interval: int) -> bool:
    latest = await store.latest_historical_backfill()
    if latest is None:
        return True
    completed_at = latest.get("completed_at")
    if not completed_at:
        return True
    completed = datetime.fromisoformat(str(completed_at))
    return datetime.now(UTC) - completed >= timedelta(seconds=max(interval, 60))


def _report_pair_monitor_exit(
    task: asyncio.Task,
    monitors: dict[str, asyncio.Task] | None = None,
    pair_id: str | None = None,
) -> None:
    """Log why a live pair monitor stopped; never let it fail silently.

    Always drops the pair from the monitors registry first — on every exit
    path, including cancellation and missing credentials — so the next scan
    iteration respawns the pair instead of leaving it dead for the process
    lifetime.
    """
    if monitors is not None and pair_id is not None:
        monitors.pop(pair_id, None)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    if isinstance(exc, LiveStreamCredentialsMissing):
        print(f"live_pair_monitor_blocked=CREDENTIALS_MISSING detail={exc} paper_only=true")
        return
    print(f"live_pair_monitor_failed={type(exc).__name__} detail={exc}")


async def _safe_scan_pairs(live: bool) -> list:
    """Keep the continuous monitor alive when a venue refresh fails."""
    try:
        return await scan_pairs(live)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        print(
            f"pairs_watch_scan_failed={type(exc).__name__} "
            "status=NEVER_EXECUTED retry_on_next_interval=true"
        )
        return []


async def _run_scheduled_backfill() -> dict[str, object]:
    """Run the automated historical pass with the same bounded batch policy as the CLI."""
    return await learning_backfill_batch(
        live=True,
        target=1,
        kalshi_event_pages=BATCH_MAX_KALSHI_EVENT_PAGES,
        polymarket_pages=BATCH_MAX_POLYMARKET_PAGES,
        global_pages=BATCH_MAX_GLOBAL_PAGES,
        candidate_events=BATCH_MAX_CANDIDATE_EVENTS,
        market_pairs=BATCH_MAX_MARKET_PAIRS,
        resolved_pairs=BATCH_MAX_RESOLVED_PAIRS,
        global_tag_ids=BATCH_DEFAULT_GLOBAL_TAG_IDS,
        kalshi_series_tickers=BATCH_DEFAULT_KALSHI_SERIES_TICKERS,
    )


async def approval_frontier_report(limit: int) -> dict[str, object]:
    """Print the read-only approval frontier: closest blocked pairs, moved text first."""
    report = await approval_frontier(AtlasStore(), limit=limit)
    print(
        "approval_frontier: "
        f"blocked={report['blocked_candidates']} "
        f"text_only={report['blocked_only_on_venue_text']} "
        f"rules_changed={report['rules_changed_recently']} "
        f"unmonitored={report['unmonitored_pairs']} "
        f"window_days={report['rules_change_window_days']} paper_only=true"
    )
    for entry in report["entries"]:
        flag = " <- PUBLISHED RULES CHANGED" if entry["rules_changed_recently"] else ""
        reach = "venue-text only" if entry["blocked_only_on_venue_text"] else "structural gap"
        print(f"  {entry['event_subject']} (distance={entry['rule_distance']}, {reach}){flag}")
        if entry["text_clearable_codes"]:
            print(f"    could clear on text: {', '.join(entry['text_clearable_codes'])}")
        if entry["structural_codes"]:
            print(f"    not a text problem:  {', '.join(entry['structural_codes'])}")
        if entry["unmonitored_legs"]:
            print(
                "    BLIND SPOT: no rules baseline recorded for "
                f"{', '.join(entry['unmonitored_legs'])} — a text change here would "
                "not be detected"
            )
        for leg in ("kalshi", "polymarket"):
            state = entry[leg]
            changed = state["rules_changed_at"] or "never"
            print(f"    {leg:11s} versions={state['rules_versions']} last_change={changed}")
    return report


async def candidate_pairs(live: bool, limit: int) -> None:
    kalshi_markets = await KalshiVenue(fixture=not live).list_markets()
    polymarket_markets = await PolymarketUSVenue(fixture=not live).list_markets()
    proposals = _deduplicate_candidates(
        [
            *review_market_pairs(kalshi_markets, polymarket_markets, limit),
            *structured_identity_candidates(kalshi_markets, polymarket_markets, limit),
        ]
    )[:limit]
    if not proposals:
        proposals = propose_market_pairs(kalshi_markets, polymarket_markets, limit)
    await AtlasStore().save_candidate_proposals(proposals)
    print(f"candidate_proposals={len(proposals)} status=REVIEW_REQUIRED")
    for proposal in proposals:
        print(
            f"  {proposal['score']:.1%} | {proposal['kalshi_title']} ↔ "
            f"{proposal['polymarket_title']} | "
            f"mismatches={','.join(proposal.get('mismatch_codes', [])) or 'lexical-review'}"
        )


async def learning_export(
    path: str, evaluation_path: str | None = None, evaluation_ratio: float = 0.2
) -> None:
    if evaluation_path:
        counts = await export_learning_splits(AtlasStore(), path, evaluation_path, evaluation_ratio)
        print(
            f"exported_training={counts['training']} path={path} "
            f"evaluation={counts['evaluation']} eval_path={evaluation_path}"
        )
        return
    count = await export_training_jsonl(AtlasStore(), path)
    print(f"exported_learning_examples={count} path={path}")


async def learning_status() -> None:
    store = AtlasStore()
    counts = await store.learning_counts()
    trusted = await store.trusted_learning_counts()
    print("learning_counts=" + " ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    print(
        "trusted_settlement_labels="
        + " ".join(f"{key}={value}" for key, value in sorted(trusted.items()))
    )


async def learning_readiness() -> None:
    from atlas.evaluation import learning_readiness as get_readiness

    result = await get_readiness(AtlasStore())
    print(
        f"training_ready={result['ready']} labels={result['labels']} observations={result['observations']}"
    )
    for reason in result["reasons"]:
        print(f"  BLOCKED: {reason}")


async def learning_reconcile() -> None:
    from atlas.reconcile import reconcile_pending_trades

    print(await reconcile_pending_trades(AtlasStore()))


async def _run_learning_backfill(
    live: bool,
    target: int,
    kalshi_event_pages: int,
    polymarket_pages: int,
    global_pages: int,
    candidate_events: int = 100,
    market_pairs: int = 2_000,
    resolved_pairs: int = 250,
    global_tag_ids: tuple[str, ...] | None = None,
    kalshi_series_tickers: tuple[str, ...] | None = None,
    kalshi_event_ticker_filter: str | None = None,
    polymarket_us_event_slugs: tuple[str, ...] = (),
    polymarket_global_event_slugs: tuple[str, ...] = (),
    shared_catalog: SharedBackfillCatalog | None = None,
) -> dict[str, object]:
    if not live:
        raise ValueError("historical backfill requires --live public venue data")
    return await backfill_historical_validation(
        AtlasStore(),
        KalshiVenue(fixture=False),
        PolymarketUSVenue(fixture=False),
        target_labels=target,
        kalshi_event_pages=kalshi_event_pages,
        kalshi_series_tickers=kalshi_series_tickers,
        kalshi_event_ticker_filter=kalshi_event_ticker_filter,
        polymarket_pages=polymarket_pages,
        polymarket_us_event_slugs=polymarket_us_event_slugs,
        polymarket_global_event_slugs=polymarket_global_event_slugs,
        additional_polymarket_venues={
            "polymarket_global": PolymarketGlobalHistoricalVenue(
                tag_ids=global_tag_ids or TARGETED_GLOBAL_TAG_IDS
            )
        },
        additional_polymarket_pages=global_pages,
        max_candidate_events=candidate_events,
        max_market_pairs=market_pairs,
        max_resolved_pairs=resolved_pairs,
        shared_catalog=shared_catalog,
    )


def _print_historical_backfill_report(report: dict[str, object]) -> None:
    print(
        "historical_backfill: "
        f"status={report['status']} labels={report['labels_after']}/{report['target_labels']} "
        f"polymarket_final={report['polymarket_final_binary_markets']} "
        f"kalshi_events={report['kalshi_events_scanned']} "
        f"shared={report['cross_venue_event_candidates']} "
        f"new_labels={report['new_labels']}"
    )
    for source, coverage in report["venue_coverage"].items():
        print(
            f"  source={source} final={coverage['final_binary_markets']} "
            f"closed={coverage['closed_markets']}"
        )
    series_counts = report.get("kalshi_series_event_counts") or {}
    if series_counts:
        joined = " ".join(f"{ticker}={count}" for ticker, count in series_counts.items())
        print(f"  kalshi_series_events: {joined}")
    ticker_filter = report.get("kalshi_event_ticker_filter")
    if ticker_filter:
        print(f"  kalshi_event_ticker_filter: {ticker_filter}")
    slug_counts = report.get("polymarket_us_event_slug_markets") or {}
    for label, requested in (
        ("polymarket_us_event_slugs", report.get("polymarket_us_event_slugs") or []),
        ("polymarket_global_event_slugs", report.get("polymarket_global_event_slugs") or []),
    ):
        if requested:
            joined = " ".join(f"{slug}={slug_counts.get(slug, 0)}" for slug in requested)
            print(f"  {label}: {joined}")
    for blocker, count in report["blockers"].items():
        print(f"  BLOCKED: {blocker} x{count}")


async def learning_backfill(
    live: bool,
    target: int,
    kalshi_event_pages: int,
    polymarket_pages: int,
    global_pages: int,
    candidate_events: int = 100,
    market_pairs: int = 2_000,
    resolved_pairs: int = 250,
    global_tag_ids: tuple[str, ...] | None = None,
    kalshi_series_tickers: tuple[str, ...] | None = None,
    kalshi_event_ticker_filter: str | None = None,
    polymarket_us_event_slugs: tuple[str, ...] = (),
    polymarket_global_event_slugs: tuple[str, ...] = (),
) -> dict[str, object]:
    report = await _run_learning_backfill(
        live,
        target,
        kalshi_event_pages,
        polymarket_pages,
        global_pages,
        candidate_events,
        market_pairs,
        resolved_pairs,
        global_tag_ids,
        kalshi_series_tickers=kalshi_series_tickers,
        kalshi_event_ticker_filter=kalshi_event_ticker_filter,
        polymarket_us_event_slugs=polymarket_us_event_slugs,
        polymarket_global_event_slugs=polymarket_global_event_slugs,
    )
    _print_historical_backfill_report(report)
    return report


async def _prefetch_batch_catalog(
    *,
    kalshi_event_pages: int,
    kalshi_series_tickers: tuple[str, ...],
    polymarket_pages: int,
) -> SharedBackfillCatalog:
    """Live read-only seam for the batch's shared catalog fetch (patchable in tests)."""
    return await prefetch_shared_backfill_catalog(
        KalshiVenue(fixture=False),
        PolymarketUSVenue(fixture=False),
        kalshi_event_pages=kalshi_event_pages,
        kalshi_series_tickers=kalshi_series_tickers,
        polymarket_pages=polymarket_pages,
    )


async def learning_backfill_batch(
    live: bool,
    target: int = 1,
    kalshi_event_pages: int = BATCH_MAX_KALSHI_EVENT_PAGES,
    polymarket_pages: int = BATCH_MAX_POLYMARKET_PAGES,
    global_pages: int = 1,
    candidate_events: int = BATCH_MAX_CANDIDATE_EVENTS,
    market_pairs: int = BATCH_MAX_MARKET_PAIRS,
    resolved_pairs: int = BATCH_MAX_RESOLVED_PAIRS,
    global_tag_ids: tuple[str, ...] | None = None,
    kalshi_series_tickers: tuple[str, ...] | None = None,
) -> dict[str, object]:
    """Run one bounded, paper-only historical probe per Global tag."""
    if not live:
        raise ValueError("historical backfill batch requires --live public venue data")
    _validate_batch_limits(
        target=target,
        kalshi_event_pages=kalshi_event_pages,
        polymarket_pages=polymarket_pages,
        global_pages=global_pages,
        candidate_events=candidate_events,
        market_pairs=market_pairs,
        resolved_pairs=resolved_pairs,
    )
    tag_ids = _parse_batch_tag_ids(list(global_tag_ids) if global_tag_ids else None)
    series_tickers = _parse_batch_series_tickers(
        list(kalshi_series_tickers) if kalshi_series_tickers else None
    )

    # Fetch the tag-independent catalog once. Previously every tag re-fetched it
    # inside its own 120s budget, and the fetch alone measured ~110s live, so no
    # tag ever reached its first comparison.
    shared_catalog: SharedBackfillCatalog | None = None
    catalog_status = "SHARED"
    catalog_error: str | None = None
    try:
        shared_catalog = await asyncio.wait_for(
            _prefetch_batch_catalog(
                kalshi_event_pages=kalshi_event_pages,
                kalshi_series_tickers=series_tickers,
                polymarket_pages=polymarket_pages,
            ),
            timeout=BATCH_MAX_CATALOG_SECONDS,
        )
    except TimeoutError:
        catalog_status = "TIMED_OUT"
        catalog_error = f"shared catalog fetch exceeded {BATCH_MAX_CATALOG_SECONDS}s"
    except (httpx.HTTPError, OSError, ValueError) as exc:
        catalog_status = "FAILED"
        catalog_error = f"{type(exc).__name__}: {exc}"

    per_tag: list[dict[str, object]] = []
    for tag_id in tag_ids:
        try:
            report = await asyncio.wait_for(
                _run_learning_backfill(
                    live,
                    target,
                    kalshi_event_pages,
                    polymarket_pages,
                    global_pages,
                    candidate_events,
                    market_pairs,
                    resolved_pairs,
                    (tag_id,),
                    kalshi_series_tickers=series_tickers,
                    shared_catalog=shared_catalog,
                ),
                timeout=BATCH_MAX_TAG_SECONDS,
            )
        except TimeoutError:
            per_tag.append(
                {
                    "tag_id": tag_id,
                    "status": "TIMED_OUT",
                    "error_type": "TimeoutError",
                    "error": f"tag scan exceeded {BATCH_MAX_TAG_SECONDS}s",
                }
            )
            continue
        except (httpx.HTTPError, OSError, ValueError) as exc:
            per_tag.append(
                {
                    "tag_id": tag_id,
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            continue
        if report.get("paper_only") is not True:
            raise RuntimeError(f"tag {tag_id} returned a non-paper-only report")
        per_tag.append(
            {
                "tag_id": tag_id,
                "status": report.get("status"),
                "labels_before": report.get("labels_before"),
                "labels_after": report.get("labels_after"),
                "approved_labels": report.get("approved_labels"),
                "rejected_labels": report.get("rejected_labels"),
                "new_labels": report.get("new_labels"),
                "market_pairs_reviewed": report.get("market_pairs_reviewed"),
                "resolved_pairs": report.get("resolved_pairs"),
                "inconclusive_pairs": report.get("inconclusive_pairs"),
                "historical_candidate_events": report.get("historical_candidate_events"),
                "historical_candidate_events_found": report.get(
                    "historical_candidate_events_found"
                ),
                "venue_coverage": report.get("venue_coverage", {}),
                "kalshi_series_event_counts": report.get("kalshi_series_event_counts", {}),
                "blockers": report.get("blockers", {}),
            }
        )

    failed = [result for result in per_tag if result["status"] in {"FAILED", "TIMED_OUT"}]
    shared_catalog_report: dict[str, object] = {
        "status": catalog_status,
        "error": catalog_error,
        "fetched_at": shared_catalog.fetched_at if shared_catalog else None,
        "polymarket_us_final_binary_markets": (
            len(shared_catalog.polymarket_us_final) if shared_catalog else 0
        ),
        "kalshi_events": len(shared_catalog.kalshi_events) if shared_catalog else 0,
    }
    batch_report: dict[str, object] = {
        "status": "BATCH_PARTIAL_FAILURE" if failed else "BATCH_COMPLETE",
        "paper_only": True,
        "execution_enabled": False,
        "shared_catalog": shared_catalog_report,
        "tag_ids": list(tag_ids),
        "kalshi_series_tickers": list(series_tickers),
        "completed_tags": [
            result["tag_id"] for result in per_tag if result["status"] != "FAILED"
        ],
        "failed_tags": [result["tag_id"] for result in failed],
        "bounds": {
            "target": target,
            "kalshi_event_pages": kalshi_event_pages,
            "polymarket_pages": polymarket_pages,
            "global_pages": global_pages,
            "candidate_events": candidate_events,
            "market_pairs": market_pairs,
            "resolved_pairs": resolved_pairs,
        },
        "runs": per_tag,
    }
    print(
        "tag_batch: "
        f"tags={','.join(tag_ids)} runs={len(per_tag)} "
        "paper_only=True execution_enabled=False"
    )
    print(
        f"  shared_catalog: status={catalog_status} "
        f"pm_us_final={shared_catalog_report['polymarket_us_final_binary_markets']} "
        f"kalshi_events={shared_catalog_report['kalshi_events']}"
    )
    for result in per_tag:
        if result["status"] in {"FAILED", "TIMED_OUT"}:
            detail = f"error={result['error_type']}"
        else:
            detail = (
                f"new_labels={result['new_labels']} "
                f"approved={result['approved_labels']} rejected={result['rejected_labels']} "
                f"inconclusive={result['inconclusive_pairs']}"
            )
        print(f"  tag={result['tag_id']} status={result['status']} {detail}")
    print("tag_batch_report=" + json.dumps(batch_report, sort_keys=True))
    return batch_report


async def replay_capture(live: bool, output: str) -> None:
    markets = {
        "kalshi": await KalshiVenue(fixture=not live).list_markets(),
        "polymarket_us": await PolymarketUSVenue(fixture=not live).list_markets(),
    }
    from atlas.models import VenueName

    books = fixture_books() if not live else {}
    count = write_market_bundle(
        {VenueName.KALSHI: markets["kalshi"], VenueName.POLYMARKET_US: markets["polymarket_us"]},
        output,
        books,
    )
    print(f"captured_markets={count} output={output}")


def replay_run(path: str) -> None:
    result = replay_scan(path)
    print(
        "replay="
        + " ".join(f"{key}={value}" for key, value in result.items())
        + f" executable={replay_opportunities(path)}"
    )


async def monitor_live(pair_id: str) -> None:
    pair = await AtlasStore().get_pair(pair_id)
    if pair is None:
        raise ValueError(f"pair not found: {pair_id}")
    await run_pair(pair)


async def _polymarket_us_top_of_book(
    venue: PolymarketUSVenue, market: Market
) -> dict[str, object] | None:
    """Displayed top-of-book depth for one tradeable Polymarket US leg.

    Unlike Gamma, the US gateway publishes a two-sided book, so a paired basket
    can be sized on BOTH legs instead of assuming the Polymarket fill. Depth is
    mapped to the side actually taken: buying YES lifts the top offer
    (``yes_asks``), buying NO sells YES into the top bid (``no_asks``, which the
    adapter already derives as ``1 - bid``).

    Returns None on any book failure so a single missing book degrades that one
    observation to assumed-fill rather than dropping the pair or the scan.
    """
    try:
        book = await venue.get_orderbook(market.venue_market_id)
    except Exception:  # noqa: BLE001 - read-only; a missing book is not fatal
        return None
    return {
        "yes_size": book.yes_asks[0].quantity if book.yes_asks else None,
        "no_size": book.no_asks[0].quantity if book.no_asks else None,
    }


async def gaps_scan(live: bool) -> None:
    """One bounded, read-only radar pass over open twin-shaped candidate pairs.

    Observes and records executable top-of-book gaps; never places orders and
    never touches the approved-pair registry. Pairs are candidates only.
    """
    from atlas.gap_radar import (
        match_twin_shapes,
        observe_pair,
        paper_bankroll_summary,
        polymarket_leg_is_tradeable,
    )

    kalshi = KalshiVenue(fixture=not live)
    globalpm = PolymarketGlobalHistoricalVenue(tag_ids=GAP_RADAR_GLOBAL_TAG_IDS)
    pmus = PolymarketUSVenue(fixture=not live)
    kalshi_markets = await kalshi.list_open_series_markets(GAP_RADAR_KALSHI_SERIES_TICKERS)
    global_markets = await globalpm.list_open_markets() if live else []
    # Polymarket US is the tradeable leg. Its failure must not take the whole
    # scan down: a US outage degrades to the Global-only corpus we already had.
    try:
        pmus_markets = await pmus.list_open_category_markets(GAP_RADAR_PMUS_CATEGORIES)
    except Exception as exc:  # noqa: BLE001 - read-only scope, recorded not raised
        print(f"gap_radar_pmus_scope_failed={type(exc).__name__} degraded_to_global_only=true")
        pmus_markets = []
    polymarket_markets = [*global_markets, *pmus_markets]
    pairs = match_twin_shapes(kalshi_markets, global_markets) + match_twin_shapes(
        kalshi_markets, pmus_markets
    )
    store = AtlasStore()
    recorded = 0
    executable = 0
    tradeable_pairs = 0
    tradeable_executable = 0
    for pair in pairs:
        polymarket_market = pair["polymarket_market"]
        sizes = None
        if polymarket_leg_is_tradeable(polymarket_market):
            tradeable_pairs += 1
            sizes = await _polymarket_us_top_of_book(pmus, polymarket_market)
        observation = observe_pair(pair, polymarket_sizes=sizes)
        if observation is None:
            continue
        if observation["tradeable_venue_pair"] and observation["executable_gap"]:
            tradeable_executable += 1
        await store.save_gap_observation(observation)
        recorded += 1
        if observation["executable_gap"]:
            executable += 1
            # A gap without its depth is not a finding. Live GDP pairs on
            # 2026-08-20 printed a 7.8c gap backed by 0.06 contracts.
            size = observation.get("best_basket_size")
            floors = (
                "" if observation.get("meets_tick_floor") and observation.get("meets_size_floor")
                else " BELOW_FLOOR"
            )
            print(
                f"  GAP {observation['best_gap']} {observation['event_subject']} "
                f"[{observation['best_basket']}] size={size or 'unknown'} "
                f"venue={observation['polymarket_venue']} "
                f"status={observation['verification_status']}{floors}"
            )
    summary = paper_bankroll_summary(await store.all_gap_observations())
    print(
        f"gap_radar: paper_only=true kalshi_markets={len(kalshi_markets)} "
        f"polymarket_markets={len(polymarket_markets)} "
        f"(global={len(global_markets)} pmus={len(pmus_markets)}) "
        f"twin_shaped_pairs={len(pairs)} recorded={recorded} executable_now={executable}"
    )
    # The only numbers that speak to tradeability. Global legs are research.
    print(
        f"gap_radar_tradeable: pairs={tradeable_pairs} "
        f"executable_now={tradeable_executable} venue=polymarket_us"
    )
    print(
        f"paper_bankroll={summary['paper_bankroll']} "
        f"opportunities={summary['distinct_executable_opportunities']} "
        f"(start {summary['start_bankroll']}; candidates only, not proven twins)"
    )


async def record_capacity_samples(
    limit: int = 8, release_window: str | None = None
) -> dict[str, object]:
    """Walk live books for tradeable pairs and PERSIST what was deployable.

    The study's capacity numbers so far were all taken on a quiet market,
    where a gap is whatever dust someone left at a good price. This records
    the same ladder walk with the active release window stamped on it, so a
    CPI or jobs print can be compared against the calm baseline instead of
    argued about.

    Read-only against both venues (two book requests per pair, capped by
    ``limit``) and written to ``capacity_samples`` — never into the frozen
    observation stream. Returns a summary; never raises on a venue failure.
    """
    from atlas.capacity import best_capacity
    from atlas.gap_radar import match_twin_shapes, polymarket_leg_is_tradeable

    kalshi = KalshiVenue(fixture=False)
    pmus = PolymarketUSVenue(fixture=False)
    try:
        kalshi_markets = await kalshi.list_open_series_markets(GAP_RADAR_KALSHI_SERIES_TICKERS)
        pmus_markets = await pmus.list_open_category_markets(GAP_RADAR_PMUS_CATEGORIES)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        print(f"capacity_catalog_failed={type(exc).__name__} window={release_window}")
        return {"samples": 0, "with_capacity": 0, "release_window": release_window}
    pairs = [
        pair
        for pair in match_twin_shapes(kalshi_markets, pmus_markets)
        if polymarket_leg_is_tradeable(pair["polymarket_market"])
    ][:limit]
    store = AtlasStore()
    captured_at = datetime.now(UTC).isoformat()
    samples = 0
    with_capacity = 0
    best_usd = Decimal(0)
    for pair in pairs:
        kalshi_market = pair["kalshi_market"]
        polymarket_market = pair["polymarket_market"]
        try:
            kalshi_book = await kalshi.get_orderbook(kalshi_market.venue_market_id)
            polymarket_book = await pmus.get_orderbook(polymarket_market.venue_market_id)
        except (httpx.HTTPError, OSError, ValueError):
            continue
        result = best_capacity(
            kalshi_book,
            polymarket_book,
            _capacity_direction_legs(pair["shape"]),
            polymarket_market.raw_market_json,
        )
        best = result.get("best") if result.get("supported") else None
        profit = Decimal(best["total_profit_usd"]) if best else Decimal(0)
        if profit > 0:
            with_capacity += 1
            best_usd = max(best_usd, profit)
        samples += 1
        await store.save_capacity_sample(
            {
                "sample_id": str(uuid.uuid4()),
                "captured_at": captured_at,
                "release_window": release_window,
                "kalshi_market_id": kalshi_market.market_id,
                "polymarket_market_id": polymarket_market.market_id,
                "event_subject": pair.get("event_subject"),
                "profitable_contracts": best["profitable_contracts"] if best else "0",
                "total_profit_usd": str(profit),
                "top_of_book_contracts": best.get("top_of_book_contracts") if best else "0",
                "paper_only": True,
                "detail": best or {"supported": False},
            }
        )
    print(
        f"capacity_recorded: window={release_window or 'quiet'} samples={samples} "
        f"with_capacity={with_capacity} best_usd={best_usd}"
    )
    return {
        "samples": samples,
        "with_capacity": with_capacity,
        "release_window": release_window,
        "best_usd": str(best_usd),
    }


def _capacity_direction_legs(shape: str) -> tuple[str, ...]:
    """Basket directions a shape admits, mirroring gap_radar._baskets."""
    from atlas.gap_radar import INVERSE_SHAPE

    if shape == INVERSE_SHAPE:
        return ("kalshi_yes+polymarket_yes", "kalshi_no+polymarket_no")
    return ("kalshi_yes+polymarket_no", "kalshi_no+polymarket_yes")


async def gaps_capacity(limit: int = 12) -> None:
    """Walk the live books of tradeable twin pairs and report real capacity.

    Every recorded gap is a TOP-OF-BOOK number: one price per venue and
    whatever size rests there. This pass fetches the actual ladders and walks
    them, charging each contract its own rung and fees, so the answer is
    dollars-you-could-deploy rather than cents-per-contract on unknown size.

    Read-only and bounded: two order-book requests per pair, capped by
    ``limit``. It records nothing — the 90-day study's observation stream is
    frozen for measurement, so capacity is measured on demand, not folded
    into the recorded corpus mid-flight.
    """
    from atlas.capacity import best_capacity
    from atlas.gap_radar import match_twin_shapes, polymarket_leg_is_tradeable

    kalshi = KalshiVenue(fixture=False)
    pmus = PolymarketUSVenue(fixture=False)
    kalshi_markets = await kalshi.list_open_series_markets(GAP_RADAR_KALSHI_SERIES_TICKERS)
    pmus_markets = await pmus.list_open_category_markets(GAP_RADAR_PMUS_CATEGORIES)
    pairs = [
        pair
        for pair in match_twin_shapes(kalshi_markets, pmus_markets)
        if polymarket_leg_is_tradeable(pair["polymarket_market"])
    ][:limit]
    print(f"gaps_capacity: paper_only=true tradeable_pairs={len(pairs)}")
    total = Decimal(0)
    priced = 0
    for pair in pairs:
        kalshi_market = pair["kalshi_market"]
        polymarket_market = pair["polymarket_market"]
        try:
            kalshi_book = await kalshi.get_orderbook(kalshi_market.venue_market_id)
            polymarket_book = await pmus.get_orderbook(polymarket_market.venue_market_id)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            print(f"  {kalshi_market.venue_market_id}: book_fetch_failed={type(exc).__name__}")
            continue
        result = best_capacity(
            kalshi_book,
            polymarket_book,
            _capacity_direction_legs(pair["shape"]),
            polymarket_market.raw_market_json,
        )
        if not result.get("supported"):
            print(f"  {kalshi_market.venue_market_id}: no_profitable_rung")
            continue
        best = result["best"]
        priced += 1
        total += Decimal(best["total_profit_usd"])
        print(
            f"  {kalshi_market.venue_market_id}: {best['legs']} "
            f"contracts={best['profitable_contracts']} "
            f"top_of_book={best['top_of_book_contracts']} "
            f"levels={best['levels_consumed']} stop={best['stop_reason']} "
            f"profit_usd={best['total_profit_usd']}"
        )
    print(
        f"gaps_capacity_total: pairs_with_capacity={priced} "
        f"deployable_profit_usd={total} (top-of-book snapshot, no orders placed)"
    )


async def gaps_capacity_report() -> None:
    """Deployable capacity by release window, against the quiet baseline."""
    summary = await AtlasStore().capacity_window_summary()
    windows = summary["windows"]
    if not windows:
        print("capacity_report: no samples recorded yet (records during release windows)")
        return
    print(f"{'window':<26}{'samples':>9}{'with cap':>10}{'max $':>10}{'max contracts':>15}")
    print("-" * 70)
    for row in windows:
        name = "(quiet baseline)" if row["window"] == "_quiet" else row["window"]
        print(
            f"{name:<26}{row['samples']:>9}{row['samples_with_capacity']:>10}"
            f"{row['max_profit_usd'] or 0:>10.4f}{row['max_profitable_contracts'] or 0:>15.2f}"
        )


async def gaps_status() -> None:
    from atlas.gap_radar import paper_bankroll_summary

    store = AtlasStore()
    observations = await store.all_gap_observations()
    summary = paper_bankroll_summary(observations)
    print(json.dumps(summary, indent=2))
    for observation in (await store.recent_gap_observations(5)) or []:
        print(
            f"  {observation.get('observed_at', '')[:19]} gap={observation.get('best_gap')} "
            f"{observation.get('event_subject')} status={observation.get('verification_status')}"
        )


async def gaps_study(write: bool = True) -> None:
    """Weekly 90-day-study report from persisted radar observations only.

    Charter: docs/NINETY_DAY_STUDY.md. Regenerable bit-for-bit from the
    database; writing the dated JSON keeps a tamper-evident weekly trail for
    the go/no-go decision at day 90.
    """
    from atlas.study import study_report

    store = AtlasStore()
    observations = await store.all_gap_observations()
    report = study_report(observations)
    print(json.dumps(report, indent=2))
    if write:
        directory = Path("data/study")
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d")
        target = directory / f"study-report-{stamp}.json"
        target.write_text(json.dumps(report, indent=2) + "\n")
        print(f"study_report_written={target}")


async def intel_report(write: bool = True) -> None:
    """Weekly Contract Divergence Report — the contract-intelligence deliverable.

    Read-only over persisted evidence, regenerable bit-for-bit from the
    database. Writes both the JSON (machine trail) and the markdown (the thing
    a person outside this repo actually reads).
    """
    from atlas.intel import divergence_report, render_divergence_markdown

    store = AtlasStore()
    report = await divergence_report(store, clarity_scan=latest_clarity_scan())
    markdown = render_divergence_markdown(report)
    print(markdown)
    if write:
        directory = Path("data/intel")
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d")
        json_target = directory / f"divergence-report-{stamp}.json"
        md_target = directory / f"divergence-report-{stamp}.md"
        json_target.write_text(json.dumps(report, indent=2, default=str) + "\n")
        md_target.write_text(markdown)
        print(f"intel_report_written={md_target}")


CLARITY_VENUES = ("kalshi", "polymarket_us")
# A live grade of every open contract on both venues is a catalog-wide sweep, so
# it carries a per-venue cap by default: the artifact records the cap and marks
# the venue truncated, because a sample presented as a census would be exactly
# the kind of overclaim this score exists to avoid.
CLARITY_MAX_MARKETS_DEFAULT = 2000


def _clarity_venue(name: str, live: bool):
    return KalshiVenue(fixture=not live) if name == "kalshi" else PolymarketUSVenue(fixture=not live)


# A scan of ~2,000 Kalshi markets spans a few hundred series; each is fetched
# once, and the cap bounds a pathological catalog. When the cap binds it is
# recorded in scope — an unfetched series only means the source finding stands.
CLARITY_MAX_SERIES_FETCHES = 400


def _kalshi_series_ticker(market: Market) -> str:
    """The series a Kalshi market belongs to, from its ticker prefix.

    `KXFEDDECISION-26SEP-H0` -> `KXFEDDECISION`. The market payload carries no
    series field; the prefix convention is venue-wide (verified live 2026-08-24
    across macro, metals, and sports series).
    """
    return market.venue_market_id.split("-")[0]


async def _kalshi_series_evidence(
    venue: KalshiVenue, markets: list[Market], cache: dict[str, list[str]]
) -> dict[str, list[str]]:
    """market_id -> series-level settlement source names, best effort.

    Failures leave a market out of the map entirely, which the grader treats as
    no evidence — findings stand, so an outage can only make grades stricter.
    """
    evidence: dict[str, list[str]] = {}
    failures = 0
    for market in markets:
        ticker = _kalshi_series_ticker(market)
        if ticker not in cache:
            if len(cache) >= CLARITY_MAX_SERIES_FETCHES:
                continue
            try:
                cache[ticker] = await venue.get_series_settlement_sources(ticker)
            except (httpx.HTTPError, ValueError):
                failures += 1
                continue
        if cache[ticker]:
            evidence[market.market_id] = cache[ticker]
    if failures:
        print(f"clarity_series_fetch_failures={failures} findings_stand=true")
    return evidence


async def clarity_grade(venue_name: str, market_id: str, live: bool = True) -> None:
    """Grade one market's published settlement text. Read-only, decides nothing."""
    from atlas.clarity import clarity_score, flag_prose

    venue = _clarity_venue(venue_name, live)
    market = await venue.get_market(market_id)
    series_sources: list[str] | None = None
    if venue_name == "kalshi" and live:
        try:
            series_sources = await venue.get_series_settlement_sources(
                _kalshi_series_ticker(market)
            )
        except (httpx.HTTPError, ValueError):
            series_sources = None  # evidence missing -> the stricter grade stands
    grade = clarity_score(market, series_settlement_sources=series_sources)
    print(f"{grade['grade']} ({grade['score']}/100) {grade['market_id']}")
    print(f"  {grade['title']}")
    print(f"  settlement_guarantee={grade['guarantee_status']}")
    for finding in grade["findings"]:
        # A zero-point finding is the grade CAP, not a free pass; label it so.
        cost = f"-{finding['points']}" if finding["points"] else "cap"
        print(f"  {cost} {finding['code']}: {finding['prose']}")
        print(f"      fix: {finding['fix']}")
    for entry in grade["superseded"]:
        print(f"  overruled {entry['code']}: {entry['reason']}")
    for flag in grade["flags"]:
        print(f"  flag {flag}: {flag_prose(flag)} (disclosed, not scored)")
    if not grade["findings"]:
        print("  no findings — every branch this grader checks is published")


async def clarity_scan(live: bool, max_markets: int = CLARITY_MAX_MARKETS_DEFAULT) -> None:
    """Grade the bounded open catalogs of Kalshi and Polymarket US.

    One venue's outage must not take the scan down: a failed catalog fetch is
    recorded as a degraded venue and the other venue still gets graded. Writes
    the dated JSON artifact the divergence report reads.
    """
    from atlas.clarity import clarity_scan_report, render_scan_summary

    markets: list[Market] = []
    degraded: list[str] = []
    truncated: list[str] = []
    series_sources: dict[str, list[str]] = {}
    series_cache: dict[str, list[str]] = {}
    for venue_name in CLARITY_VENUES:
        venue = _clarity_venue(venue_name, live)
        try:
            fetched = await venue.list_markets()
        except Exception as exc:  # noqa: BLE001 - read-only scope, recorded not raised
            print(f"clarity_scan_fetch_failed={venue_name} error={type(exc).__name__}")
            degraded.append(venue_name)
            continue
        if max_markets and len(fetched) > max_markets:
            truncated.append(venue_name)
            fetched = fetched[:max_markets]
        if venue_name == "kalshi" and live:
            series_sources.update(
                await _kalshi_series_evidence(venue, fetched, series_cache)
            )
        markets.extend(fetched)
    report = clarity_scan_report(
        markets,
        degraded_venues=degraded,
        series_sources=series_sources,
        scope={
            "venues": list(CLARITY_VENUES),
            "catalog": "open markets via list_markets()",
            "live": bool(live),
            "max_markets_per_venue": max_markets or None,
            "truncated_venues": truncated,
            "kalshi_series_fetched": len(series_cache),
            "kalshi_series_fetch_cap": CLARITY_MAX_SERIES_FETCHES,
        },
    )
    for line in render_scan_summary(report):
        print(line)
    directory = Path("data/clarity")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    target = directory / f"clarity-scan-{stamp}.json"
    target.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"clarity_scan_written={target}")


def latest_clarity_scan(directory: str = "data/clarity") -> dict | None:
    """The most recent dated clarity scan on disk, or ``None`` when none exists.

    Absence is absence: the divergence report omits its clarity section rather
    than rendering an empty one that reads like a clean bill of health.
    """
    paths = sorted(Path(directory).glob("clarity-scan-*.json"))
    if not paths:
        return None
    try:
        return json.loads(paths[-1].read_text())
    except (OSError, ValueError) as exc:
        print(f"clarity_scan_unreadable={paths[-1]} error={type(exc).__name__}")
        return None


def main() -> None:
    # The authenticated order-book streams read their credentials from the
    # process environment; worker.py already does this, but the continuous
    # monitor runs through this entry point, so without it a .env holding the
    # venue credentials never reaches run_pair. The path is explicit rather
    # than discovered: bare load_dotenv() resolves relative to the caller's
    # stack/CWD, which is not something a launchd-managed service should
    # depend on. Values are never logged.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    parser = argparse.ArgumentParser(prog="atlas")
    sub = parser.add_subparsers(dest="command", required=True)
    markets = sub.add_parser("markets")
    sync = markets.add_subparsers(dest="action", required=True).add_parser("sync")
    sync.add_argument("--live", action="store_true")
    books = sub.add_parser("books")
    inspect = books.add_subparsers(dest="action", required=True).add_parser("inspect")
    inspect.add_argument("venue")
    inspect.add_argument("market_id")
    opps = sub.add_parser("opportunities")
    opps.add_subparsers(dest="action", required=True).add_parser("demo")
    agent = sub.add_parser("agent")
    agent_sub = agent.add_subparsers(dest="action", required=True)
    research_agent = agent_sub.add_parser("research")
    research_agent.add_argument("--live", action="store_true")
    research_agent.add_argument("--replay")
    monitor = sub.add_parser("monitor")
    monitor_sub = monitor.add_subparsers(dest="action", required=True)
    monitor_sub.add_parser("once")
    live = monitor_sub.add_parser("live")
    live.add_argument("pair_id")
    pairs = sub.add_parser("pairs")
    pairs_sub = pairs.add_subparsers(dest="action", required=True)
    approve = pairs_sub.add_parser("approve")
    approve.add_argument("kalshi_id")
    approve.add_argument("polymarket_id")
    approve.add_argument("--approved-by", default="operator")
    scan = pairs_sub.add_parser("scan")
    scan.add_argument("--live", action="store_true")
    watch = pairs_sub.add_parser("watch")
    watch.add_argument("--live", action="store_true")
    watch.add_argument("--interval", type=int, default=300)
    watch.add_argument("--backfill-interval", type=int, default=86_400)
    shadow = pairs_sub.add_parser(
        "shadow",
        help="continuously observe live cross-venue shadow pairs; never executes",
    )
    shadow.add_argument("--live", action="store_true", required=True)
    shadow.add_argument("--interval", type=int, default=60)
    shadow.add_argument("--limit", type=int, default=10)
    candidates = pairs_sub.add_parser("candidates")
    candidates.add_argument("--live", action="store_true")
    candidates.add_argument("--limit", type=int, default=25)
    frontier_parser = pairs_sub.add_parser(
        "frontier",
        help="rank blocked pairs by proximity to approval; flag moved venue text",
    )
    frontier_parser.add_argument("--limit", type=int, default=200)
    gaps = sub.add_parser("gaps", help="paper-only cross-venue price-gap radar")
    gaps_sub = gaps.add_subparsers(dest="action", required=True)
    gaps_scan_parser = gaps_sub.add_parser("scan")
    gaps_scan_parser.add_argument("--live", action="store_true")
    gaps_capacity_parser = gaps_sub.add_parser(
        "capacity",
        help="walk live books of tradeable pairs and report deployable capacity",
    )
    gaps_capacity_parser.add_argument("--limit", type=int, default=12)
    gaps_capacity_parser.add_argument(
        "--record",
        action="store_true",
        help="persist the walk to capacity_samples (stamped with any active release window)",
    )
    gaps_sub.add_parser("capacity-report", help="deployable capacity by release window")
    gaps_study_parser = gaps_sub.add_parser(
        "study", help="90-day study report (docs/NINETY_DAY_STUDY.md)"
    )
    gaps_study_parser.add_argument(
        "--no-write", action="store_true", help="print only; skip the dated JSON artifact"
    )
    gaps_sub.add_parser("status")
    intel = sub.add_parser(
        "intel", help="contract-intelligence reports over persisted evidence"
    )
    intel_sub = intel.add_subparsers(dest="action", required=True)
    intel_report_parser = intel_sub.add_parser(
        "report", help="weekly Contract Divergence Report (markdown + JSON)"
    )
    intel_report_parser.add_argument(
        "--no-write", action="store_true", help="print without writing data/intel/"
    )
    clarity = sub.add_parser(
        "clarity", help="Settlement Clarity Score — deterministic A-F grade of venue fine print"
    )
    clarity_sub = clarity.add_subparsers(dest="action", required=True)
    clarity_grade_parser = clarity_sub.add_parser(
        "grade", help="grade one market's published settlement text"
    )
    clarity_grade_parser.add_argument("--venue", choices=CLARITY_VENUES, required=True)
    clarity_grade_parser.add_argument("--market", required=True, help="venue market id or slug")
    clarity_grade_parser.add_argument(
        "--fixture", action="store_true", help="grade the bundled fixture market instead of live"
    )
    clarity_scan_parser = clarity_sub.add_parser(
        "scan", help="grade the bounded open catalogs and write data/clarity/"
    )
    clarity_scan_parser.add_argument("--live", action="store_true")
    clarity_scan_parser.add_argument(
        "--max-markets",
        type=int,
        default=CLARITY_MAX_MARKETS_DEFAULT,
        help="per-venue cap; 0 grades the whole bounded sweep",
    )
    learning = sub.add_parser("learning")
    learning_sub = learning.add_subparsers(dest="action", required=True)
    export = learning_sub.add_parser("export")
    export.add_argument("--output", default="data/training/atlas.jsonl")
    export.add_argument("--eval-output")
    export.add_argument("--eval-ratio", type=float, default=0.2)
    learning_sub.add_parser("status")
    learning_sub.add_parser("readiness")
    learning_sub.add_parser("reconcile")
    backfill = learning_sub.add_parser("backfill")
    backfill.add_argument("--live", action="store_true", required=True)
    backfill.add_argument("--target", type=int, default=50)
    backfill.add_argument("--kalshi-event-pages", type=int, default=100)
    backfill.add_argument("--polymarket-pages", type=int, default=20)
    backfill.add_argument("--global-pages", type=int, default=5)
    backfill.add_argument(
        "--global-tag-ids",
        action="append",
        metavar="TAG_ID[,TAG_ID...]",
        help="bounded Polymarket Global tag-ID override; repeat or comma-separate values",
    )
    backfill.add_argument(
        "--kalshi-series-tickers",
        action="append",
        metavar="SERIES[,SERIES...]",
        help=(
            "bounded Kalshi settled-series scan (e.g. KXFEDDECISION,KXFED) so macro "
            "events beyond recent-first paging reach the candidate pool"
        ),
    )
    backfill.add_argument(
        "--kalshi-event-ticker-filter",
        metavar="REGEX",
        help=(
            "explicit-harvest re.search filter applied only to the requested "
            "--kalshi-series-tickers events before they join the candidate pool "
            "(e.g. '12$' keeps just the noon-ET hourly crypto events); never "
            "applied to the general recent scan"
        ),
    )
    backfill.add_argument(
        "--polymarket-us-event-slugs",
        action="append",
        metavar="SLUG[,SLUG...]",
        help=(
            "explicit-harvest tool: bounded Polymarket US event-slug lookups "
            "(e.g. uscpi-july-yoy-2026-08-12) so settled macro ladders buried "
            "under the ~400k-row closed sweep reach the final-binary pool; "
            "never part of scheduled defaults; repeat or comma-separate values"
        ),
    )
    backfill.add_argument(
        "--polymarket-global-event-slugs",
        action="append",
        metavar="SLUG[,SLUG...]",
        help=(
            "explicit-harvest tool: bounded Polymarket Global (Gamma) event-slug "
            "lookups for settled macro ladders with no known tag ID (e.g. "
            "core-pce-mom-june-2026-20260702035317774); never part of scheduled "
            "defaults; repeat or comma-separate values"
        ),
    )
    backfill.add_argument("--candidate-events", type=int, default=100)
    backfill.add_argument("--market-pairs", type=int, default=2_000)
    backfill.add_argument("--resolved-pairs", type=int, default=250)
    batch = learning_sub.add_parser(
        "backfill-batch",
        help="run bounded paper-only historical probes one Global tag at a time",
    )
    batch.add_argument("--live", action="store_true", required=True)
    batch.add_argument("--target", type=int, default=1)
    batch.add_argument("--kalshi-event-pages", type=int, default=BATCH_MAX_KALSHI_EVENT_PAGES)
    batch.add_argument("--polymarket-pages", type=int, default=BATCH_MAX_POLYMARKET_PAGES)
    batch.add_argument("--global-pages", type=int, default=1)
    batch.add_argument(
        "--global-tag-ids",
        action="append",
        metavar="TAG_ID[,TAG_ID...]",
        help=(
            "bounded tag override; defaults to crypto, weather, and commodities; "
            "repeat or comma-separate values"
        ),
    )
    batch.add_argument(
        "--kalshi-series-tickers",
        action="append",
        metavar="SERIES[,SERIES...]",
        help=(
            "bounded Kalshi settled-series scan; defaults to the FOMC decision series "
            "(KXFEDDECISION, KXFED); repeat or comma-separate values"
        ),
    )
    batch.add_argument("--candidate-events", type=int, default=BATCH_MAX_CANDIDATE_EVENTS)
    batch.add_argument("--market-pairs", type=int, default=BATCH_MAX_MARKET_PAIRS)
    batch.add_argument("--resolved-pairs", type=int, default=BATCH_MAX_RESOLVED_PAIRS)
    sub.add_parser(
        "prune",
        help=(
            "delete stale operational rows (order books >30d; newest-20 reports/scans/runs); "
            "never touches evidence or labels; a one-time manual VACUUM reclaims disk"
        ),
    )
    replay = sub.add_parser("replay")
    replay_sub = replay.add_subparsers(dest="action", required=True)
    capture = replay_sub.add_parser("capture")
    capture.add_argument("--live", action="store_true")
    capture.add_argument("--output", default="data/replays/latest-markets.json")
    run = replay_sub.add_parser("run")
    run.add_argument("--input", required=True)
    args = parser.parse_args()
    if args.command == "markets":
        asyncio.run(markets_sync(fixture=not args.live))
    elif args.command == "books":
        asyncio.run(books_inspect(args.venue, args.market_id))
    elif args.command == "opportunities":
        asyncio.run(opportunities_demo())
    elif args.command == "agent":
        mode = "live" if args.live else "replay" if args.replay else "fixture"
        asyncio.run(agent_research(mode, args.replay))
    elif args.command == "monitor":
        if args.action == "once":
            asyncio.run(monitor_once())
        else:
            asyncio.run(monitor_live(args.pair_id))
    elif args.command == "pairs" and args.action == "scan":
        asyncio.run(scan_pairs(args.live))
    elif args.command == "pairs" and args.action == "watch":
        asyncio.run(
            watch_pairs(
                args.live,
                max(args.interval, 10),
                max(args.backfill_interval, 60),
            )
        )
    elif args.command == "pairs" and args.action == "shadow":
        asyncio.run(shadow_watch(args.interval, args.limit))
    elif args.command == "pairs" and args.action == "candidates":
        asyncio.run(candidate_pairs(args.live, max(args.limit, 1)))
    elif args.command == "pairs" and args.action == "frontier":
        asyncio.run(approval_frontier_report(max(args.limit, 1)))
    elif args.command == "gaps" and args.action == "scan":
        asyncio.run(gaps_scan(args.live))
    elif args.command == "gaps" and args.action == "study":
        asyncio.run(gaps_study(write=not args.no_write))
    elif args.command == "gaps" and args.action == "capacity":
        if args.record:
            from atlas.release_calendar import active_release_window

            asyncio.run(
                record_capacity_samples(
                    limit=args.limit,
                    release_window=active_release_window(datetime.now(UTC)),
                )
            )
        else:
            asyncio.run(gaps_capacity(limit=args.limit))
    elif args.command == "gaps" and args.action == "capacity-report":
        asyncio.run(gaps_capacity_report())
    elif args.command == "intel" and args.action == "report":
        asyncio.run(intel_report(write=not args.no_write))
    elif args.command == "clarity" and args.action == "grade":
        asyncio.run(clarity_grade(args.venue, args.market, live=not args.fixture))
    elif args.command == "clarity" and args.action == "scan":
        asyncio.run(clarity_scan(args.live, max(args.max_markets, 0)))
    elif args.command == "gaps" and args.action == "status":
        asyncio.run(gaps_status())
    elif args.command == "learning" and args.action == "export":
        asyncio.run(learning_export(args.output, args.eval_output, args.eval_ratio))
    elif args.command == "learning" and args.action == "status":
        asyncio.run(learning_status())
    elif args.command == "learning" and args.action == "readiness":
        asyncio.run(learning_readiness())
    elif args.command == "learning" and args.action == "reconcile":
        asyncio.run(learning_reconcile())
    elif args.command == "learning" and args.action == "backfill":
        try:
            global_tag_ids = _parse_global_tag_ids(args.global_tag_ids)
            kalshi_series_tickers = _parse_kalshi_series_tickers(args.kalshi_series_tickers)
            kalshi_event_ticker_filter = _parse_kalshi_event_ticker_filter(
                args.kalshi_event_ticker_filter
            )
            polymarket_us_event_slugs = _parse_polymarket_us_event_slugs(
                args.polymarket_us_event_slugs
            )
            polymarket_global_event_slugs = _parse_polymarket_global_event_slugs(
                args.polymarket_global_event_slugs
            )
        except ValueError as exc:
            parser.error(str(exc))
        asyncio.run(
            learning_backfill(
                args.live,
                max(args.target, 1),
                max(args.kalshi_event_pages, 1),
                max(args.polymarket_pages, 1),
                max(args.global_pages, 1),
                max(args.candidate_events, 1),
                max(args.market_pairs, 1),
                max(args.resolved_pairs, 1),
                global_tag_ids,
                kalshi_series_tickers,
                kalshi_event_ticker_filter=kalshi_event_ticker_filter,
                polymarket_us_event_slugs=polymarket_us_event_slugs,
                polymarket_global_event_slugs=polymarket_global_event_slugs,
            )
        )
    elif args.command == "learning" and args.action == "backfill-batch":
        try:
            global_tag_ids = _parse_batch_tag_ids(args.global_tag_ids)
            kalshi_series_tickers = _parse_batch_series_tickers(args.kalshi_series_tickers)
            _validate_batch_limits(
                target=args.target,
                kalshi_event_pages=args.kalshi_event_pages,
                polymarket_pages=args.polymarket_pages,
                global_pages=args.global_pages,
                candidate_events=args.candidate_events,
                market_pairs=args.market_pairs,
                resolved_pairs=args.resolved_pairs,
            )
        except ValueError as exc:
            parser.error(str(exc))
        asyncio.run(
            learning_backfill_batch(
                args.live,
                args.target,
                args.kalshi_event_pages,
                args.polymarket_pages,
                args.global_pages,
                args.candidate_events,
                args.market_pairs,
                args.resolved_pairs,
                global_tag_ids,
                kalshi_series_tickers,
            )
        )
    elif args.command == "prune":
        asyncio.run(prune_stale_data())
    elif args.command == "replay" and args.action == "capture":
        asyncio.run(replay_capture(args.live, args.output))
    elif args.command == "replay" and args.action == "run":
        replay_run(args.input)
    else:
        asyncio.run(approve_live_pair(args.kalshi_id, args.polymarket_id, args.approved_by))


if __name__ == "__main__":
    main()
