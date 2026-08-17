import argparse
import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

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
from atlas.learning import export_learning_splits, export_training_jsonl
from atlas.live_monitor import run_pair
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
# Elections/House stay out: margin-of-victory spreads produce no twin shapes.
GAP_RADAR_KALSHI_SERIES_TICKERS = BATCH_DEFAULT_KALSHI_SERIES_TICKERS + (
    "KXU3",
    "KXISMPMI",
    "KXUSISMSERV",
)
GAP_RADAR_GLOBAL_TAG_IDS = (
    "100196",  # Fed Rates
    "101701",  # CPI
    "702",  # Inflation
    "993",  # jobs
    "1624",  # unemployment
    "370",  # GDP
    "105113",  # ISM manufacturing + services
    "105533",  # Core PCE
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
    store = AtlasStore()
    for venue in (KalshiVenue(fixture=fixture), PolymarketUSVenue(fixture=fixture)):
        markets = await venue.list_markets()
        await store.save_markets(markets)
        print(f"{venue.name}: synced {len(markets)} market(s)")
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
        fees=Decimal("0.83"),
        slippage=Decimal("0.20"),
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
        # the bounded series scan so open FOMC/CPI markets enter the queue.
        series_markets = await kalshi_venue.list_open_series_markets(
            BATCH_DEFAULT_KALSHI_SERIES_TICKERS
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
    pairs = scan_market_pairs(kalshi_markets, polymarket_markets)
    approved = [
        pair for pair in pairs if pair.status.value in {"APPROVED_EQUIVALENT", "APPROVED_INVERSE"}
    ]
    review = [pair for pair in pairs if pair.status.value == "REVIEW_REQUIRED"]
    catalog_report = compatibility_report(
        kalshi_markets, [*polymarket_markets, *global_open_markets]
    )
    catalog_report["polymarket_global_open_markets"] = len(global_open_markets)
    catalog_report["polymarket_global_open_tag_ids"] = list(
        LIVE_GLOBAL_TAG_IDS if global_open_markets else ()
    )
    if weather_enrichment is not None:
        catalog_report["weather_rule_enrichment"] = weather_enrichment
    if shared_rule_enrichment is not None:
        catalog_report["shared_rule_enrichment"] = shared_rule_enrichment
    await store.save_catalog_report(catalog_report)
    await store.save_settlement_candidates(
        catalog_report.get("settlement_discovery", {}).get("rankings", [])
    )
    reviews = review_market_pairs(kalshi_markets, polymarket_markets)
    identity_candidates = structured_identity_candidates(kalshi_markets, polymarket_markets)
    review_candidates = _deduplicate_candidates([*reviews, *identity_candidates])
    if not review_candidates:
        review_candidates = propose_market_pairs(kalshi_markets, polymarket_markets)
    await store.save_candidate_proposals(review_candidates[:25])
    validation_capture = await capture_validation_universe(
        store, kalshi_markets, polymarket_markets, approved, review_candidates
    )
    validation_reconciliation = await reconcile_validation_cases(
        store, kalshi_venue, polymarket_venue
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


async def watch_pairs(live: bool, interval: int, backfill_interval: int = 86_400) -> None:
    monitors: dict[str, asyncio.Task] = {}
    store = AtlasStore()
    while True:
        approved = await _safe_scan_pairs(live)
        if live:
            for pair in approved:
                if pair.pair_id not in monitors:
                    monitors[pair.pair_id] = asyncio.create_task(run_pair(pair))
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
        await asyncio.sleep(delay)
        slept += delay
        try:
            await gaps_scan(live=True)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            print(f"gap_radar_scan_failed={type(exc).__name__} retry_on_next_interval=true")


async def _historical_backfill_due(store: AtlasStore, interval: int) -> bool:
    latest = await store.latest_historical_backfill()
    if latest is None:
        return True
    completed_at = latest.get("completed_at")
    if not completed_at:
        return True
    completed = datetime.fromisoformat(str(completed_at))
    return datetime.now(UTC) - completed >= timedelta(seconds=max(interval, 60))


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


async def gaps_scan(live: bool) -> None:
    """One bounded, read-only radar pass over open twin-shaped candidate pairs.

    Observes and records executable top-of-book gaps; never places orders and
    never touches the approved-pair registry. Pairs are candidates only.
    """
    from atlas.gap_radar import match_twin_shapes, observe_pair, paper_bankroll_summary

    kalshi = KalshiVenue(fixture=not live)
    globalpm = PolymarketGlobalHistoricalVenue(tag_ids=GAP_RADAR_GLOBAL_TAG_IDS)
    kalshi_markets = await kalshi.list_open_series_markets(GAP_RADAR_KALSHI_SERIES_TICKERS)
    polymarket_markets = await globalpm.list_open_markets() if live else []
    pairs = match_twin_shapes(kalshi_markets, polymarket_markets)
    store = AtlasStore()
    recorded = 0
    executable = 0
    for pair in pairs:
        observation = observe_pair(pair)
        if observation is None:
            continue
        await store.save_gap_observation(observation)
        recorded += 1
        if observation["executable_gap"]:
            executable += 1
            print(
                f"  GAP {observation['best_gap']} {observation['event_subject']} "
                f"[{observation['best_basket']}] status={observation['verification_status']}"
            )
    summary = paper_bankroll_summary(await store.all_gap_observations())
    print(
        f"gap_radar: paper_only=true kalshi_markets={len(kalshi_markets)} "
        f"polymarket_markets={len(polymarket_markets)} twin_shaped_pairs={len(pairs)} "
        f"recorded={recorded} executable_now={executable}"
    )
    print(
        f"paper_bankroll={summary['paper_bankroll']} "
        f"opportunities={summary['distinct_executable_opportunities']} "
        f"(start {summary['start_bankroll']}; candidates only, not proven twins)"
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


def main() -> None:
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
    gaps = sub.add_parser("gaps", help="paper-only cross-venue price-gap radar")
    gaps_sub = gaps.add_subparsers(dest="action", required=True)
    gaps_scan_parser = gaps_sub.add_parser("scan")
    gaps_scan_parser.add_argument("--live", action="store_true")
    gaps_sub.add_parser("status")
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
    elif args.command == "gaps" and args.action == "scan":
        asyncio.run(gaps_scan(args.live))
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
    elif args.command == "replay" and args.action == "capture":
        asyncio.run(replay_capture(args.live, args.output))
    elif args.command == "replay" and args.action == "run":
        replay_run(args.input)
    else:
        asyncio.run(approve_live_pair(args.kalshi_id, args.polymarket_id, args.approved_by))


if __name__ == "__main__":
    main()
