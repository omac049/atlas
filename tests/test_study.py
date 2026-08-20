"""90-day study metrics (docs/NINETY_DAY_STUDY.md).

Pins the charter's definitions exactly: the pair+UTC-day opportunity dedup, the
survival-run tolerance, the venue-text-only classification, the fee-model split
between legacy flat-buffer rows and venue-published rows, and the go-threshold
arithmetic. The report must be regenerable from persisted observations alone.
"""

from datetime import date

from atlas.study import (
    NO_ANNOTATED_OBSERVATIONS,
    NO_WATCHED_PAIR_PUBLISHES_EARLY_DETERMINATION,
    STUDY_START,
    study_report,
)


def _observation(
    at: str,
    *,
    pair: str = "p1",
    gap: str = "0.03",
    executable: bool = True,
    codes: list[str] | None = None,
    basket: dict | None = None,
    subject: str = "us_fomc_rate_decision|2027-01",
    days_to_settlement: str | None = None,
    asymmetric: bool = False,
) -> dict:
    default_basket = {
        "legs": "kalshi_yes+polymarket_no",
        "cost": "0.95",
        "kalshi_fee": "0.01",
        "polymarket_fee": "0.0125",
        "kalshi_size": "40",
    }
    return {
        "observed_at": at,
        "kalshi_market_id": f"kalshi:{pair}",
        "polymarket_market_id": f"pm:{pair}",
        "event_subject": subject,
        "executable_gap": executable,
        "best_gap": gap,
        "best_basket": "kalshi_yes+polymarket_no",
        "mismatch_codes": codes
        if codes is not None
        else ["SETTLEMENT_GUARANTEE_UNKNOWN", "SETTLEMENT_POLICY_MISMATCH"],
        "baskets": [basket if basket is not None else default_basket],
        "settlement_timing": {
            "asymmetric": asymmetric,
            "codes": ["SETTLEMENT_TIMING_ASYMMETRIC"] if asymmetric else [],
            "early_venue": "kalshi" if asymmetric else None,
            "early_codes": ["EARLY_MEDIA_CONSENSUS"] if asymmetric else [],
            "days_to_settlement": days_to_settlement,
            "horizon_basis": "kalshi_close_time" if days_to_settlement else None,
        },
    }


def test_opportunity_dedup_is_pair_and_utc_day():
    """A gap re-observed across sweeps in one day is ONE opportunity; a new day
    or a different pair is a new one."""
    observations = [
        _observation("2026-08-20T10:00:00+00:00"),
        _observation("2026-08-20T10:05:00+00:00"),
        _observation("2026-08-20T10:10:00+00:00"),
        _observation("2026-08-21T10:00:00+00:00"),
        _observation("2026-08-21T10:00:00+00:00", pair="p2"),
    ]
    report = study_report(observations, today=date(2026, 8, 21))
    assert report["distinct_opportunities"] == 3
    assert report["paper_only"] is True


def test_survival_runs_split_on_the_sweep_tolerance():
    """Three sweeps 5 minutes apart are one 10-minute run; an observation 16
    minutes later starts a new run and, alone, counts as single-sweep-only."""
    observations = [
        _observation("2026-08-20T10:00:00+00:00"),
        _observation("2026-08-20T10:05:00+00:00"),
        _observation("2026-08-20T10:10:00+00:00"),
        _observation("2026-08-20T10:26:00+00:00"),
    ]
    report = study_report(observations, today=date(2026, 8, 20))
    assert report["survival"]["runs"] == 1
    assert report["survival"]["median_minutes"] == "10.0"
    assert report["survival"]["single_sweep_only"] == 1


def test_venue_text_only_classification_is_strict():
    """One structural code disqualifies: only refusals consisting SOLELY of
    text-revision-clearable codes count as verified-opportunity precursors."""
    observations = [
        _observation("2026-08-20T10:00:00+00:00", pair="textonly"),
        _observation(
            "2026-08-20T10:00:00+00:00",
            pair="structural",
            codes=["SETTLEMENT_POLICY_MISMATCH", "THRESHOLD_MISMATCH"],
        ),
    ]
    report = study_report(observations, today=date(2026, 8, 20))
    week = report["weekly"][0]
    assert week["opportunities"] == 2
    assert week["venue_text_only_opportunities"] == 1


