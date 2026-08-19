"""Gap radar: twin-shape matching, executable-gap math, and the $2k paper meter.

The radar is a paper-only measurement instrument over CANDIDATE pairs. These
tests pin: deterministic shape matching on canonical terms, honest top-of-book
quote extraction, locked-basket gap math with venue-published taker fees, the
candidate/never-trusted labeling on every observation, and the bankroll
summary's dedup and sizing assumptions.
"""

from decimal import Decimal

from fastapi.testclient import TestClient
from test_cpi_yoy import (
    KALSHI_T31_RULES,
    KALSHI_T31_TITLE,
    POLYMARKET_CPI_RULES,
    POLYMARKET_TAIL_TITLE,
)

from apps.api.main import app
from atlas.gap_radar import (
    BANKROLL_START,
    INVERSE_SHAPE,
    PAIR_KIND,
    STAKE_FRACTION,
    kalshi_quotes,
    kalshi_taker_fee_per_contract,
    match_twin_shapes,
    observe_pair,
    paper_bankroll_summary,
    polymarket_quotes,
    polymarket_taker_fee_per_share,
)
from atlas.storage import AtlasStore
from atlas.venues.fixtures import fixture_markets


def _market(venue_key: str, title: str, rules: str, raw: dict):
    market = fixture_markets()[venue_key][0]
    market.title = title
    market.raw_rules_text = rules
    market.description = None
    market.resolution_source = "unknown"
    market.threshold = None
    market.threshold_upper = None
    market.threshold_operator = None
    market.raw_market_json = raw
    return market


def _kalshi_t31(raw: dict):
    return _market("kalshi", KALSHI_T31_TITLE, KALSHI_T31_RULES, raw)


def _polymarket_tail(raw: dict):
    return _market("polymarket_us", POLYMARKET_TAIL_TITLE, POLYMARKET_CPI_RULES, raw)


def test_matches_real_cpi_tail_pair_as_inverse_shape():
    pairs = match_twin_shapes([_kalshi_t31({})], [_polymarket_tail({})])
    assert len(pairs) == 1
    assert pairs[0]["shape"] == INVERSE_SHAPE
    assert pairs[0]["event_subject"] == "us_cpi_yoy|2026-07"


def test_no_match_without_canonical_subject_or_threshold():
    plain = _market("kalshi", "Will something happen?", "Some rules.", {})
    assert match_twin_shapes([plain], [_polymarket_tail({})]) == []


def test_opposite_direction_fomc_buckets_never_pair():
    """First-live-scan regression: a hike-25 bucket and a CUT-25 bucket share
    subject, threshold, and operator but are opposite bets — pairing them
    produced phantom 30-cent 'gaps'. Direction must match exactly."""
    from test_fomc_decision import KALSHI_H25_RULES, KALSHI_H25_TITLE, POLYMARKET_25_RULES

    kalshi_hike = _market("kalshi", KALSHI_H25_TITLE, KALSHI_H25_RULES, {})
    polymarket_cut = _market(
        "polymarket_us",
        "Will the Fed decrease interest rates by 25 bps after the July 2026 meeting?",
        POLYMARKET_25_RULES,
        {},
    )
    assert match_twin_shapes([kalshi_hike], [polymarket_cut]) == []
    polymarket_hike = _market(
        "polymarket_us",
        "Will the Fed increase interest rates by 25 bps after the July 2026 meeting?",
        POLYMARKET_25_RULES,
        {},
    )
    assert len(match_twin_shapes([kalshi_hike], [polymarket_hike])) == 1


def test_kalshi_zero_size_ask_is_not_a_quote():
    quotes = kalshi_quotes(
        _kalshi_t31(
            {
                "yes_ask_dollars": "1.0000",
                "yes_ask_size_fp": "0.00",
                "no_ask_dollars": "0.02",
                "no_ask_size_fp": "540",
            }
        )
    )
    assert quotes["yes_ask"] is None
    assert quotes["no_ask"] == Decimal("0.02")


def test_polymarket_no_ask_derives_from_yes_bid():
    quotes = polymarket_quotes(_polymarket_tail({"bestAsk": "0.059", "bestBid": "0.0345"}))
    assert quotes["yes_ask"] == Decimal("0.059")
    assert quotes["no_ask"] == Decimal(1) - Decimal("0.0345")


