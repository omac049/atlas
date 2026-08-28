from decimal import Decimal

from atlas.early_exit import MIN_ENTRY_GAP, early_exit_model
from atlas.study import study_report

D_YES = "kalshi_yes+polymarket_no"
D_NO = "kalshi_no+polymarket_yes"


def _basket(legs: str, cost: str, kalshi_fee: str = "0.01", polymarket_fee: str = "0.01") -> dict:
    gap = Decimal(1) - Decimal(cost) - Decimal(kalshi_fee) - Decimal(polymarket_fee)
    return {
        "legs": legs,
        "cost": cost,
        "kalshi_fee": kalshi_fee,
        "polymarket_fee": polymarket_fee,
        "gap": str(gap),
    }


def _observation(observed_at: str, yes_cost: str, no_cost: str, subject: str = "us_cpi_yoy|2026-09") -> dict:
    return {
        "observed_at": observed_at,
        "event_subject": subject,
        "kalshi_market_id": "kalshi:K1",
        "polymarket_market_id": "polymarket_us:P1",
        "baskets": [_basket(D_YES, yes_cost), _basket(D_NO, no_cost)],
        "settlement_timing": {"days_to_settlement": "100.0"},
    }


def test_exit_fires_when_complement_cost_drops_enough():
    # Entry: D_YES gap = 1 - 0.94 - 0.02 = 0.04. Exit unwind pnl vs D_NO cost:
    # pnl = (2 - no_cost - 0.02) - (0.94 + 0.02); at no_cost 0.98 -> 0.04 = full gap.
    series = [
        _observation("2026-08-01T00:00:00+00:00", "0.94", "1.05"),
        _observation("2026-08-03T00:00:00+00:00", "0.99", "0.98"),
    ]
    result = early_exit_model(series, thresholds=(Decimal(1),))
    summary = result["thresholds"]["1"]
    assert summary["entries"] == 1
    assert summary["exited"] == 1
    assert summary["median_exit_pnl"] == "0.0400"
    assert summary["median_days_to_exit"] == "2.0000"


def test_position_never_reaching_threshold_is_censored_not_dropped():
    series = [
        _observation("2026-08-01T00:00:00+00:00", "0.94", "1.05"),
        _observation("2026-08-11T00:00:00+00:00", "0.94", "1.05"),
    ]
    result = early_exit_model(series, thresholds=(Decimal("0.5"),))
    summary = result["thresholds"]["0.5"]
    assert summary["entries"] == 1
    assert summary["exited"] == 0
    assert summary["censored"] == 1
    assert summary["median_censored_days"] == "10.0000"


def test_first_crossing_exit_uses_the_earliest_qualifying_observation():
    series = [
        _observation("2026-08-01T00:00:00+00:00", "0.94", "1.05"),
        _observation("2026-08-02T00:00:00+00:00", "0.99", "0.98"),
        _observation("2026-08-09T00:00:00+00:00", "0.99", "0.90"),
    ]
    result = early_exit_model(series, thresholds=(Decimal(1),))
    assert result["thresholds"]["1"]["median_days_to_exit"] == "1.0000"


def test_reentry_after_exit_produces_a_second_event():
    series = [
        _observation("2026-08-01T00:00:00+00:00", "0.94", "1.05"),
        _observation("2026-08-02T00:00:00+00:00", "0.99", "0.98"),
        _observation("2026-08-05T00:00:00+00:00", "0.94", "1.05"),
        _observation("2026-08-06T00:00:00+00:00", "0.99", "0.98"),
    ]
    summary = early_exit_model(series, thresholds=(Decimal(1),))["thresholds"]["1"]
    assert summary["entries"] == 2
    assert summary["exited"] == 2


def test_sub_tick_gaps_never_open_a_position():
    # gap = 1 - 0.975 - 0.02 = 0.005 < MIN_ENTRY_GAP (one tick)
    assert Decimal("0.005") < MIN_ENTRY_GAP
    series = [
        _observation("2026-08-01T00:00:00+00:00", "0.975", "1.05"),
        _observation("2026-08-02T00:00:00+00:00", "0.99", "0.98"),
    ]
    summary = early_exit_model(series, thresholds=(Decimal("0.5"),))["thresholds"]["0.5"]
    assert summary["entries"] == 0


def test_annualization_floors_the_holding_period_at_one_day():
    # 30-minute round trip: annualized must use the 1-day floor, and the raw
    # holding period must still be reported honestly.
    series = [
        _observation("2026-08-01T00:00:00+00:00", "0.94", "1.05"),
        _observation("2026-08-01T00:30:00+00:00", "0.99", "0.98"),
    ]
    summary = early_exit_model(series, thresholds=(Decimal(1),))["thresholds"]["1"]
    assert summary["median_days_to_exit"] == "0.0208"
    # pnl 0.04 on capital 0.96 over floored 1 day -> 0.04/0.96*365 = 15.2083
    assert summary["median_annualized"] == "15.2083"


def test_missing_complement_basket_is_skipped_not_fatal():
    entry = _observation("2026-08-01T00:00:00+00:00", "0.94", "1.05")
    broken = _observation("2026-08-02T00:00:00+00:00", "0.99", "0.98")
    broken["baskets"] = [b for b in broken["baskets"] if b["legs"] == D_YES]
    exit_ok = _observation("2026-08-04T00:00:00+00:00", "0.99", "0.98")
    summary = early_exit_model([entry, broken, exit_ok], thresholds=(Decimal(1),))[
        "thresholds"
    ]["1"]
    assert summary["exited"] == 1
    assert summary["median_days_to_exit"] == "3.0000"


def test_hold_to_settlement_comparison_reported_for_same_entries():
    series = [
        _observation("2026-08-01T00:00:00+00:00", "0.94", "1.05"),
        _observation("2026-08-03T00:00:00+00:00", "0.99", "0.98"),
    ]
    summary = early_exit_model(series, thresholds=(Decimal(1),))["thresholds"]["1"]
    # hold: gap 0.04 / capital 0.96 * 365/100 days = 0.1521
    assert summary["median_hold_annualized"] == "0.1521"


def test_study_report_carries_the_early_exit_model():
    report = study_report([])
    model = report["early_exit_model"]
    assert model["paper_only"] is True
    assert model["assumptions"]["depth_modeled"] is False
    assert set(model["thresholds"]) == {"0.5", "0.8", "1.0"}