def test_fee_model_rows_are_counted_separately():
    """Legacy flat-buffer rows and venue-published rows must never be silently
    mixed: the report says how many of each it saw."""
    legacy_basket = {
        "legs": "kalshi_yes+polymarket_no",
        "cost": "0.95",
        "fee_buffer": "0.02",
        "kalshi_size": "40",
    }
    observations = [
        _observation("2026-08-20T10:00:00+00:00"),
        _observation("2026-08-20T10:05:00+00:00", basket=legacy_basket),
    ]
    report = study_report(observations, today=date(2026, 8, 20))
    assert report["fee_model_rows"] == {"venue_published": 1, "legacy_flat_buffer": 1}


def test_go_threshold_uses_elapsed_days_not_calendar_hope():
    """2 opportunities in the first 10 study days = 6.0/30d — below the go
    threshold and reported as such."""
    observations = [
        _observation("2026-08-20T10:00:00+00:00"),
        _observation("2026-08-21T10:00:00+00:00"),
    ]
    report = study_report(observations, today=date(2026, 8, 28))
    assert report["study_start"] == STUDY_START.isoformat()
    assert report["study_day"] == 10
    assert report["phase"] == 1
    assert report["rate_window_days"] == 10
    assert report["verified_opportunities_per_30_days"] == "6.0"
    assert report["meets_frequency_threshold"] is False
    assert report["meets_go_threshold"]["go"] is False


def test_rate_window_extends_to_retroactive_observations():
    """Pre-charter observations widen the rate window: a week of retroactive
    data on study day 1 must NOT be divided by one day."""
    observations = [
        _observation("2026-08-12T10:00:00+00:00"),
        _observation("2026-08-18T10:00:00+00:00"),
    ]
    report = study_report(observations, today=date(2026, 8, 19))
    assert report["study_day"] == 1
    assert report["rate_window_days"] == 8
    assert report["verified_opportunities_per_30_days"] == "7.5"


def test_go_threshold_counts_verified_not_candidate_opportunities():
    """Ten structural-mismatch candidates a day cannot pass the go test; the
    charter's decision rule counts venue-text-only opportunities."""
    observations = [
        _observation(
            f"2026-08-{day:02d}T10:00:00+00:00",
            pair=f"p{day}",
            codes=["THRESHOLD_MISMATCH"],
        )
        for day in range(19, 29)
    ]
    report = study_report(observations, today=date(2026, 8, 28))
    assert report["distinct_opportunities"] == 10
    assert report["venue_text_only_opportunities_total"] == 0
    assert report["meets_frequency_threshold"] is False
    assert report["meets_go_threshold"]["go"] is False


def test_settlement_timing_curve_buckets_by_days_to_settlement():
    """The carry-vs-mispricing curve: gaps bucketed by days until the basket's
    capital unlocks, split by whether the pair carries a settlement-timing
    asymmetry. Observations with no horizon, or past it, are counted apart."""
    observations = [
        _observation("2026-08-20T10:00:00+00:00", pair="p1", gap="0.02", days_to_settlement="3.0"),
        _observation("2026-08-20T10:00:00+00:00", pair="p2", gap="0.04", days_to_settlement="5.0"),
        _observation(
            "2026-08-20T10:00:00+00:00", pair="p3", gap="0.06",
            days_to_settlement="20.0", asymmetric=True,
        ),
        _observation(
            "2026-08-20T10:00:00+00:00", pair="p4", gap="0.10",
            days_to_settlement="30.0", asymmetric=True,
        ),
        _observation("2026-08-20T10:00:00+00:00", pair="p5", gap="0.01", days_to_settlement="45.0"),
        _observation(
            "2026-08-20T10:00:00+00:00", pair="p6", gap="0.20",
            days_to_settlement="120.0", asymmetric=True,
        ),
        _observation("2026-08-20T10:00:00+00:00", pair="p7", gap="0.03"),
        _observation("2026-08-20T10:00:00+00:00", pair="p8", gap="0.03", days_to_settlement="-2.0"),
    ]
    curve = study_report(observations, today=date(2026, 8, 20))["settlement_timing_curve"]
    assert curve["observations_with_horizon"] == 6
    assert curve["observations_without_horizon"] == 1
    assert curve["observations_after_horizon"] == 1
    assert curve["asymmetric_pairs"] == 3
    buckets = {bucket["bucket"]: bucket for bucket in curve["buckets"]}
    assert [bucket["bucket"] for bucket in curve["buckets"]] == ["0-7", "8-30", "31-90", "90+"]
    assert buckets["0-7"]["observations"] == 2
    assert buckets["0-7"]["median_gap"] == "0.03"
    assert buckets["8-30"]["observations"] == 2
    assert buckets["8-30"]["median_gap"] == "0.08"
    assert buckets["31-90"]["observations"] == 1
    assert buckets["31-90"]["median_gap"] == "0.01"
    assert buckets["90+"]["observations"] == 1
    assert buckets["90+"]["median_gap"] == "0.20"


