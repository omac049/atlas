from decimal import Decimal

import pytest

from atlas.backfill import (
    _historical_label,
    backfill_historical_validation,
    historical_event_candidates,
    prefetch_shared_backfill_catalog,
)
from atlas.cli import _historical_backfill_due
from atlas.models import MarketStatus, MatchStatus
from atlas.storage import AtlasStore
from atlas.venues.fixtures import fixture_markets
from atlas.verification import verify_equivalence


class HistoricalKalshiVenue:
    def __init__(self, markets):
        self.markets = markets

    async def list_settled_events(self, max_pages=100):
        return [
            {
                "event_ticker": "KXFED-SEP26",
                "title": "Federal Reserve raises federal funds target September 2026",
                "sub_title": "25 basis points",
            }
        ]

    async def list_settled_event_markets(self, event_ticker):
        return self.markets


class HistoricalPolymarketVenue:
    def __init__(self, markets):
        self.markets = markets

    async def list_closed_markets(self, max_pages=20):
        return self.markets

    async def get_terminal_settlement_evidence(self, market_id):
        return {
            "source": "terminal_market_book",
            "settlement": "1",
            "state": "MARKET_STATE_EXPIRED",
        }


@pytest.mark.asyncio
async def test_historical_backfill_creates_positive_and_negative_labels(tmp_path):
    markets = fixture_markets()
    exact = markets["kalshi"][0].model_copy(deep=True)
    exact.status = MarketStatus.SETTLED
    exact.raw_market_json["result"] = "yes"
    exact.raw_market_json["event_ticker"] = "KXFED-SEP26"

    mismatch = exact.model_copy(deep=True)
    mismatch.market_id = "kalshi:KXFED-SEP26-T50"
    mismatch.venue_market_id = "KXFED-SEP26-T50"
    mismatch.raw_market_json["result"] = "no"

    polymarket = markets["polymarket_us"][0].model_copy(deep=True)
    polymarket.status = MarketStatus.CLOSED
    polymarket.raw_market_json["question"] = polymarket.title

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    report = await backfill_historical_validation(
        store,
        HistoricalKalshiVenue([exact, mismatch]),
        HistoricalPolymarketVenue([polymarket]),
        target_labels=2,
    )

    assert report["status"] == "MILESTONE_COMPLETE"
    assert report["new_labels"] == 2
    assert report["approved_labels"] == 1
    assert report["rejected_labels"] == 1
    assert report["labels_remaining"] == 0
    assert report["venue_coverage"]["polymarket_us"] == {
        "closed_markets": 1,
        "final_binary_markets": 1,
        "catalog_scope": "all",
    }
    assert (await store.validation_summary())["trusted_labels"] == 2
    assert (await store.latest_historical_backfill())["status"] == "MILESTONE_COMPLETE"


@pytest.mark.asyncio
async def test_historical_backfill_scans_requested_kalshi_series(tmp_path):
    class SeriesKalshiVenue:
        def __init__(self):
            self.captured_series = None

        async def list_settled_events(self, max_pages=100, series_tickers=None):
            self.captured_series = series_tickers
            return [
                {
                    "event_ticker": "KXFEDDECISION-26JUL",
                    "title": "Fed decision in July?",
                    "sub_title": "On Jul 29, 2026",
                }
            ]

        async def list_settled_event_markets(self, event_ticker):
            return []

    venue = SeriesKalshiVenue()
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    report = await backfill_historical_validation(
        store,
        venue,
        HistoricalPolymarketVenue([]),
        target_labels=1,
        kalshi_series_tickers=("KXFEDDECISION", "KXFED"),
    )

    assert venue.captured_series == ("KXFEDDECISION", "KXFED")
    assert report["kalshi_series_tickers"] == ["KXFEDDECISION", "KXFED"]
    assert report["kalshi_series_event_counts"] == {"KXFEDDECISION": 1, "KXFED": 0}
    assert report["blockers"]["KALSHI_SERIES_EVENT_SCAN_EMPTY"] == 1
    assert report["paper_only"] is True