def test_observation_records_candidate_caveats_and_gap_math():
    kalshi = _kalshi_t31(
        {
            "yes_ask_dollars": "0.99",
            "yes_ask_size_fp": "100",
            "no_ask_dollars": "0.06",
            "no_ask_size_fp": "200",
        }
    )
    polymarket = _polymarket_tail({"bestAsk": "0.059", "bestBid": "0.01"})
    pairs = match_twin_shapes([kalshi], [polymarket])
    observation = observe_pair(pairs[0], observed_at="2026-08-12T20:00:00+00:00")
    assert observation["paper_only"] is True
    assert observation["trusted"] is False
    assert observation["pair_kind"] == PAIR_KIND
    assert observation["verification_status"] == "REVIEW_REQUIRED"
    assert observation["mismatch_codes"]
    # inverse baskets: yes+yes = 0.99 + 0.059; no+no = 0.06 + (1 - 0.01)
    costs = {basket["legs"]: Decimal(basket["cost"]) for basket in observation["baskets"]}
    assert costs["kalshi_yes+polymarket_yes"] == Decimal("1.049")
    assert costs["kalshi_no+polymarket_no"] == Decimal("1.05")
    # Per-leg fees flip the best basket: no+no's polymarket leg at 0.99 pays a
    # near-zero quadratic fee, beating yes+yes despite its 0.001 cheaper cost.
    assert observation["best_basket"] == "kalshi_no+polymarket_no"
    expected_fees = kalshi_taker_fee_per_contract(Decimal("0.06")) + (
        polymarket_taker_fee_per_share(Decimal("0.99"), {})[0]
    )
    assert Decimal(observation["best_gap"]) == Decimal(1) - Decimal("1.05") - expected_fees
    assert observation["executable_gap"] is False


def test_executable_gap_detected_when_basket_beats_fees():
    kalshi = _kalshi_t31(
        {"no_ask_dollars": "0.02", "no_ask_size_fp": "500", "yes_ask_dollars": "0.99",
         "yes_ask_size_fp": "10"}
    )
    polymarket = _polymarket_tail({"bestAsk": "0.90", "bestBid": "0.955"})
    pairs = match_twin_shapes([kalshi], [polymarket])
    observation = observe_pair(pairs[0])
    # no+no basket: 0.02 + (1 - 0.955) = 0.065; fees = ceil-cent Kalshi quadratic
    # (0.01) + polymarket max-rate fallback (no schedule on the fixture payload)
    assert observation["best_basket"] == "kalshi_no+polymarket_no"
    expected_fees = kalshi_taker_fee_per_contract(Decimal("0.02")) + (
        polymarket_taker_fee_per_share(Decimal("0.045"), {})[0]
    )
    assert Decimal(observation["best_gap"]) == Decimal(1) - Decimal("0.065") - expected_fees
    assert observation["executable_gap"] is True


def test_kalshi_fee_matches_the_published_schedule_examples():
    """Kalshi's own schedule: 100 contracts at 50c cost $1.75 (1.75c each,
    ceil-per-contract makes it 2c — conservative, never in our favor); at 10c
    and 90c the published example is 63c per 100 (0.63c -> 1c ceiled)."""
    assert kalshi_taker_fee_per_contract(Decimal("0.5")) == Decimal("0.02")
    assert kalshi_taker_fee_per_contract(Decimal("0.10")) == Decimal("0.01")
    assert kalshi_taker_fee_per_contract(Decimal("0.90")) == Decimal("0.01")


def test_polymarket_fee_uses_the_published_per_market_schedule():
    economics = {"feesEnabled": True, "feeSchedule": {"exponent": 1, "rate": 0.05}}
    fee, basis = polymarket_taker_fee_per_share(Decimal("0.5"), economics)
    assert fee == Decimal("0.0125")
    assert basis == "venue_published_schedule"


def test_polymarket_fee_disabled_market_is_free():
    fee, basis = polymarket_taker_fee_per_share(Decimal("0.5"), {"feesEnabled": False})
    assert fee == Decimal(0)
    assert basis == "venue_fees_disabled"


def test_polymarket_missing_schedule_takes_the_maximum_published_rate():
    """An absent field must never flatter a gap: unknown schedule -> max rate."""
    fee, basis = polymarket_taker_fee_per_share(Decimal("0.5"), {"feesEnabled": True})
    assert fee == Decimal("0.0175")
    assert basis == "schedule_missing_max_rate_applied"


def test_observation_carries_the_settlement_timing_annotation():
    """The timing tag rides along with every pre-existing field and changes
    none of them: it is observability only (atlas/settlement_timing.py)."""
    from datetime import UTC, datetime

    from test_settlement_timing import KALSHI_CHAMBER_CONTROL_RULES

    kalshi = _kalshi_t31(
        {"yes_ask_dollars": "0.99", "yes_ask_size_fp": "100", "no_ask_dollars": "0.06",
         "no_ask_size_fp": "200"}
    )
    kalshi.raw_rules_text += " " + KALSHI_CHAMBER_CONTROL_RULES
    kalshi.close_time = datetime(2026, 9, 11, 20, 0, tzinfo=UTC)
    polymarket = _polymarket_tail({"bestAsk": "0.059", "bestBid": "0.01"})
    polymarket.close_time = datetime(2026, 9, 11, 20, 0, tzinfo=UTC)

    pairs = match_twin_shapes([kalshi], [polymarket])
    observation = observe_pair(pairs[0], observed_at="2026-08-12T20:00:00+00:00")
    timing = observation["settlement_timing"]
    assert timing["asymmetric"] is True
    assert timing["early_venue"] == "kalshi"
    assert "EARLY_MEDIA_CONSENSUS" in timing["early_codes"]
    assert timing["days_to_settlement"] == "30.0"
    assert timing["horizon_basis"] == "kalshi_close_time"
    # Nothing the radar already recorded moved.
    assert observation["trusted"] is False
    assert observation["pair_kind"] == PAIR_KIND
    assert observation["verification_status"] == "REVIEW_REQUIRED"
    assert observation["best_basket"] == "kalshi_no+polymarket_no"
    assert Decimal(observation["best_gap"]) < 0
    assert observation["executable_gap"] is False
    assert {basket["legs"] for basket in observation["baskets"]} == {
        "kalshi_yes+polymarket_yes",
        "kalshi_no+polymarket_no",
    }


