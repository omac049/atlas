from atlas.watchlist import HISTORY_POINTS, build_watchlist


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


def test_watchlist_history_is_bounded_and_oldest_first():
    observations = [
        _observation("a|2026-08", f"2026-08-01T00:{index:02d}:00+00:00", f"-0.{index:02d}")
        for index in range(40)
    ]

    row = build_watchlist(observations)["rows"][0]

    assert len(row["history"]) == HISTORY_POINTS
    assert row["history"][0] == "-0.16"
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