@pytest.mark.asyncio
async def test_requested_series_events_bypass_the_lexical_candidate_gate(tmp_path):
    """'Fed decision in July?' shares too few tokens with 'increase interest rates by
    25 bps after the July 2026 meeting' to match lexically; an explicitly requested
    series must still reach deterministic verification."""
    kalshi_market = fixture_markets()["kalshi"][0].model_copy(deep=True)
    kalshi_market.status = MarketStatus.SETTLED
    kalshi_market.raw_market_json["result"] = "yes"
    kalshi_market.raw_market_json["event_ticker"] = "KXFEDDECISION-26JUL"

    class SeriesKalshiVenue:
        async def list_settled_events(self, max_pages=100, series_tickers=None):
            return [
                {
                    "event_ticker": "KXFEDDECISION-26JUL",
                    "title": "Fed decision in July?",
                    "sub_title": "On Jul 29, 2026",
                }
            ]

        async def list_settled_event_markets(self, event_ticker):
            return [kalshi_market]

    polymarket = fixture_markets()["polymarket_us"][0].model_copy(deep=True)
    polymarket.status = MarketStatus.CLOSED
    polymarket.title = "Will the Fed increase interest rates by 25 bps after the July 2026 meeting?"
    polymarket.raw_market_json["question"] = polymarket.title

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    report = await backfill_historical_validation(
        store,
        SeriesKalshiVenue(),
        HistoricalPolymarketVenue([polymarket]),
        target_labels=1,
        kalshi_series_tickers=("KXFEDDECISION",),
    )

    assert report["cross_venue_event_candidates"] == 1
    assert report["market_pairs_reviewed"] == 1
    assert report["kalshi_series_event_counts"] == {"KXFEDDECISION": 1}
    assert "NO_CROSS_VENUE_SETTLED_EVENT_OVERLAP" not in report["blockers"]


@pytest.mark.asyncio
async def test_market_pair_cap_keeps_priority_pairs_not_arrival_order(tmp_path):
    """Verification runs on every constructed pair before the cap, so the cap
    must truncate the PRIORITY-sorted list: with the old arrival-order cut, one
    venue's ladder could crowd every labelable pair out of the reviewed window
    (observed live 2026-08-14: 3000/3000 inconclusive on the payrolls/GDP
    harvest while the slug-targeted twin pairs sat beyond the cap)."""
    markets = fixture_markets()
    exact = markets["kalshi"][0].model_copy(deep=True)
    exact.status = MarketStatus.SETTLED
    exact.raw_market_json["result"] = "yes"
    exact.raw_market_json["event_ticker"] = "KXFED-SEP26"

    review_only = markets["polymarket_us"][0].model_copy(deep=True)
    review_only.status = MarketStatus.CLOSED
    review_only.market_id = "polymarket_us:review-only"
    review_only.venue_market_id = "review-only"
    review_only.threshold = Decimal(50)
    review_only.raw_market_json["question"] = review_only.title

    matching = markets["polymarket_us"][0].model_copy(deep=True)
    matching.status = MarketStatus.CLOSED
    matching.raw_market_json["question"] = matching.title

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    report = await backfill_historical_validation(
        store,
        HistoricalKalshiVenue([exact]),
        # Arrival order puts the review-shaped pair first; the cap of 1 must
        # keep the approvable pair anyway.
        HistoricalPolymarketVenue([review_only, matching]),
        target_labels=1,
        max_market_pairs=1,
    )

    assert report["blockers"]["HISTORICAL_MARKET_PAIR_CAP_APPLIED"] == 1
    assert report["approved_labels"] == 1


async def _seed_persisted_rejections(store, subject, count, start=0):
    for index in range(start, start + count):
        pair_id = f"historical:seed-{index}"
        await store.save_validation_case(
            {
                "pair_id": pair_id,
                "source_kind": "HISTORICAL_BACKFILL",
                "decision_status": "REVIEW_REQUIRED",
                "guarantee_a": "UNKNOWN",
                "guarantee_b": "UNKNOWN",
                "tracking_status": "RESOLVED",
                "payload": {
                    "pair": {"decision": {"fingerprint_a": {"event_subject": subject}}}
                },
            }
        )
        await store.save_validation_outcome(
            {
                "pair_id": pair_id,
                "resolved_at": "2026-08-13T00:00:00+00:00",
                "relationship_status": "DIVERGED",
                "outcome_a": "yes",
                "outcome_b": "no",
                "trusted_label": "REJECTED",
            }
        )


