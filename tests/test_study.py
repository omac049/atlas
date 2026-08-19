"""90-day study metrics (docs/NINETY_DAY_STUDY.md).

Pins the charter's definitions exactly: the pair+UTC-day opportunity dedup, the
survival-run tolerance, the venue-text-only classification, the fee-model split
between legacy flat-buffer rows and venue-published rows, and the go-threshold
arithmetic. The report must be regenerable from persisted observations alone.
"""

from datetime import date

from atlas.study import STUDY_START, study_report


def _observation(
    at: str,
    *,
    pair: str = "p1",
    gap: str = "0.03",
    executable: bool = True,
    codes: list[str] | None = None,
    basket: dict | None = None,
    subject: str = "us_fomc_rate_decision|2027-01",
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
    assert report["meets_go_threshold"] is False


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
    assert report["meets_go_threshold"] is False


def test_non_executable_observations_never_become_opportunities():
    observations = [
        _observation("2026-08-20T10:00:00+00:00", executable=False),
        _observation("2026-08-20T10:05:00+00:00", gap="-0.01"),
    ]
    report = study_report(observations, today=date(2026, 8, 20))
    assert report["distinct_opportunities"] == 0
    assert report["weekly"][0]["observations"] == 2
