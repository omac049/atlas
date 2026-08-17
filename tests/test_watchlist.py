from datetime import UTC, datetime, timedelta

from atlas.watchlist import (
    DEFAULT_WINDOW,
    HISTORY_POINTS,
    RECENT_CROSSING_HOURS,
    build_watchlist,
)


def _observation(subject, observed_at, best_gap, *, executable=False, **extra):
    return {
        "event_subject": subject,
        "observed_at": observed_at,
        "best_gap": best_gap,
        "executable_gap": executable,
        "shape": "equivalent_shape",
        "verification_status": "REVIEW_REQUIRED",
        "pair_kind": "CANDIDATE_TWIN_SHAPE_NOT_PROVEN",
        "trusted": False,
        "kalshi_market_id": "kalshi:K-1",
        "polymarket_market_id": "polymarket_global:1",
        **extra,
    }


def test_watchlist_collapses_observations_into_one_row_per_subject():
    watchlist = build_watchlist(
        [
            _observation("a|2026-08", "2026-08-01T00:00:00+00:00", "-0.05"),
            _observation("a|2026-08", "2026-08-02T00:00:00+00:00", "-0.02"),
            _observation("b|2026-08", "2026-08-01T00:00:00+00:00", "-0.01"),
        ]
    )

    assert watchlist["tracked_subjects"] == 2
    assert watchlist["observations_reviewed"] == 3
    row = next(r for r in watchlist["rows"] if r["event_subject"] == "a|2026-08")
    assert row["observations"] == 2
    assert row["best_gap"] == "-0.02"
    assert row["previous_gap"] == "-0.05"
    assert row["narrowest_gap"] == "-0.05"
    assert row["widest_gap"] == "-0.02"


def test_watchlist_reports_direction_from_the_last_two_observations():
    widening = build_watchlist(
        [
            _observation("a|2026-08", "2026-08-01T00:00:00+00:00", "-0.05"),
            _observation("a|2026-08", "2026-08-02T00:00:00+00:00", "-0.01"),
        ]
    )["rows"][0]
    narrowing = build_watchlist(
        [
            _observation("b|2026-08", "2026-08-01T00:00:00+00:00", "-0.01"),
            _observation("b|2026-08", "2026-08-02T00:00:00+00:00", "-0.05"),
        ]
    )["rows"][0]
    first_sighting = build_watchlist(
        [_observation("c|2026-08", "2026-08-01T00:00:00+00:00", "-0.01")]
    )["rows"][0]

    assert widening["direction"] == "WIDENING"
    assert widening["gap_delta"] == "0.04"
    assert narrowing["direction"] == "NARROWING"
    assert first_sighting["direction"] == "NEW"
    assert first_sighting["gap_delta"] is None


def test_watchlist_treats_sub_noise_movement_as_flat():
    """Quote timing and the fee buffer move the gap by a hair every scan; calling
    that a move would make the board look busy when nothing happened."""
    row = build_watchlist(
        [
            _observation("a|2026-08", "2026-08-01T00:00:00+00:00", "-0.0100"),
            _observation("a|2026-08", "2026-08-02T00:00:00+00:00", "-0.0102"),
        ]
    )["rows"][0]

    assert row["direction"] == "FLAT"


def test_watchlist_puts_executable_rows_first_then_widest_gap():
    watchlist = build_watchlist(
        [
            _observation("wide|2026-08", "2026-08-01T00:00:00+00:00", "0.05"),
            _observation("exec|2026-08", "2026-08-01T00:00:00+00:00", "0.01", executable=True),
            _observation("narrow|2026-08", "2026-08-01T00:00:00+00:00", "-0.20"),
        ]
    )

    assert [row["event_subject"] for row in watchlist["rows"]] == [
        "exec|2026-08",
        "wide|2026-08",
        "narrow|2026-08",
    ]
    assert watchlist["executable_now"] == 1
    assert watchlist["widest_gap"] == "0.05"