@pytest.mark.asyncio
async def test_review_rejection_event_cap_holds_across_runs(tmp_path):
    """The owner-signed 5-per-event bound is cross-run: a fresh backfill run must
    count the rejections an event already holds in the store, not restart at zero
    (the July 2026 CPI event reached 6 through exactly this leak)."""
    markets = fixture_markets()
    mismatch = markets["kalshi"][0].model_copy(deep=True)
    mismatch.threshold = Decimal(50)
    mismatch.status = MarketStatus.SETTLED
    mismatch.market_id = "kalshi:KXFED-SEP26-T50"
    mismatch.venue_market_id = "KXFED-SEP26-T50"
    mismatch.raw_market_json["result"] = "no"
    mismatch.raw_market_json["event_ticker"] = "KXFED-SEP26"

    polymarket = markets["polymarket_us"][0].model_copy(deep=True)
    polymarket.status = MarketStatus.CLOSED
    polymarket.raw_market_json["question"] = polymarket.title

    review_pair = verify_equivalence(mismatch, polymarket)
    assert review_pair.status is MatchStatus.REVIEW_REQUIRED
    subject = str(review_pair.decision.fingerprint_a.event_subject)

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await _seed_persisted_rejections(store, subject, 5)
    assert await store.review_rejection_counts_by_subject() == {subject: 5}

    report = await backfill_historical_validation(
        store,
        HistoricalKalshiVenue([mismatch]),
        HistoricalPolymarketVenue([polymarket]),
        target_labels=50,
    )

    assert report["rejected_labels"] == 0
    assert report["new_labels"] == 0
    assert report["blockers"]["REVIEW_REJECTION_EVENT_CAP_APPLIED"] == 1


@pytest.mark.asyncio
async def test_review_rejection_event_cap_allows_room_below_the_bound(tmp_path):
    """Seeding must not over-block: an event holding fewer than five persisted
    rejections still accepts new ones up to the bound."""
    markets = fixture_markets()
    mismatch = markets["kalshi"][0].model_copy(deep=True)
    mismatch.threshold = Decimal(50)
    mismatch.status = MarketStatus.SETTLED
    mismatch.market_id = "kalshi:KXFED-SEP26-T50"
    mismatch.venue_market_id = "KXFED-SEP26-T50"
    mismatch.raw_market_json["result"] = "no"
    mismatch.raw_market_json["event_ticker"] = "KXFED-SEP26"

    polymarket = markets["polymarket_us"][0].model_copy(deep=True)
    polymarket.status = MarketStatus.CLOSED
    polymarket.raw_market_json["question"] = polymarket.title

    review_pair = verify_equivalence(mismatch, polymarket)
    subject = str(review_pair.decision.fingerprint_a.event_subject)

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await _seed_persisted_rejections(store, subject, 4)

    report = await backfill_historical_validation(
        store,
        HistoricalKalshiVenue([mismatch]),
        HistoricalPolymarketVenue([polymarket]),
        target_labels=50,
    )

    assert report["rejected_labels"] == 1
    assert "REVIEW_REJECTION_EVENT_CAP_APPLIED" not in report["blockers"]


@pytest.mark.asyncio
async def test_global_event_slug_harvest_reaches_the_final_pool(tmp_path):
    """The Gamma event-slug door mirrors the US one: slug-fetched markets join
    the global source's closed pool (deduped) and the report records the
    requested slugs, so tag-less settled ladders (June core PCE) are reachable
    before Kalshi's pruning window closes."""
    slug_market = fixture_markets()["polymarket_us"][0].model_copy(deep=True)
    slug_market.status = MarketStatus.CLOSED
    slug_market.market_id = "polymarket_global:slug-market"
    slug_market.venue_market_id = "slug-market"
    slug_market.raw_market_json["question"] = slug_market.title

    class SlugGlobalVenue:
        catalog_scope = "tagged:none"

        def __init__(self):
            self.requested: list[str] = []

        async def list_closed_markets(self, max_pages=20):
            return []

        async def list_event_markets(self, event_slug):
            self.requested.append(event_slug)
            return [slug_market]

        async def get_terminal_settlement_evidence(self, market_id):
            return {
                "source": "terminal_market_book",
                "settlement": "1",
                "state": "MARKET_STATE_EXPIRED",
            }

    venue = SlugGlobalVenue()
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    report = await backfill_historical_validation(
        store,
        HistoricalKalshiVenue([]),
        HistoricalPolymarketVenue([]),
        target_labels=1,
        polymarket_global_event_slugs=("core-pce-mom-june-2026",),
        additional_polymarket_venues={"polymarket_global": venue},
    )

    assert venue.requested == ["core-pce-mom-june-2026"]
    assert report["polymarket_global_event_slugs"] == ["core-pce-mom-june-2026"]
    assert report["polymarket_us_event_slug_markets"] == {"core-pce-mom-june-2026": 1}
    assert report["venue_coverage"]["polymarket_global"]["closed_markets"] == 1


