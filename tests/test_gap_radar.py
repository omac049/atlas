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
from atlas.fingerprints import build_fingerprint
from atlas.gap_radar import (
    BANKROLL_START,
    EQUIVALENT_SHAPE,
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
    # Sized far above the stake so displayed depth never binds and only the
    # bankroll fraction moves. (Unsized observations are skipped outright now —
    # see test_an_opportunity_with_no_published_depth_is_skipped_not_uncapped.)
    observations = [
        _observation("2026-08-12", "0.48", "0.50", "100000"),
        _observation("2026-08-13", "0.48", "0.50", "100000"),
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
    assert summary["assumptions"]["stake_capped_by_thinner_leg_displayed_size"] is True


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


# --------------------------------------------------------------------------
# Categorical twins (chamber control). Texts, ids, and quote fields frozen from
# the live catalogs 2026-08-20: Kalshi CONTROLH-2026-D and Polymarket 562802 /
# 562828. Added to radar scope mid-study; see docs/NINETY_DAY_STUDY.md.
# --------------------------------------------------------------------------

KALSHI_HOUSE_D_TITLE = "Will Democrats win the House in 2026?"
KALSHI_HOUSE_D_RULES = (
    "If the Democratic Party has won control of the House in 2026, then the market resolves "
    "to Yes.\n\nThis market may be determined early based on a consensus of media calls "
    "projecting control of the U.S. House. See full rules for details. Otherwise, victory "
    "will be determined by the party identification of the Speaker of the House on "
    "February 1, 2027."
)
POLYMARKET_HOUSE_D_TITLE = (
    "Will the Democratic Party control the House after the 2026 Midterm elections?"
)
POLYMARKET_HOUSE_D_RULES = (
    "This market will resolve according to the party that wins control of the United States "
    "House of Representatives in the 2026 United States midterm election. A party wins "
    "control if it wins a majority of the chamber's voting seats. If no party wins a "
    "majority, control is determined by the party with which the first elected Speaker of "
    "the House is affiliated. Outcome sourced from relevant state electoral authorities, "
    "and the United States House of Representatives."
)
# The trap: a JOINT Senate+House contract that normalizes to the HOUSE subject
# with NO affirmative outcome. Pairing it with a House-only bet would compare
# two different claims and manufacture a gap.
POLYMARKET_BALANCE_OF_POWER_TITLE = "2026 Balance of Power: D Senate, D House"
POLYMARKET_BALANCE_OF_POWER_RULES = (
    "This market will resolve according to the party that wins control of the United States "
    "Senate and the party that wins control of the United States House of Representatives "
    "in the 2026 United States midterm election."
)


def _chamber_market(venue_key: str, title: str, rules: str, raw: dict):
    market = _market(venue_key, title, rules, raw)
    market.resolution_text = rules
    market.subtitle = None
    return market


def _kalshi_house_d(raw: dict | None = None):
    return _chamber_market(
        "kalshi",
        KALSHI_HOUSE_D_TITLE,
        KALSHI_HOUSE_D_RULES,
        raw
        if raw is not None
        else {
            "yes_ask_dollars": "0.8500",
            "no_ask_dollars": "0.1600",
            "yes_ask_size_fp": "163705.67",
        },
    )


def _polymarket_house_d(raw: dict | None = None):
    return _chamber_market(
        "polymarket_us",
        POLYMARKET_HOUSE_D_TITLE,
        POLYMARKET_HOUSE_D_RULES,
        raw
        if raw is not None
        else {
            "bestAsk": 0.89,
            "bestBid": 0.88,
            "feesEnabled": True,
            "feeSchedule": {"exponent": 1, "rate": 0.04, "takerOnly": True},
        },
    )


def test_matches_real_chamber_control_pair_as_categorical_equivalent_shape():
    """Neither leg publishes a threshold, so the threshold-only matcher formed no
    pair at all before 2026-08-20 — which is why the study's settlement-timing
    asymmetry split had no eligible population."""
    pairs = match_twin_shapes([_kalshi_house_d()], [_polymarket_house_d()])
    assert len(pairs) == 1
    assert pairs[0]["shape"] == EQUIVALENT_SHAPE
    assert pairs[0]["event_subject"] == "us_house_control|2026"


def test_categorical_pair_carries_the_settlement_timing_asymmetry_tag():
    """The whole point of bringing this family in: Kalshi may settle early on a
    media-call consensus while the Polymarket twin waits on official sources."""
    pair = match_twin_shapes([_kalshi_house_d()], [_polymarket_house_d()])[0]
    observation = observe_pair(pair)
    timing = observation["settlement_timing"]
    assert timing["asymmetric"] is True
    assert timing["early_venue"] == "kalshi"
    assert "SETTLEMENT_TIMING_ASYMMETRIC" in timing["codes"]
    # Never trusted: the asymmetry is a caution, not an approval input.
    assert observation["trusted"] is False
    assert observation["pair_kind"] == PAIR_KIND


def test_joint_balance_of_power_contract_never_pairs_with_a_single_chamber_bet():
    """It normalizes to the house-control subject but names no single party. A
    null-tolerant match would compare 'D Senate AND D House' with 'D House'."""
    joint = _chamber_market(
        "polymarket_us",
        POLYMARKET_BALANCE_OF_POWER_TITLE,
        POLYMARKET_BALANCE_OF_POWER_RULES,
        {"bestAsk": 0.48, "bestBid": 0.47},
    )
    assert build_fingerprint(joint).affirmative_outcome is None
    assert match_twin_shapes([_kalshi_house_d()], [joint]) == []


def test_opposing_parties_are_not_treated_as_an_inverse_shape():
    """'Democrats win' and 'Republicans win' are NOT a published complement: ties
    and third outcomes exist, which is why both venues publish tiebreak clauses.
    Calling them inverse would be inference."""
    republican = _chamber_market(
        "polymarket_us",
        "Will the Republican Party control the House after the 2026 Midterm elections?",
        POLYMARKET_HOUSE_D_RULES.replace("Democratic Party", "Republican Party"),
        {"bestAsk": 0.12, "bestBid": 0.11},
    )
    assert match_twin_shapes([_kalshi_house_d()], [republican]) == []


def test_a_threshold_contract_never_pairs_with_a_categorical_one():
    """A CPI threshold bucket and a chamber-control bet can share neither a
    basket nor a meaning, even if a subject collision ever put them together."""
    assert match_twin_shapes([_kalshi_t31({})], [_polymarket_house_d()]) == []
    assert match_twin_shapes([_kalshi_house_d()], [_polymarket_tail({})]) == []


# Captured verbatim from the live Polymarket US gateway 2026-08-20
# (`paccc-balpow-2026-11-03-rhou-dsen`). Unlike the Gamma joint contract above,
# this one names a specific party per chamber, so the normalizer DOES extract an
# affirmative outcome — and extracts the wrong one.
PMUS_JOINT_RHOU_DSEN_TITLE = "R House, D Senate"
PMUS_JOINT_RHOU_DSEN_RULES = (
    "This market will settle to Yes if the Republican Party wins control of the United "
    "States House of Representatives and the Democratic Party wins control of the United "
    "States Senate following the 2026 Midterm elections."
)


def test_pmus_joint_balance_of_power_misattributes_its_chamber_party():
    """Documents a REAL normalizer defect, so it cannot regress silently.

    `R House, D Senate` pays when REPUBLICANS take the House, but the fingerprint
    reports `democratic_party`. The Gamma joint contract is caught by the
    null-outcome guard; this one is not, because it publishes a party name per
    chamber. Live on 2026-08-20 this paired straight through to Kalshi's
    "Will Democrats win the House" leg and printed a phantom 79.8c gap.

    When the election normalizer learns chamber attribution, this assertion
    flips to `republican_party` and the scope exclusion below can be lifted.
    """
    joint = _chamber_market(
        "polymarket_us",
        PMUS_JOINT_RHOU_DSEN_TITLE,
        PMUS_JOINT_RHOU_DSEN_RULES,
        {"bestAskQuote": {"value": "0.1800"}, "bestBidQuote": {"value": "0.1700"}},
    )
    fingerprint = build_fingerprint(joint)
    assert fingerprint.event_subject == "us_house_control|2026"
    # The defect, pinned: a joint two-chamber bet claims a single-chamber party.
    assert fingerprint.affirmative_outcome == "democratic_party"
    # And therefore it DOES pair — which is exactly why scope must exclude it.
    assert match_twin_shapes([_kalshi_house_d()], [joint]) != []


def test_radar_scope_excludes_polymarket_us_politics_until_the_defect_is_fixed():
    """The containment for the defect above is scope, not a silent filter."""
    from atlas.cli import GAP_RADAR_PMUS_CATEGORIES

    assert "politics" not in GAP_RADAR_PMUS_CATEGORIES
    assert GAP_RADAR_PMUS_CATEGORIES == ("macro",)


# --- Polymarket US: the tradeable leg -------------------------------------
#
# Every observation the radar recorded before 2026-08-20 priced a Polymarket
# GLOBAL leg, which publishes no book and which `polymarket_global.py` states
# can never reach an executable path. These pin the US venue's different quote
# shape, its different fee publication, and the depth it does provide.


def test_polymarket_us_quote_object_is_read_like_a_gamma_scalar():
    """Gamma publishes `bestAsk: "0.71"`; the US gateway publishes
    `bestAskQuote: {"value": "0.7100", "currency": "USD"}`. Same meaning."""
    us = _polymarket_tail(
        {"bestAskQuote": {"value": "0.7100"}, "bestBidQuote": {"value": "0.7000"}}
    )
    quotes = polymarket_quotes(us)
    assert quotes["yes_ask"] == Decimal("0.7100")
    # NO ask is 1 minus the YES bid, exactly as on Gamma.
    assert quotes["no_ask"] == Decimal("0.3000")


def test_polymarket_us_fee_uses_the_published_coefficient_not_the_max_fallback():
    """The US gateway publishes a scalar `feeCoefficient` and no `feeSchedule`.
    Without this the max-rate fallback would overstate the fee by ~17%."""
    fee, basis = polymarket_taker_fee_per_share(Decimal("0.50"), {"feeCoefficient": "0.06"})
    assert basis == "venue_published_coefficient"
    assert fee == Decimal("0.06") * Decimal("0.25")
    # A market publishing neither still pays the conservative maximum.
    _, missing_basis = polymarket_taker_fee_per_share(Decimal("0.50"), {})
    assert missing_basis == "schedule_missing_max_rate_applied"


def test_a_basket_is_capped_by_its_thinner_leg_and_unknown_when_one_is_unpublished():
    """A paired position is only as large as the smaller side. A venue that
    publishes no depth makes the binding size UNKNOWN, never unlimited."""
    kalshi = _kalshi_t31(
        {"yes_ask_dollars": "0.4000", "no_ask_dollars": "0.6100", "yes_ask_size_fp": "900"}
    )
    polymarket = _polymarket_tail(
        {"bestAskQuote": {"value": "0.4000"}, "bestBidQuote": {"value": "0.4200"}}
    )
    sized = observe_pair(
        match_twin_shapes([kalshi], [polymarket])[0],
        polymarket_sizes={"yes_size": Decimal(120), "no_size": Decimal(250)},
    )
    best = next(b for b in sized["baskets"] if b["legs"] == sized["best_basket"])
    # This CPI pair is an operator complement, so both legs take their YES side
    # and both size keys are "yes_size" — the case where a single shared key
    # would have looked correct while silently reading one leg twice.
    assert sized["shape"] == INVERSE_SHAPE
    assert best["legs"] == "kalshi_yes+polymarket_yes"
    assert best["kalshi_size"] == "900"
    assert best["polymarket_size"] == "120"
    assert best["basket_size"] == "120"  # the thinner leg binds
    assert sized["polymarket_fill_assumed_at_quote"] is False

    unsized = observe_pair(match_twin_shapes([kalshi], [polymarket])[0])
    unsized_best = next(
        b for b in unsized["baskets"] if b["legs"] == unsized["best_basket"]
    )
    assert unsized_best["polymarket_size"] is None
    assert unsized_best["basket_size"] is None
    assert unsized["polymarket_fill_assumed_at_quote"] is True


def test_every_observation_records_which_polymarket_venue_priced_it():
    """No downstream metric should have to infer tradeability from an id."""
    kalshi = _kalshi_t31(
        {"yes_ask_dollars": "0.4000", "no_ask_dollars": "0.6100", "yes_ask_size_fp": "900"}
    )
    observation = observe_pair(
        match_twin_shapes(
            [kalshi],
            [_polymarket_tail({"bestAskQuote": {"value": "0.4000"}, "bestBidQuote": {"value": "0.4200"}})],
        )[0]
    )
    assert observation["polymarket_venue"] == "polymarket_us"
    assert observation["tradeable_venue_pair"] is True


def test_an_opportunity_with_no_published_depth_is_skipped_not_uncapped():
    """An unknown displayed size is unknown, never unlimited.

    Before 2026-08-20 a null size ran at the full 5% stake: 8 of 26 recorded
    opportunities had no depth at all and supplied 35% of the meter's profit.
    Skipping is the conservative reading, and the count is reported so the
    difference is always attributable.
    """
    summary = paper_bankroll_summary(
        [
            _observation("2026-08-12", "0.48", "0.50", None),
            _observation("2026-08-13", "0.10", "0.90", "400"),
        ]
    )
    assert summary["unsized_opportunities_skipped"] == 1
    assert summary["distinct_executable_opportunities"] == 1
    # Only the sized opportunity moved the bankroll.
    expected = (
        BANKROLL_START + (BANKROLL_START * STAKE_FRACTION) / Decimal("0.90") * Decimal("0.10")
    ).quantize(Decimal("0.01"))
    assert summary["paper_bankroll"] == str(expected)
    assert summary["assumptions"]["unsized_opportunities_are_skipped_not_uncapped"] is True


def test_sub_tick_and_dust_sized_gaps_are_flagged_below_the_floors():
    """`executable_gap` alone counted noise as opportunity.

    Both venues quote in whole cents, so an edge under one tick is inside the
    quantization noise of the prices that produced it. And a gap is only worth
    anything if you can take some of it: a live GDP pair on 2026-08-20 showed a
    7.8c gap against 0.06 contracts of Polymarket depth.

    `executable_gap` is deliberately unchanged so the recorded series stays
    comparable across the whole study; the floors are stricter companions.
    """
    kalshi = _kalshi_t31(
        {"yes_ask_dollars": "0.4000", "no_ask_dollars": "0.6100", "yes_ask_size_fp": "900"}
    )
    polymarket = _polymarket_tail(
        {"bestAskQuote": {"value": "0.4000"}, "bestBidQuote": {"value": "0.4200"}}
    )
    dust = observe_pair(
        match_twin_shapes([kalshi], [polymarket])[0],
        polymarket_sizes={"yes_size": Decimal("0.06"), "no_size": Decimal("0.06")},
    )
    assert dust["executable_gap"] is True  # gross edge is positive...
    assert dust["meets_size_floor"] is False  # ...on 0.06 contracts
    assert dust["best_basket_size"] == "0.06"

    real = observe_pair(
        match_twin_shapes([kalshi], [polymarket])[0],
        polymarket_sizes={"yes_size": Decimal(500), "no_size": Decimal(500)},
    )
    assert real["meets_size_floor"] is True
    assert real["meets_tick_floor"] is True


def test_an_unsized_observation_never_claims_to_meet_the_size_floor():
    """Unknown depth is unknown, not passing."""
    kalshi = _kalshi_t31({"yes_ask_dollars": "0.4000", "no_ask_dollars": "0.6100"})
    polymarket = _polymarket_tail(
        {"bestAskQuote": {"value": "0.4000"}, "bestBidQuote": {"value": "0.4200"}}
    )
    observation = observe_pair(match_twin_shapes([kalshi], [polymarket])[0])
    assert observation["meets_size_floor"] is False
    assert observation["best_basket_size"] is None
