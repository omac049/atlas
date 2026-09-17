from decimal import Decimal

import pytest

from atlas.orderbooks.state import SequenceGapError
from atlas.streams import coordinator as coordinator_module
from atlas.streams import kalshi as kalshi_stream
from atlas.streams.coordinator import StreamCoordinator


@pytest.fixture(autouse=True)
def forget_the_observed_convention():
    kalshi_stream._observed_no_side_in_yes_prices = None
    yield
    kalshi_stream._observed_no_side_in_yes_prices = None


def snapshot(yes, no, seq=1, ticker="KXFEDDECISION-26OCT-H0"):
    """The opening message in the shape Kalshi documents and sends."""
    return {"type": "orderbook_snapshot", "sid": 2, "seq": seq,
            "msg": {"market_ticker": ticker, "yes_dollars_fp": yes, "no_dollars_fp": no}}


def delta(side, price, change, seq, ticker="KXFEDDECISION-26OCT-H0"):
    return {"type": "orderbook_delta", "sid": 2, "seq": seq,
            "msg": {"market_ticker": ticker, "price_dollars": price, "delta_fp": change,
                    "side": side, "ts_ms": 1789840000000}}


YES_SIDE = [["0.0100", "505362.61"], ["0.4900", "27641.00"], ["0.5000", "2668.55"],
            ["0.5100", "3108.74"]]


def test_the_documented_snapshot_loads_its_levels():
    """The defect until 2026-09-17: the snapshot was read under `yes`/`yes_dollars`,
    the feed sends `yes_dollars_fp`, and every book started empty."""
    book = StreamCoordinator().kalshi_event(
        "KXFEDDECISION-26OCT-H0",
        snapshot(YES_SIDE, [["0.0100", "3277125.63"], ["0.4700", "17125.45"], ["0.4800", "8630.00"]]),
    )
    assert [lv.price for lv in book.yes_bids] == [Decimal("0.51"), Decimal("0.50"),
                                                   Decimal("0.49"), Decimal("0.01")]
    assert book.yes_bids[0].quantity == Decimal("3108.74")
    assert min(lv.price for lv in book.yes_asks) == Decimal("0.52")  # 1 - the 48c NO bid


def test_a_no_side_sent_in_yes_prices_is_read_as_the_same_book():
    """Under this subscription the NO side arrives in YES prices (measured against
    the REST book on 2026-09-17). Mirrored a second time it read as a YES ask of
    19c under a 51c bid; read correctly it is the 52c ask."""
    coordinator = StreamCoordinator()
    book = coordinator.kalshi_event(
        "KXFEDDECISION-26OCT-H0",
        snapshot(YES_SIDE, [["0.5200", "8630.00"], ["0.5300", "17125.45"], ["0.8100", "2133.00"]]),
    )
    state = coordinator.states["kalshi:KXFEDDECISION-26OCT-H0"]
    assert state.no_side_in_yes_prices is True
    assert [lv.price for lv in book.no_bids] == [Decimal("0.48"), Decimal("0.47"), Decimal("0.19")]
    assert min(lv.price for lv in book.yes_asks) == Decimal("0.52")
    # A NO-side delta arrives in the same convention and lands on the same level.
    book = coordinator.kalshi_event("KXFEDDECISION-26OCT-H0", delta("no", "0.5200", "-630.00", 2))
    assert book.no_bids[0].price == Decimal("0.48") and book.no_bids[0].quantity == Decimal(8000)
    book = coordinator.kalshi_event("KXFEDDECISION-26OCT-H0", delta("yes", "0.5100", "-108.74", 3))
    assert book.yes_bids[0].quantity == Decimal(3000)


def test_a_one_sided_snapshot_uses_the_convention_last_observed():
    coordinator = StreamCoordinator()
    assert kalshi_stream.no_side_convention([], [Decimal("0.99")]) is None
    coordinator.kalshi_event("A", snapshot(YES_SIDE, [["0.4800", "10.00"]], ticker="A"))  # NO prices
    book = coordinator.kalshi_event("B", snapshot([], [["0.9900", "2071.00"]], ticker="B"))
    assert coordinator.states["kalshi:B"].no_side_in_yes_prices is False
    assert book.yes_asks[0].price == Decimal("0.01")


def test_zero_size_rows_are_not_levels():
    book = StreamCoordinator().kalshi_event(
        "T", snapshot([["0.4000", "0.00"], ["0.3900", "12.00"]], [], ticker="T")
    )
    assert [lv.price for lv in book.yes_bids] == [Decimal("0.39")]