def test_watchlist_history_is_bounded_and_spans_the_whole_series():
    """Downsampled, not truncated: keeping only the newest N would silently redraw
    a long window as a short one."""
    observations = [
        _observation("a|2026-08", f"2026-08-01T00:{index:02d}:00+00:00", f"-0.{index:02d}")
        for index in range(40)
    ]

    row = build_watchlist(observations)["rows"][0]

    assert len(row["history"]) == HISTORY_POINTS
    assert row["history"][0] == "-0.00"
    assert row["history"][-1] == "-0.39"


def test_watchlist_never_presents_candidates_as_proven_twins():
    watchlist = build_watchlist(
        [_observation("a|2026-08", "2026-08-01T00:00:00+00:00", "0.02", executable=True)]
    )

    assert watchlist["paper_only"] is True
    assert watchlist["pairs_are_candidates_not_proven_twins"] is True
    row = watchlist["rows"][0]
    assert row["trusted"] is False
    assert row["pair_kind"] == "CANDIDATE_TWIN_SHAPE_NOT_PROVEN"
    # Carried through verbatim from the deterministic verifier, never re-derived.
    assert row["verification_status"] == "REVIEW_REQUIRED"


def test_watchlist_skips_observations_with_no_subject():
    watchlist = build_watchlist(
        [
            _observation("", "2026-08-01T00:00:00+00:00", "-0.01"),
            _observation("a|2026-08", "2026-08-01T00:00:00+00:00", "-0.01"),
        ]
    )

    assert watchlist["tracked_subjects"] == 1


def test_watchlist_tolerates_unparseable_gaps():
    watchlist = build_watchlist(
        [
            _observation("a|2026-08", "2026-08-01T00:00:00+00:00", None),
            _observation("a|2026-08", "2026-08-02T00:00:00+00:00", "not-a-number"),
        ]
    )

    row = watchlist["rows"][0]
    assert row["best_gap"] is None
    assert row["history"] == []
    assert watchlist["widest_gap"] is None


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def _at(hours_ago, gap, subject="a|2026-08", **extra):
    stamp = (NOW - timedelta(hours=hours_ago)).isoformat()
    return _observation(subject, stamp, gap, **extra)


def test_watchlist_measures_change_against_each_window_open():
    """The whole point of windows: a pair can be widening on the hour and
    narrowing on the week, and the board must be able to say which."""
    watchlist = build_watchlist(
        [
            _at(100, "0.05"),
            _at(20, "-0.04"),
            _at(0.5, "-0.06"),
            _at(0.1, "-0.02"),
        ],
        now=NOW,
    )
    windows = watchlist["rows"][0]["windows"]

    assert windows["1h"]["open"] == "-0.06"
    assert windows["1h"]["change"] == "0.04"
    assert windows["1h"]["direction"] == "WIDENING"
    assert windows["24h"]["open"] == "-0.04"
    assert windows["24h"]["change"] == "0.02"
    assert windows["7d"]["open"] == "0.05"
    assert windows["7d"]["change"] == "-0.07"
    assert windows["7d"]["direction"] == "NARROWING"


def test_watchlist_window_reports_no_data_instead_of_borrowing_older_readings():
    """A window with no readings must not reuse numbers from outside it, which
    would make a stale pair look freshly observed."""
    watchlist = build_watchlist([_at(50, "-0.03")], now=NOW)
    windows = watchlist["rows"][0]["windows"]

    assert windows["1h"]["observations"] == 0
    assert windows["1h"]["open"] is None
    assert windows["1h"]["change"] is None
    assert windows["1h"]["direction"] == "NO_DATA"
    assert windows["1h"]["history"] == []
    assert windows["7d"]["observations"] == 1


def test_watchlist_window_high_low_are_scoped_to_the_window():
    watchlist = build_watchlist(
        [_at(100, "0.30"), _at(100, "-0.30"), _at(0.2, "-0.01"), _at(0.1, "-0.02")],
        now=NOW,
    )
    windows = watchlist["rows"][0]["windows"]

    assert windows["1h"]["high"] == "-0.01"
    assert windows["1h"]["low"] == "-0.02"
    assert windows["all"]["high"] == "0.30"
    assert windows["all"]["low"] == "-0.30"