def test_historical_label_review_pairs_reject_on_divergence_only():
    """SEMANTIC FLIP, named in the owner-signed 2026-08-13 decision
    (docs/decisions/2026-08-13-rejected-labels-from-review-pairs.md): a
    same-subject review pair with divergent terminal outcomes now mints an
    evidence-backed REJECTED label; agreement still proves nothing, and review
    pairs still can never approve."""
    markets = fixture_markets()
    pair = verify_equivalence(
        markets["kalshi"][0].model_copy(update={"threshold": Decimal(50)}),
        markets["polymarket_us"][0],
    )

    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert _historical_label(pair, "yes", "no") == ("REJECTED", "DIVERGED")
    assert _historical_label(pair, "no", "no") == (None, "INCONCLUSIVE")


def test_historical_event_candidates_require_strong_identity_overlap():
    market = fixture_markets()["polymarket_us"][0]
    events = [
        {
            "event_ticker": "WEAK",
            "title": "Federal election result",
            "sub_title": "",
        },
        {
            "event_ticker": "STRONG",
            "title": "Federal Reserve raises federal funds target",
            "sub_title": "September 2026",
        },
    ]

    candidates = historical_event_candidates(events, [market])

    assert [item[0]["event_ticker"] for item in candidates] == ["STRONG"]


def test_historical_event_candidates_prioritize_distinctive_overlap():
    market = fixture_markets()["polymarket_us"][0]
    events = [
        {
            "event_ticker": "GENERIC",
            "title": "Federal Reserve event in September",
            "sub_title": "",
        },
        {
            "event_ticker": "DISTINCTIVE",
            "title": "Federal Reserve raises federal funds target",
            "sub_title": "September 2026",
        },
    ]

    candidates = historical_event_candidates(events, [market])

    assert [item[0]["event_ticker"] for item in candidates] == [
        "DISTINCTIVE",
        "GENERIC",
    ]


def test_historical_event_candidates_reject_generic_sports_overlap():
    market = fixture_markets()["polymarket_us"][0].model_copy(deep=True)
    market.title = "Crook Town AFC vs Pickering Town FC: Neither team to score first?"
    market.raw_market_json["question"] = market.title
    market.venue_market_id = "crook-pickering-2026-08-10-first-score"
    events = [
        {
            "event_ticker": "KXMLS-26AUG10DAL",
            "title": "Will FC Dallas record the first goal of the game?",
            "sub_title": "FC Dallas vs Austin FC",
        }
    ]

    assert historical_event_candidates(events, [market]) == []


def test_historical_event_candidates_reject_conflicting_event_dates():
    market = fixture_markets()["polymarket_us"][0].model_copy(deep=True)
    market.title = "Will WTI Crude Oil hit $80 Week of August 10 2026?"
    market.raw_market_json["question"] = market.title
    market.venue_market_id = "wti-high-2026-08-10"
    events = [
        {
            "event_ticker": "KXWTI-26AUG07",
            "title": "Will WTI Oil close above $78 on August 7 2026?",
            "sub_title": "WTI Oil",
        }
    ]

    assert historical_event_candidates(events, [market]) == []


def test_historical_event_candidates_reject_unrelated_elections_with_shared_generic_terms():
    market = fixture_markets()["polymarket_us"][0].model_copy(deep=True)
    market.title = "Will Marsha Blackburn win the 2026 Tennessee Governor Republican primary election?"
    market.raw_market_json["question"] = market.title
    market.venue_market_id = "tennessee-primary-marsha-blackburn"
    events = [
        {
            "event_ticker": "KXPRIMARY-GOVORNOMR26",
            "title": "Who will win the Republican Governor primary?",
            "sub_title": "",
        }
    ]

    assert historical_event_candidates(events, [market]) == []