def test_settlement_timing_curve_splits_asymmetric_from_symmetric_pairs():
    """The question the split answers: do asymmetric-settlement pairs carry
    systematically wider gaps than symmetric ones at the same horizon?"""
    observations = [
        _observation("2026-08-20T10:00:00+00:00", pair="s1", gap="0.01", days_to_settlement="10.0"),
        _observation("2026-08-20T10:00:00+00:00", pair="s2", gap="0.03", days_to_settlement="12.0"),
        _observation(
            "2026-08-20T10:00:00+00:00", pair="a1", gap="0.07",
            days_to_settlement="11.0", asymmetric=True,
        ),
        _observation(
            "2026-08-20T10:00:00+00:00", pair="a2", gap="0.09",
            days_to_settlement="13.0", asymmetric=True,
        ),
    ]
    curve = study_report(observations, today=date(2026, 8, 20))["settlement_timing_curve"]
    assert curve["asymmetric_observations"] == 2
    assert curve["symmetric_observations"] == 2
    assert curve["asymmetric_median_gap"] == "0.08"
    assert curve["symmetric_median_gap"] == "0.02"
    bucket = next(item for item in curve["buckets"] if item["bucket"] == "8-30")
    assert bucket["asymmetric_median_gap"] == "0.08"
    assert bucket["symmetric_median_gap"] == "0.02"
    assert bucket["executable_observations"] == 4


def test_settlement_timing_curve_includes_gaps_that_do_not_clear_fees():
    """The curve measures the price relationship, not opportunity counting: a
    negative (non-executable) gap still belongs on it."""
    observations = [
        _observation(
            "2026-08-20T10:00:00+00:00", pair="n1", gap="-0.01",
            executable=False, days_to_settlement="4.0",
        ),
    ]
    report = study_report(observations, today=date(2026, 8, 20))
    curve = report["settlement_timing_curve"]
    assert report["distinct_opportunities"] == 0
    assert curve["observations_with_horizon"] == 1
    bucket = next(item for item in curve["buckets"] if item["bucket"] == "0-7")
    assert bucket["observations"] == 1
    assert bucket["executable_observations"] == 0
    assert bucket["median_gap"] == "-0.01"


def test_legacy_observations_without_the_timing_field_are_symmetric_and_horizonless():
    """Observations persisted before the annotation existed must not crash the
    report or be silently counted as asymmetric."""
    legacy = _observation("2026-08-20T10:00:00+00:00")
    legacy.pop("settlement_timing")
    curve = study_report([legacy], today=date(2026, 8, 20))["settlement_timing_curve"]
    assert curve["observations_with_horizon"] == 0
    assert curve["unannotated_observations"] == 1
    assert curve["asymmetric_pairs"] == 0


def test_unannotated_rows_are_never_pooled_with_missing_venue_anchors():
    """The two reasons a row has no horizon must stay separable.

    A row recorded before the annotation shipped says nothing about what the
    venues published; a row annotated with a null horizon says the venues
    published no usable anchor. Pooling them would permanently understate
    coverage, because the pre-annotation block never shrinks as the study runs.
    """
    legacy = _observation("2026-08-20T10:00:00+00:00", pair="old")
    legacy.pop("settlement_timing")
    no_anchor = _observation("2026-08-20T10:00:00+00:00", pair="new", days_to_settlement=None)
    curve = study_report([legacy, no_anchor], today=date(2026, 8, 20))["settlement_timing_curve"]

    assert curve["unannotated_observations"] == 1
    assert curve["observations_without_horizon"] == 1
    assert curve["annotated_observations"] == 1
    assert curve["annotated_pairs"] == 1


def test_empty_asymmetry_split_names_its_blind_spot_instead_of_reading_as_a_finding():
    """A null asymmetric median beside a populated symmetric one must not read
    as "asymmetry was measured and did not matter" — if no watched pair carries
    the tag, the comparison had no eligible population and was never made."""
    observations = [
        _observation("2026-08-20T10:00:00+00:00", pair="s1", gap="0.01", days_to_settlement="10.0"),
        _observation("2026-08-20T10:00:00+00:00", pair="s2", gap="0.03", days_to_settlement="12.0"),
    ]
    curve = study_report(observations, today=date(2026, 8, 20))["settlement_timing_curve"]

    assert curve["symmetric_median_gap"] == "0.02"
    assert curve["asymmetric_median_gap"] is None
    assert curve["asymmetry_measured"] is False
    assert curve["asymmetry_blind_spot"] == NO_WATCHED_PAIR_PUBLISHES_EARLY_DETERMINATION


