"""Fifth charter instrument: every rule in section 4, on synthetic tapes only.

No network and no real market. The charter allows instrument validation before
the freeze only on synthetic tapes and the excluded probe game; these are the
synthetic tapes.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from atlas import making
from atlas.making import Book, Level, MarketResult, Params, Print, replay

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
END = T0 + timedelta(minutes=10)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def prt(seconds: float, price_cents: int, count: str, taker_yes: bool, block: bool = False,
        tid: str | None = None) -> Print:
    return Print(trade_id=tid or f"t{seconds}", at=at(seconds), price_cents=price_cents,
                 count=Decimal(count), taker_bought_yes=taker_yes, is_block=block)


def book(seconds: float, bids: list[tuple[int, str]], asks: list[tuple[int, str]]) -> Book:
    return Book(
        at=at(seconds),
        bids=tuple(Level(p, Decimal(q)) for p, q in sorted(bids, reverse=True)),
        asks=tuple(Level(p, Decimal(q)) for p, q in sorted(asks)),
    )


def anchor() -> Print:
    """A first print at the window start so Arm B has a reference from the second interval on."""
    return prt(0, 50, "1", True, tid="anchor")


def run_b(prints: list[Print], params: Params = making.PRIMARY, result="yes") -> MarketResult:
    return replay("m", "B", [anchor(), *prints], T0, END, result, params)


def test_parse_prints_types_sorts_and_flags_blocks():
    rows = [
        {"trade_id": "b", "created_time": "2026-09-01T12:00:05Z", "yes_price_dollars": "0.6200",
         "count_fp": "12.50", "taker_side": "no", "is_block_trade": False},
        {"trade_id": "a", "created_time": "2026-09-01T12:00:01Z", "yes_price_dollars": "0.6100",
         "count_fp": "3", "taker_side": "yes", "is_block_trade": True},
        {"trade_id": "bad", "created_time": "not a time", "yes_price_dollars": "0.5"},
    ]
    prints = making.parse_prints(rows)
    assert [p.trade_id for p in prints] == ["a", "b"]
    assert prints[0].is_block and prints[0].taker_bought_yes and prints[0].price_cents == 61
    assert prints[1].count == Decimal("12.50") and not prints[1].taker_bought_yes


def test_reference_from_prints_is_the_vwap_of_the_last_20_non_block_prints():
    tape = [prt(i, 40, "1", True) for i in range(25)]  # 25 old prints at 40c
    tape += [prt(30 + i, 60, "1", True) for i in range(20)]  # then 20 at 60c
    tape.append(prt(55, 99, "100", True, block=True))  # a block print, ignored
    ref = making.reference_from_prints(tape, len(tape))
    assert ref == Decimal("0.60")
    assert making.reference_from_prints(tape, 0) is None


def test_reference_from_book_is_the_mid_or_none_when_a_side_is_empty():
    index = making.BookIndex([book(0, [(48, "10")], [(52, "5")]), book(60, [(40, "1")], [])])
    assert making.reference_from_book(index, at(30)) == Decimal("0.50")
    assert making.reference_from_book(index, at(61)) is None  # latest book has no asks
    assert making.reference_from_book(index, at(-1)) is None


def test_quote_prices_round_to_the_cent_and_clip_to_one_and_ninety_nine():
    assert making.quote_prices(Decimal("0.505"), 2) == (49, 53)
    assert making.quote_prices(Decimal("0.01"), 2) == (1, 3)
    assert making.quote_prices(Decimal("0.99"), 2) == (97, 99)


def test_fees_follow_the_published_formulas():
    assert making.maker_fee(Decimal(10), 50) == Decimal("0.05")  # 0.04375 rounded up
    assert making.maker_fee(Decimal(10), 90) == Decimal("0.02")  # 0.01575 rounded up
    assert making.maker_fee(Decimal(1), 50) == Decimal("0.01")
    assert making.taker_fee(Decimal(10), 50) == Decimal("0.20")  # 0.0175 -> 0.02 per contract


def test_arm_b_fills_only_on_strict_crossing_and_ignores_blocks():
    prints = [
        prt(31.5, 48, "4", False, tid="at-bid"),  # taker sold AT our bid: no fill
        prt(32.0, 47, "4", False, tid="below-bid"),  # strictly below: fills 4
        prt(32.5, 40, "50", False, block=True, tid="block"),  # block: ignored
        prt(33.0, 52, "3", True, tid="at-ask"),  # taker bought AT our ask: no fill
        prt(33.5, 53, "30", True, tid="above-ask"),  # strictly above: fills min(10, 30)
    ]
    out = run_b(prints)
    assert [(f.side, f.price_cents, str(f.quantity), f.print_id) for f in out.fills] == [
        ("bid", 48, "4", "below-bid"), ("ask", 52, "10", "above-ask"),
    ]
    assert out.trading_cash == Decimal("-1.92") + Decimal("5.20")
    assert out.fees == making.maker_fee(Decimal(4), 48) + making.maker_fee(Decimal(10), 52)


def test_quotes_go_live_only_after_the_latency():
    early = run_b([prt(30.5, 40, "5", False)])  # before t + 1 s
    late = run_b([prt(31.5, 40, "5", False)])
    assert early.fills == [] and len(late.fills) == 1
    slow = run_b([prt(35.5, 40, "5", False)], making.SLOW)  # 5 s latency: live from 35 s
    too_early_for_slow = run_b([prt(34.5, 40, "5", False)], making.SLOW)
    assert len(slow.fills) == 1 and too_early_for_slow.fills == []


def test_unfilled_quantity_does_not_carry_between_requotes():
    # The first fill's print at 40c pulls the trade-weighted reference down, so the
    # second print sits well below it to be sure it crosses whatever bid is quoted.
    out = run_b([prt(31.5, 40, "4", False), prt(61.5, 30, "50", False)])
    assert [str(f.quantity) for f in out.fills] == ["4", "10"]


def test_inventory_cap_stops_the_side_that_would_add_exposure():
    params = Params(inventory_cap=10)
    prints = [prt(31.5, 40, "10", False), prt(61.5, 30, "10", False)]
    capped = run_b(prints, params)
    assert [str(f.quantity) for f in capped.fills] == ["10"]  # at +10 the bid is not quoted
    # 19 quoting intervals in the window: both sides once, then only the ask.
    assert capped.quotes_placed == 2 + 18
    uncapped = run_b(prints)
    assert [str(f.quantity) for f in uncapped.fills] == ["10", "10"]


def test_arm_a_waits_behind_displayed_size_then_fills_the_excess():
    books = [book(0, [(48, "30")], [(52, "5")])]  # mid 50: our bid 48 sits behind 30, ask behind 5
    prints = [
        prt(0, 50, "1", True, tid="anchor"),
        prt(1.5, 48, "20", False, tid="q1"),  # seen 20 <= 30: nothing
        prt(2.0, 47, "15", False, tid="q2"),  # seen 35: excess 5 -> fill 5
        prt(3.0, 48, "20", False, tid="q3"),  # seen 55: excess 20, 5 left -> fill 5
        prt(4.0, 52, "3", True, tid="a1"),  # seen 3 <= 5: nothing
        prt(5.0, 55, "4", True, tid="a2"),  # seen 7: excess 2 -> fill 2
    ]
    out = replay("m", "A", prints, T0, END, "no", books=books)
    assert [(f.side, str(f.quantity), f.print_id) for f in out.fills] == [
        ("bid", "5", "q2"), ("bid", "5", "q3"), ("ask", "2", "a2"),
    ]


def test_arm_a_improving_the_book_means_nothing_ahead():
    books = [book(0, [(45, "30")], [(55, "5")])]  # mid 50: bid 48 improves on 45, ask 52 on 55
    prints = [prt(1.5, 46, "3", False, tid="s"), prt(2.0, 54, "2", True, tid="b")]
    out = replay("m", "A", prints, T0, END, "no", books=books)
    assert [(f.side, str(f.quantity)) for f in out.fills] == [("bid", "3"), ("ask", "2")]


def test_settlement_marks_the_position_and_unsettled_markets_are_excluded():
    won = run_b([prt(31.5, 47, "10", False)], result="yes")
    assert won.settlement_cash == Decimal(10)
    assert won.net_pnl == Decimal("-4.80") + Decimal(10) - making.maker_fee(Decimal(10), 48)
    lost = run_b([prt(31.5, 47, "10", False)], result="no")
    assert lost.settlement_cash == Decimal(0)
    pending = run_b([prt(31.5, 47, "10", False)], result=None)
    assert not pending.settled and pending.settlement_cash == Decimal(0)
    assert making.adequate("B", [pending] * 200) == (False, "0 settled games (need 100)")


def test_peak_collateral_counts_resting_orders_and_open_basis():
    out = run_b([prt(31.5, 47, "10", False)])
    # Interval 2 rests 10 @ 48c and 10 @ 52c: 4.80 + 4.80. After the fill, interval 3
    # rests both again on top of a 4.80 long basis: 14.40.
    assert out.peak_collateral == Decimal("14.40")


def _settled(net: str, contracts: str = "10", prints: int = 600, settled: bool = True) -> MarketResult:
    r = MarketResult("m", "B", "p", T0, END, prints, settled, "yes" if settled else None)
    r.trading_cash = Decimal(net)
    r.contracts_filled = Decimal(contracts)
    return r


def test_verdict_needs_an_adequate_sample_then_all_four_criteria():
    good = [_settled("1.00") for _ in range(100)]
    assert making.verdict("B", good, good)["verdict"] == "PASS"
    assert making.verdict("B", good[:99], good[:99])["verdict"] == "INCONCLUSIVE"
    slow_loses = [_settled("-1.00") for _ in range(100)]
    v = making.verdict("B", good, slow_loses)
    assert v["verdict"] == "FAIL" and v["primary"]["c1_earns"] and not v["c4_survives_slow"]
    one_lucky = [_settled("50.00")] + [_settled("-0.10") for _ in range(99)]
    assert making.verdict("B", one_lucky, one_lucky)["primary"]["c2_not_one_market"] is False
    noise = [_settled("0.01", contracts="10") for _ in range(100)]  # 0.1c per contract
    assert making.verdict("B", noise, noise)["primary"]["c3_clears_noise"] is False
    arm_a = [_settled("1.00", prints=600) for _ in range(4)]
    assert making.adequate("A", arm_a)[0] and not making.adequate("A", arm_a[:3])[0]
    thin = [_settled("1.00", prints=499) for _ in range(4)]
    assert not making.adequate("A", thin)[0]


def test_primary_parameters_and_grid_are_the_charter_values():
    assert making.PRIMARY == Params(2, 10, 1.0, 30.0, 100, "maker")
    assert making.SLOW.latency_seconds == 5.0
    assert len(making.grid()) == 4 * 3 * 2 * 2
    assert {p.half_spread_cents for p in making.grid()} == {1, 2, 3, 5}


def test_parse_books_orders_levels_and_reports_displayed_size():
    payload = ('{"yes_bids":[{"price":"0.4800","quantity":"30.00"},'
               '{"price":"0.4900","quantity":"2"}],"yes_asks":[{"price":"0.5200","quantity":"5"}]}')
    rows = [("2026-09-01T12:01:00+00:00", payload),
            ("2026-09-01T12:00:00+00:00", '{"yes_bids":[],"yes_asks":[]}')]
    books = making.parse_books(rows)
    assert [b.at.minute for b in books] == [0, 1]
    assert books[1].best_bid == 49 and books[1].best_ask == 52
    assert books[1].displayed("bid", 48) == Decimal("30.00")
    assert books[1].displayed("bid", 47) == Decimal(0)