def test_a_crossed_state_is_never_emitted_and_forces_a_resubscribe_if_it_persists(monkeypatch):
    monkeypatch.setattr(coordinator_module, "MAX_CROSSED_STATES", 3)
    coordinator = StreamCoordinator()
    coordinator.kalshi_event("T", snapshot([["0.5100", "10.00"]], [["0.4800", "10.00"]], ticker="T"))
    # A YES bid at 60c cannot rest against a NO bid at 48c.
    assert coordinator.kalshi_event("T", delta("yes", "0.6000", "5.00", 2, ticker="T")) is None
    assert coordinator.kalshi_event("T", delta("yes", "0.6000", "5.00", 3, ticker="T")) is None
    assert coordinator.kalshi_event("T", delta("yes", "0.6000", "5.00", 4, ticker="T")) is None
    with pytest.raises(SequenceGapError):
        coordinator.kalshi_event("T", delta("yes", "0.6000", "5.00", 5, ticker="T"))
    assert coordinator.states["kalshi:T"].synced is False


def test_coordinator_emits_canonical_kalshi_book_after_snapshot():
    coordinator = StreamCoordinator()
    book = coordinator.kalshi_event(
        "TEST",
        {
            "type": "orderbook_snapshot",
            "seq": 1,
            "msg": {"market_ticker": "TEST", "yes": [["0.40", "10"]], "no": []},
        },
    )
    assert book is not None
    assert book.market_id == "kalshi:TEST"
    assert book.no_asks[0].price == Decimal("0.60")


def test_coordinator_parses_polymarket_currency_objects():
    book = StreamCoordinator().polymarket_event(
        {
            "marketData": {
                "marketSlug": "test",
                "bids": [{"px": {"value": "0.41"}, "qty": "4"}],
                "offers": [],
            }
        }
    )
    assert book is not None
    assert book.yes_bids[0].price == Decimal("0.41")


def test_coordinator_parses_polymarket_scalar_prices():
    book = StreamCoordinator().polymarket_event(
        {
            "marketData": {
                "marketSlug": "test",
                "bids": [{"px": "0.41", "qty": "4"}],
                "offers": [{"px": "0.47", "qty": "2"}],
            }
        }
    )
    assert book is not None
    assert book.yes_bids[0].price == Decimal("0.41")
    assert book.yes_asks[0].price == Decimal("0.47")


def test_coordinator_derives_polymarket_no_side_as_exact_complement():
    """APPROVED_EQUIVALENT pairs price the Polymarket leg off asks_for("NO");
    a YES-only stream book would make the live monitor permanently blind."""
    book = StreamCoordinator().polymarket_event(
        {
            "marketData": {
                "marketSlug": "test",
                "bids": [{"px": {"value": "0.41"}, "qty": "4"}],
                "offers": [{"px": {"value": "0.47"}, "qty": "2"}],
            }
        }
    )
    assert book is not None
    assert book.no_bids[0].price == Decimal("0.53")
    assert book.no_bids[0].quantity == Decimal(2)
    assert book.no_asks[0].price == Decimal("0.59")
    assert book.no_asks[0].quantity == Decimal(4)
    assert book.asks_for("NO"), "NO asks must be populated for equivalent pairs"


def test_coordinator_raises_on_sequence_gap_after_synced_snapshot():
    """A gap on a synced book resets the state, and Kalshi only sends a
    snapshot on (re)subscribe — the caller must see the gap and reconnect."""
    coordinator = StreamCoordinator()
    coordinator.kalshi_event(
        "TEST",
        {
            "type": "orderbook_snapshot",
            "seq": 1,
            "msg": {"market_ticker": "TEST", "yes": [["0.40", "10"]], "no": []},
        },
    )
    with pytest.raises(SequenceGapError):
        coordinator.kalshi_event(
            "TEST",
            {
                "type": "orderbook_delta",
                "seq": 5,
                "msg": {"market_ticker": "TEST", "side": "yes", "price": "0.40", "quantity": "5"},
            },
        )


def test_coordinator_ignores_delta_before_first_snapshot():
    """At subscribe time a delta can outrace the snapshot; that is not a gap —
    the snapshot is coming, so the event is simply dropped."""
    coordinator = StreamCoordinator()
    book = coordinator.kalshi_event(
        "TEST",
        {
            "type": "orderbook_delta",
            "seq": 3,
            "msg": {"market_ticker": "TEST", "side": "yes", "price": "0.40", "quantity": "5"},
        },
    )
    assert book is None
