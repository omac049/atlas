"""Repricing lag: the instrument the fourth charter freezes.

These protect the INSTRUMENT: ticker parsing that never guesses, lead changes
derived from scores only, every metric excluding block trades, insufficient
tape reported rather than scored, negative lags kept as-is, and the three
charter criteria applied as a conjunction on an adequate sample.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from atlas.repricing import (
    MIN_GAMES,
    MIN_PLAYS,
    NO_REPRICE_CAP_SECONDS,
    PlayMeasurement,
    Trade,
    evaluate,
    lead_changes,
    measure_play,
    mlb_game_join_key,
    parse_ticker,
    parse_trades,
)

T0 = datetime(2026, 9, 4, 3, 0, 0, tzinfo=UTC)


def _trade(offset_s: float, price: str, count: str = "10", block: bool = False, tid=None) -> Trade:
    return Trade(
        trade_id=tid or f"t{offset_s}",
        at=T0 + timedelta(seconds=offset_s),
        yes_price=Decimal(price),
        count=Decimal(count),
        is_block=block,
    )


def _pre_tape(price: str = "0.60", n: int = 20) -> list[Trade]:
    return [_trade(-120 + i * 5, price) for i in range(n)]


def test_ticker_parses_by_using_the_contract_suffix_to_split_the_clubs():
    parsed = parse_ticker("KXMLBGAME-26SEP032210STLLAD-STL")
    assert parsed is not None
    assert parsed.date_et == "2026-09-03"
    assert parsed.team == "STL" and parsed.opponent == "LAD"
    # Suffix on the other side of the concatenation.
    other = parse_ticker("KXMLBGAME-26SEP032210STLLAD-LAD")
    assert other is not None and other.opponent == "STL"
    assert parsed.join_key == other.join_key


def test_ticker_parsing_never_guesses():
    """A suffix that is not a prefix or suffix of the club pair is unparseable."""
    assert parse_ticker("KXMLBGAME-26SEP032210STLLAD-NYY") is None
    assert parse_ticker("KXNFLGAME-26SEP032210STLLAD-STL") is None


def test_mlb_join_key_uses_the_eastern_date_like_the_ticker_does():
    """A 22:10 ET first pitch is 02:10 UTC the NEXT day. The ticker says
    SEP03; the join must too."""
    key = mlb_game_join_key("2026-09-04T02:10:00Z", "STL", "LAD")
    assert key == ("2026-09-03", frozenset({"STL", "LAD"}))
    # Alias normalization on both sides.
    assert mlb_game_join_key("2026-09-04T02:10:00Z", "ARI", "SFG")[1] == frozenset({"AZ", "SF"})


def test_lead_changes_derive_from_scores_only_and_name_who_benefited():
    plays = [
        {"result": {"event": "Single", "homeScore": 0, "awayScore": 1},
         "about": {"endTime": "2026-09-04T02:18:55.025Z", "inning": 1, "halfInning": "top"}},
        {"result": {"event": "Groundout", "homeScore": 0, "awayScore": 1},
         "about": {"endTime": "2026-09-04T02:30:00.000Z", "inning": 2, "halfInning": "top"}},
        {"result": {"event": "Single", "homeScore": 0, "awayScore": 2},
         "about": {"endTime": "2026-09-04T02:56:41.977Z", "inning": 3, "halfInning": "top"}},
        {"result": {"event": "Double", "homeScore": 3, "awayScore": 2},
         "about": {"endTime": "2026-09-04T05:09:33.593Z", "inning": 9, "halfInning": "bottom"}},
    ]
    changes = lead_changes(plays)
    # 0-0 -> 0-1 is a lead change; 0-1 -> 0-2 is not; 0-2 -> 3-2 is.
    assert [c.benefiting for c in changes] == ["away", "home"]
    assert changes[1].score_before == (0, 2) and changes[1].score_after == (3, 2)


def test_measurement_excludes_block_trades_from_every_metric():
    tape = _pre_tape() + [
        _trade(2, "0.90", count="500", block=True),   # negotiated, ignored
        _trade(10, "0.60", count="30"),               # stale fill in window
        _trade(70, "0.80"), _trade(71, "0.80"), _trade(72, "0.80"),
        _trade(73, "0.80"), _trade(74, "0.80"),
    ]
    m = measure_play(tape, T0, "KXMLBGAME-X-HOME")
    assert m.unmeasurable is None
    assert m.pre_price == Decimal("0.60")
    # The block print at +2s would have counted as the reprice; excluded, the
    # first real reprice is the 0.80 print at +70s.
    assert m.repricing_lag_seconds == Decimal(70)
    assert m.stale_contracts == Decimal(30)
    assert m.stale_trade_ids == ["t10"]
    assert m.net_gap is not None and m.net_gap > 0


def test_stale_fills_respect_the_human_floor_and_the_window():
    tape = _pre_tape() + [
        _trade(3, "0.60", count="100"),   # before the 5s floor: a bot's fill, not a human's
        _trade(30, "0.61", count="20"),   # inside window, inside 2c band
        _trade(61, "0.60", count="100"),  # after the window
        *[_trade(120 + i, "0.75") for i in range(5)],
    ]
    m = measure_play(tape, T0, "X")
    assert m.stale_contracts == Decimal(20)


def test_a_play_that_never_reprices_is_capped_not_infinite():
    tape = _pre_tape() + [_trade(10 + i, "0.60") for i in range(200)]
    m = measure_play(tape, T0, "X")
    assert m.no_reprice is True
    assert m.repricing_lag_seconds == Decimal(NO_REPRICE_CAP_SECONDS)


def test_negative_lag_is_kept_as_is_never_clipped():
    """The market moved BEFORE the stringer finalized the play: a fast maker
    beat the public feed. That counts against the theory and must survive as
    a NEGATIVE lag — not vanish into P0, and not clip to zero."""
    tape = _pre_tape("0.60", n=20)  # all prints > 30s before T0
    early = [_trade(-3, "0.80", tid="early"), _trade(-2, "0.80"), _trade(-1, "0.80")]
    post = [_trade(70 + i, "0.80") for i in range(5)]
    m = measure_play(tape + early + post, T0, "X")
    assert m.pre_price == Decimal("0.60")  # the early move did NOT contaminate P0
    assert m.repricing_lag_seconds == Decimal(-3)
    assert m.repriced_trade_id == "early"


def test_insufficient_tape_is_unmeasurable_not_zero():
    m = measure_play([_trade(-10, "0.60")], T0, "X")
    assert m.unmeasurable == "INSUFFICIENT_PRE_TAPE"
    m2 = measure_play(_pre_tape() + [_trade(10, "0.70")], T0, "X")
    assert m2.unmeasurable == "INSUFFICIENT_POST_TAPE"


def test_parse_trades_sorts_and_types_kalshi_rows():
    rows = [
        {"trade_id": "b", "created_time": "2026-09-04T03:00:05.500000Z",
         "yes_price_dollars": "0.6100", "count_fp": "12.5", "is_block_trade": False},
        {"trade_id": "a", "created_time": "2026-09-04T03:00:01.000000Z",
         "yes_price_dollars": "0.6000", "count_fp": "3", "is_block_trade": True},
        {"trade_id": "bad", "created_time": "nope"},
    ]
    trades = parse_trades(rows)
    assert [t.trade_id for t in trades] == ["a", "b"]
    assert trades[0].is_block is True and trades[1].count == Decimal("12.5")


def _measured(lag: str, stale: str, gap: str) -> PlayMeasurement:
    return PlayMeasurement(
        t0="x", benefiting_ticker="x", pre_price=Decimal("0.60"),
        repricing_lag_seconds=Decimal(lag), stale_contracts=Decimal(stale),
        post_price=Decimal("0.70"), net_gap=Decimal(gap),
    )


def test_all_three_criteria_are_required_and_a_thin_sample_is_inconclusive():
    strong = [_measured("12", "40", "0.05") for _ in range(MIN_PLAYS)]
    report = evaluate(strong, games_covered=MIN_GAMES)
    assert report["outcome"] == "PROVEN"

    fast_market = [_measured("1.2", "40", "0.05") for _ in range(MIN_PLAYS)]
    assert evaluate(fast_market, games_covered=MIN_GAMES)["outcome"] == "DISPROVEN"

    thin = evaluate(strong[:10], games_covered=MIN_GAMES)
    assert thin["outcome"] == "INCONCLUSIVE_SAMPLE_TOO_SMALL"
    few_games = evaluate(strong, games_covered=MIN_GAMES - 1)
    assert few_games["outcome"] == "INCONCLUSIVE_SAMPLE_TOO_SMALL"


def test_unmeasurable_plays_are_counted_by_reason_never_silently_dropped():
    rows = [_measured("12", "40", "0.05") for _ in range(3)]
    rows.append(PlayMeasurement(t0="x", benefiting_ticker="x", unmeasurable="INSUFFICIENT_PRE_TAPE"))
    report = evaluate(rows, games_covered=1)
    assert report["plays_total"] == 4 and report["plays_measurable"] == 3
    assert report["plays_unmeasurable"] == {"INSUFFICIENT_PRE_TAPE": 1}


def test_the_approval_pipeline_never_imports_the_repricing_instrument():
    import importlib
    import sys

    for name in list(sys.modules):
        if name.startswith("atlas.repricing"):
            del sys.modules[name]
    for module in ("atlas.normalization", "atlas.settlement", "atlas.verification"):
        importlib.import_module(module)
    assert not any(name.startswith("atlas.repricing") for name in sys.modules)