@pytest.mark.asyncio
async def test_recent_historical_backfill_is_not_due(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_historical_backfill(
        {
            "status": "EXTERNAL_EVIDENCE_BLOCKED",
            "completed_at": "2999-01-01T00:00:00+00:00",
        }
    )

    assert await _historical_backfill_due(store, 86_400) is False


class CountingKalshiVenue(HistoricalKalshiVenue):
    """Counts catalog scans so a shared catalog can be proven to skip them."""

    def __init__(self, markets):
        super().__init__(markets)
        self.settled_event_scans = 0

    async def list_settled_events(self, max_pages=100, series_tickers=None):
        self.settled_event_scans += 1
        return await super().list_settled_events(max_pages=max_pages)


class CountingPolymarketVenue(HistoricalPolymarketVenue):
    def __init__(self, markets):
        super().__init__(markets)
        self.closed_sweeps = 0
        self.evidence_fetches = 0

    async def list_closed_markets(self, max_pages=20):
        self.closed_sweeps += 1
        return await super().list_closed_markets(max_pages=max_pages)

    async def get_terminal_settlement_evidence(self, market_id):
        self.evidence_fetches += 1
        return await super().get_terminal_settlement_evidence(market_id)


def _settled_backfill_markets():
    markets = fixture_markets()
    kalshi = markets["kalshi"][0].model_copy(deep=True)
    kalshi.status = MarketStatus.SETTLED
    kalshi.raw_market_json["result"] = "yes"
    kalshi.raw_market_json["event_ticker"] = "KXFED-SEP26"
    polymarket = markets["polymarket_us"][0].model_copy(deep=True)
    polymarket.status = MarketStatus.CLOSED
    polymarket.raw_market_json["question"] = polymarket.title
    return kalshi, polymarket


@pytest.mark.asyncio
async def test_prefetched_catalog_replaces_the_per_run_venue_scans(tmp_path):
    kalshi_market, polymarket_market = _settled_backfill_markets()
    kalshi = CountingKalshiVenue([kalshi_market])
    polymarket = CountingPolymarketVenue([polymarket_market])

    catalog = await prefetch_shared_backfill_catalog(
        kalshi, polymarket, kalshi_event_pages=100, polymarket_pages=20
    )
    assert kalshi.settled_event_scans == 1
    assert polymarket.closed_sweeps == 1

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    report = await backfill_historical_validation(
        store,
        kalshi,
        polymarket,
        target_labels=1,
        shared_catalog=catalog,
    )

    # The run consumed the prefetched catalog instead of re-scanning either venue.
    assert kalshi.settled_event_scans == 1
    assert polymarket.closed_sweeps == 1
    assert report["kalshi_events_scanned"] == 1
    assert report["venue_coverage"]["polymarket_us"]["final_binary_markets"] == 1
    assert report["shared_catalog_fetched_at"] == catalog.fetched_at
    assert report["new_labels"] == 1


@pytest.mark.asyncio
async def test_shared_catalog_refuses_a_run_with_a_different_kalshi_scope(tmp_path):
    kalshi_market, polymarket_market = _settled_backfill_markets()
    kalshi = CountingKalshiVenue([kalshi_market])
    polymarket = CountingPolymarketVenue([polymarket_market])
    catalog = await prefetch_shared_backfill_catalog(
        kalshi,
        polymarket,
        kalshi_event_pages=100,
        kalshi_series_tickers=("KXFED",),
        polymarket_pages=20,
    )

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    with pytest.raises(ValueError, match="different Kalshi scope"):
        await backfill_historical_validation(
            store,
            kalshi,
            polymarket,
            target_labels=1,
            kalshi_series_tickers=("KXCPIYOY",),
            shared_catalog=catalog,
        )


@pytest.mark.asyncio
async def test_event_slug_harvest_bypasses_the_shared_catalog(tmp_path):
    """The slug door reaches markets the plain sweep cannot, so it must always
    fetch for itself rather than trusting the shared sweep."""
    kalshi_market, polymarket_market = _settled_backfill_markets()
    kalshi = CountingKalshiVenue([kalshi_market])
    polymarket = CountingPolymarketVenue([polymarket_market])

    async def list_event_markets(_event_slug):
        return []

    polymarket.list_event_markets = list_event_markets
    catalog = await prefetch_shared_backfill_catalog(
        kalshi, polymarket, kalshi_event_pages=100, polymarket_pages=20
    )
    sweeps_after_prefetch = polymarket.closed_sweeps

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await backfill_historical_validation(
        store,
        kalshi,
        polymarket,
        target_labels=1,
        polymarket_us_event_slugs=("some-settled-event",),
        shared_catalog=catalog,
    )

    assert polymarket.closed_sweeps == sweeps_after_prefetch + 1