def test_watchlist_window_history_keeps_both_ends_of_the_window():
    observations = [_at(20 - (index * 0.5), f"-0.{index:02d}") for index in range(40)]

    history = build_watchlist(observations, now=NOW)["rows"][0]["windows"]["24h"]["history"]

    assert len(history) == HISTORY_POINTS
    assert history[0] == "-0.00"
    assert history[-1] == "-0.39"


def test_watchlist_advertises_its_windows_and_default():
    watchlist = build_watchlist([_at(1, "-0.01")], now=NOW)

    assert watchlist["windows"] == ["1h", "24h", "7d", "all"]
    assert watchlist["default_window"] == DEFAULT_WINDOW
    assert watchlist["generated_at"] == NOW.isoformat()


def test_watchlist_folds_threshold_flicker_into_one_episode():
    """Live pairs flicker across the executable line on almost every scan. One
    observed pair produced 155 rising edges in five days, always at the same gap
    — alerting per edge would bury the next real one."""
    observations = [
        _at(3.0, "0.02", executable=True),
        _at(2.9, "0.01", executable=False),
        _at(2.8, "0.03", executable=True),
        _at(2.7, "0.01", executable=False),
        _at(2.6, "0.02", executable=True),
    ]

    row = build_watchlist(observations, now=NOW)["rows"][0]

    assert row["crossings_total"] == 1
    episode = row["crossings"][0]
    assert episode["observations"] == 3
    # The peak inside the episode, not merely the gap at the moment it opened.
    assert episode["peak_gap"] == "0.03"


def test_watchlist_starts_a_new_episode_after_the_cooldown():
    observations = [
        _at(30, "0.02", executable=True),
        _at(2, "0.04", executable=True),
    ]

    row = build_watchlist(observations, now=NOW)["rows"][0]

    assert row["crossings_total"] == 2
    assert [episode["peak_gap"] for episode in row["crossings"]] == ["0.02", "0.04"]


def test_watchlist_recent_crossings_follow_last_activity_not_episode_start():
    """A pair executable since yesterday is the most current alert there is;
    filtering on when the episode opened would hide exactly that case."""
    observations = [
        _at(hours, "0.03", executable=True) for hours in (40, 39.5, 39, 2, 1.5, 1)
    ]

    watchlist = build_watchlist(observations, now=NOW)

    # Two episodes exist (the 40h-ago run and the recent one), but only the one
    # with activity inside the window is alerted on.
    assert watchlist["rows"][0]["crossings_total"] == 2
    assert len(watchlist["recent_crossings"]) == 1
    event = watchlist["recent_crossings"][0]
    assert event["event_subject"] == "a|2026-08"
    # Reflects the pair's latest reading, which was executable.
    assert event["still_executable"] is True
    assert watchlist["crossing_window_hours"] == RECENT_CROSSING_HOURS


def test_watchlist_drops_episodes_whose_activity_left_the_window():
    watchlist = build_watchlist([_at(40, "0.03", executable=True)], now=NOW)

    assert watchlist["recent_crossings"] == []
    assert watchlist["rows"][0]["crossings_total"] == 1


def test_watchlist_crossing_carries_the_verdict_so_it_cannot_read_as_approval():
    watchlist = build_watchlist([_at(1, "0.03", executable=True)], now=NOW)

    assert watchlist["recent_crossings"][0]["verification_status"] == "REVIEW_REQUIRED"


def test_watchlist_reports_no_episodes_for_a_pair_that_never_became_executable():
    row = build_watchlist([_at(1, "-0.05"), _at(0.5, "-0.04")], now=NOW)["rows"][0]

    assert row["crossings_total"] == 0
    assert row["crossings"] == []
    assert row["last_crossing_at"] is None