def test_no_annotated_rows_at_all_is_a_distinct_blind_spot_from_no_asymmetric_pair():
    legacy = _observation("2026-08-20T10:00:00+00:00")
    legacy.pop("settlement_timing")
    curve = study_report([legacy], today=date(2026, 8, 20))["settlement_timing_curve"]

    assert curve["asymmetry_measured"] is False
    assert curve["asymmetry_blind_spot"] == NO_ANNOTATED_OBSERVATIONS


def test_a_real_asymmetric_pair_clears_the_blind_spot():
    observations = [
        _observation("2026-08-20T10:00:00+00:00", pair="s1", gap="0.01", days_to_settlement="10.0"),
        _observation(
            "2026-08-20T10:00:00+00:00", pair="a1", gap="0.07",
            days_to_settlement="11.0", asymmetric=True,
        ),
    ]
    curve = study_report(observations, today=date(2026, 8, 20))["settlement_timing_curve"]

    assert curve["asymmetry_measured"] is True
    assert curve["asymmetry_blind_spot"] is None


def test_non_executable_observations_never_become_opportunities():
    observations = [
        _observation("2026-08-20T10:00:00+00:00", executable=False),
        _observation("2026-08-20T10:05:00+00:00", gap="-0.01"),
    ]
    report = study_report(observations, today=date(2026, 8, 20))
    assert report["distinct_opportunities"] == 0
    assert report["weekly"][0]["observations"] == 2


def test_post_start_families_are_measured_but_kept_out_of_the_go_threshold():
    """A family added to radar scope mid-study must not raise the headline rate.

    The go/no-go asks whether opportunities occur often enough. If the instrument
    widens mid-flight, the rate rises because we changed what we look at — so the
    new family is measured in full on its own ledger and quarantined from both
    the rate and the weekly table, which is the artifact compared across weeks.
    """
    macro = _observation("2026-08-20T10:00:00+00:00", pair="m1", gap="0.03")
    house = _observation(
        "2026-08-20T10:00:00+00:00", pair="h1", gap="0.09",
        subject="us_house_control|2026",
    )
    report = study_report([macro, house], today=date(2026, 8, 20))

    # Headline stays on the scope frozen at STUDY_START.
    assert report["distinct_opportunities"] == 1
    assert report["frozen_scope_observations"] == 1
    assert report["weekly"][0]["opportunities"] == 1
    assert "us_house_control" not in report["weekly"][0]["families"]

    # ...and the new family is fully counted, clearly labelled, on its own ledger.
    post = report["post_start_scope"]
    assert post["observations"] == 1
    assert post["distinct_opportunities"] == 1
    assert post["excluded_from_go_threshold"] is True
    assert post["family_executable_observations"] == {"us_house_control": 1}
    assert "us_house_control" in post["families"]


def test_post_start_families_still_appear_on_the_settlement_timing_curve():
    """Quarantine applies to the go/no-go rate, NOT to the curve — the chamber
    pairs are the only ones that can carry the asymmetry tag, so excluding them
    there would re-open the very blind spot they were added to close."""
    house = _observation(
        "2026-08-20T10:00:00+00:00", pair="h1", gap="0.09",
        subject="us_house_control|2026", days_to_settlement="165.0", asymmetric=True,
    )
    curve = study_report([house], today=date(2026, 8, 20))["settlement_timing_curve"]

    assert curve["asymmetric_observations"] == 1
    assert curve["asymmetry_measured"] is True
    assert curve["asymmetry_blind_spot"] is None
    bucket = next(item for item in curve["buckets"] if item["bucket"] == "90+")
    assert bucket["asymmetric_median_gap"] == "0.09"


def test_fee_model_rows_still_count_every_observation_regardless_of_scope():
    """Fee provenance is a property of the row, not the scope."""
    macro = _observation("2026-08-20T10:00:00+00:00", pair="m1")
    house = _observation("2026-08-20T10:00:00+00:00", pair="h1", subject="us_house_control|2026")
    report = study_report([macro, house], today=date(2026, 8, 20))
    assert report["fee_model_rows"]["venue_published"] == 2
