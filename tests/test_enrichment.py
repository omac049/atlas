import pytest

from atlas.discovery import compatibility_report
from atlas.enrichment import (
    enrich_shared_rules,
    enrich_weather_rules,
    shared_rule_target_report,
    shared_rule_targets,
    weather_rule_targets,
)
from atlas.storage import AtlasStore
from atlas.venues.kalshi import KalshiVenue
from atlas.venues.polymarket_us import PolymarketUSVenue


def _weather_market(venue: str, complete_policy: bool = True, high: bool = True):
    action = "highest" if high else "lowest"
    revision = (
        "The official and final value is taken from the latest version of that report."
        if complete_policy
        else ""
    )
    fallback = " Otherwise, this market resolves to No." if complete_policy else ""
    cancellation = (
        " If the observation is canceled, the market resolves to No."
        if complete_policy
        else ""
    )
    rules = (
        f"If the {action} temperature recorded at Central Park (KNYC) in New York City "
        "for Aug 9, 2026 as reported by the National Weather Service's Climatological "
        f"Report (Daily) is between 90F and 91F, then the market resolves to Yes. {revision}"
        f"{fallback}{cancellation}"
    )
    if venue == "kalshi":
        return KalshiVenue._normalize_market(
            {
                "ticker": f"KX{'HIGH' if high else 'LOW'}TNYC-26AUG09-B90",
                "title": f"Will the {action} temperature be 90-91° on Aug 9, 2026?",
                "rules_primary": rules,
                "floor_strike": 90,
                "cap_strike": 91,
                "strike_type": "between",
                "status": "open",
            }
        )
    return PolymarketUSVenue._normalize_market(
        {
            "slug": f"tc-temp-nyc{'high' if high else 'low'}-2026-08-09-gte90lt91f",
            "title": "90 to 91",
            "question": f"{action.title()} temperature in NYC on August 9?",
            "description": rules,
            "status": "MARKET_STATUS_OPEN",
        }
    )


class _DetailVenue:
    def __init__(self, markets: list):
        self.markets = {market.venue_market_id: market for market in markets}

    async def get_market(self, market_id: str):
        return self.markets[market_id].model_copy(deep=True)

    async def enrich_market_source(self, market):
        return market


def test_weather_targets_do_not_collapse_daily_high_and_low():
    kalshi_high = _weather_market("kalshi", high=True)
    kalshi_low = _weather_market("kalshi", high=False)
    polymarket_high = _weather_market("polymarket", high=True)
    targets = weather_rule_targets(
        [kalshi_low, kalshi_high], [polymarket_high]
    )
    assert len(targets) == 1
    assert "HIGH" in targets[0][0].venue_market_id


def test_weather_high_and_low_are_not_reported_as_shared_events():
    report = compatibility_report(
        [_weather_market("kalshi", high=False)],
        [_weather_market("polymarket", high=True)],
    )
    assert report["shared_event_count"] == 0
    assert report["settlement_discovery"]["shared_events_ranked"] == 0


def test_shared_rule_targets_are_bounded_and_family_aware():
    left = _weather_market("kalshi")
    right = _weather_market("polymarket")
    assert len(shared_rule_targets([left], [right], limit=1)) == 1
    assert shared_rule_targets(
        [left], [right], limit=1, exclude_market_types={"weather"}
    ) == []


def test_shared_rule_target_report_skips_known_discretionary_events():
    left = _weather_market("kalshi")
    right = _weather_market("polymarket")
    right.raw_rules_text = f"{right.raw_rules_text} The exchange may use a fair market price."
    report = shared_rule_target_report([left], [right], limit=1)
    assert report["shared_events_available"] == 1
    assert report["skipped_non_guaranteed"] == 1
    assert report["targets"] == []


@pytest.mark.asyncio
async def test_weather_enrichment_persists_missing_policy_as_blocker(tmp_path):
    listed_left = _weather_market("kalshi")
    listed_right = _weather_market("polymarket", complete_policy=False)
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    summary = await enrich_weather_rules(
        store,
        _DetailVenue([listed_left]),
        _DetailVenue([listed_right]),
        [listed_left],
        [listed_right],
    )
    assert summary["pairs_refreshed"] == 1
    assert summary["evidence_observed"] == 2
    assert summary["exact_rule_matches"] == 0
    assert summary["blocker_counts"]["REVISION_POLICY_MISMATCH"] == 1
    assert summary["blocker_counts"]["SETTLEMENT_GUARANTEE_UNKNOWN"] == 1
    cohort = summary["validation_cohort"]
    assert cohort["status"] == "RULE_EVIDENCE_BLOCKED"
    assert cohort["events"] == 1
    assert cohort["eligible_events"] == 0
    assert cohort["items"][0]["stage"] == "SETTLEMENT_EVIDENCE_BLOCKED"
    assert cohort["items"][0]["policy_compatibility"]["status"] == "INCOMPLETE"
    assert (await store.validation_summary())["markets_tracked"] == 2


@pytest.mark.asyncio
async def test_shared_rule_enrichment_persists_complete_policy_without_inference(tmp_path):
    left = _weather_market("kalshi")
    right = _weather_market("polymarket")
    summary = await enrich_shared_rules(
        AtlasStore(str(tmp_path / "atlas.sqlite3")),
        _DetailVenue([left]),
        _DetailVenue([right]),
        [left],
        [right],
        limit=1,
    )
    assert summary["shared_events_considered"] == 1
    assert summary["pairs_refreshed"] == 1
    assert summary["complete_policy_pairs"] == 1
    assert summary["policy_blocker_counts"] == {}


@pytest.mark.asyncio
async def test_weather_enrichment_allows_only_complete_identical_rules(tmp_path):
    left = _weather_market("kalshi")
    right = _weather_market("polymarket")
    summary = await enrich_weather_rules(
        AtlasStore(str(tmp_path / "atlas.sqlite3")),
        _DetailVenue([left]),
        _DetailVenue([right]),
        [left],
        [right],
    )
    assert summary["guaranteed_pairs"] == 1
    assert summary["exact_rule_matches"] == 1
    assert summary["blocker_counts"] == {}
    cohort = summary["validation_cohort"]
    assert cohort["status"] == "READY_FOR_SETTLEMENT_TRACKING"
    assert cohort["eligible_events"] == 1
    assert cohort["items"][0]["stage"] == "ELIGIBLE_FOR_VALIDATION"
    assert cohort["items"][0]["policy_compatibility"]["compatible"] is True