def test_symmetric_pair_observation_flags_no_asymmetry():
    kalshi = _kalshi_t31({"no_ask_dollars": "0.02", "no_ask_size_fp": "500"})
    polymarket = _polymarket_tail({"bestAsk": "0.90", "bestBid": "0.955"})
    pairs = match_twin_shapes([kalshi], [polymarket])
    observation = observe_pair(pairs[0], observed_at="2026-08-12T20:00:00+00:00")
    assert observation["settlement_timing"]["asymmetric"] is False
    assert observation["settlement_timing"]["codes"] == []
    assert observation["settlement_timing"]["early_venue"] is None


def _observation(day: str, gap: str, cost: str, size: str | None, pair: str = "p1") -> dict:
    return {
        "observed_at": f"{day}T12:00:00+00:00",
        "kalshi_market_id": f"kalshi:{pair}",
        "polymarket_market_id": f"pm:{pair}",
        "executable_gap": True,
        "best_gap": gap,
        "best_basket": "kalshi_no+polymarket_no",
        "baskets": [
            {"legs": "kalshi_no+polymarket_no", "cost": cost, "gap": gap, "kalshi_size": size}
        ],
    }


def test_bankroll_starts_at_2000_and_stays_there_without_gaps():
    summary = paper_bankroll_summary([])
    assert summary["paper_bankroll"] == "2000.00"
    assert summary["distinct_executable_opportunities"] == 0
    assert summary["paper_only"] is True
    assert summary["assumptions"]["pairs_are_candidates_not_proven_twins"] is True


def test_bankroll_dedupes_same_pair_same_day_and_applies_caps():
    observations = [
        _observation("2026-08-12", "0.08", "0.90", "500"),
        _observation("2026-08-12", "0.08", "0.90", "500"),  # same pair+day: ignored
        _observation("2026-08-13", "0.05", "0.95", "20"),  # size-capped: 20*0.95=19
    ]
    summary = paper_bankroll_summary(observations)
    # day 1: stake 5% of 2000 = 100 -> profit 100/0.90*0.08 = 8.888...
    # day 2: stake min(5% of 2008.89, 19) = 19 -> profit 19/0.95*0.05 = 1.0
    expected = (
        BANKROLL_START
        + Decimal(100) / Decimal("0.90") * Decimal("0.08")
        + Decimal(19) / Decimal("0.95") * Decimal("0.05")
    ).quantize(Decimal("0.01"))
    assert summary["paper_bankroll"] == str(expected)
    assert summary["distinct_executable_opportunities"] == 2


def test_bankroll_stake_compounds_as_a_fraction_of_bankroll():
    """The stake is a bankroll fraction, not a flat cap: after a win, the next
    uncapped opportunity stakes more, so identical gaps yield growing profits.
    A flat-$100 meter would add the same profit both days."""
    observations = [
        _observation("2026-08-12", "0.48", "0.50", None),
        _observation("2026-08-13", "0.48", "0.50", None),
    ]
    stake_one = BANKROLL_START * STAKE_FRACTION
    profit_one = stake_one / Decimal("0.50") * Decimal("0.48")
    stake_two = (BANKROLL_START + profit_one) * STAKE_FRACTION
    profit_two = stake_two / Decimal("0.50") * Decimal("0.48")
    assert stake_two > stake_one
    assert profit_two > profit_one
    summary = paper_bankroll_summary(observations)
    expected = (BANKROLL_START + profit_one + profit_two).quantize(Decimal("0.01"))
    assert summary["paper_bankroll"] == str(expected)
    assert summary["assumptions"]["stake_fraction_of_bankroll"] == str(STAKE_FRACTION)
    assert summary["assumptions"]["stake_capped_by_kalshi_displayed_size"] is True


async def test_gap_observations_storage_round_trip(tmp_path):
    store = AtlasStore(str(tmp_path / "gaps.sqlite3"))
    for index in range(3):
        await store.save_gap_observation(
            {
                "observation_id": f"obs-{index}",
                "observed_at": f"2026-08-1{index}T00:00:00+00:00",
                "best_gap": "-0.01",
            }
        )
    recent = await store.recent_gap_observations(2)
    assert [item["observation_id"] for item in recent] == ["obs-2", "obs-1"]
    everything = await store.all_gap_observations()
    assert [item["observation_id"] for item in everything] == ["obs-0", "obs-1", "obs-2"]


def test_overview_exposes_gap_radar_summary():
    overview = TestClient(app).get("/api/overview")
    assert overview.status_code == 200
    radar = overview.json()["gap_radar"]
    assert radar["summary"]["paper_only"] is True
    assert "paper_bankroll" in radar["summary"]
    assert isinstance(radar["recent"], list)
