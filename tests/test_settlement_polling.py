from datetime import UTC, datetime, timedelta
from decimal import Decimal

from atlas.models import ContractPair, Market, MatchStatus, VenueName
from atlas.settlement_polling import (
    PendingReasonCode,
    PollDisposition,
    plan_settlement_polls,
)

NOW = datetime(2030, 1, 10, 12, tzinfo=UTC)


def _market(market_id: str, venue: VenueName, close: datetime | None, resolution: datetime | None):
    return Market(
        market_id=market_id,
        venue=venue,
        venue_market_id=market_id,
        title=market_id,
        close_time=close,
        resolution_time=resolution,
        resolution_source="test",
        resolution_text="test",
        event_subject="test",
        event_action="test",
    )


def _pair(pair_id: str, close: datetime | None, resolution: datetime | None):
    return ContractPair(
        pair_id=pair_id,
        market_a=_market(f"{pair_id}-k", VenueName.KALSHI, close, resolution),
        market_b=_market(f"{pair_id}-p", VenueName.POLYMARKET_US, close, resolution),
        status=MatchStatus.APPROVED_EQUIVALENT,
        match_confidence=Decimal(1),
    )


def test_ready_pairs_are_ranked_before_not_yet_ready_pairs():
    ready = _pair("ready", NOW - timedelta(hours=2), NOW - timedelta(hours=1))
    future = _pair("future", NOW + timedelta(hours=1), NOW + timedelta(hours=2))

    plan = plan_settlement_polls([future, ready], now=NOW)

    assert plan.paper_only is True
    assert [decision.pair_id for decision in plan.decisions] == ["ready", "future"]
    assert plan.ready[0].disposition is PollDisposition.READY
    assert plan.pending[0].pending_reason_codes == (PendingReasonCode.NOT_CLOSED,)


def test_resolution_time_controls_readiness_after_both_markets_close():
    pair = _pair("resolving", NOW - timedelta(hours=2), NOW + timedelta(hours=1))

    decision = plan_settlement_polls([pair], now=NOW).decisions[0]

    assert decision.expected_ready_at == NOW + timedelta(hours=1)
    assert decision.pending_reason_codes == (PendingReasonCode.RESOLUTION_NOT_DUE,)


def test_last_checked_time_suppresses_recent_work_and_accepts_iso_strings():
    pair = _pair("recent", NOW - timedelta(hours=2), NOW - timedelta(hours=1))

    recent = plan_settlement_polls(
        [pair],
        now=NOW,
        last_checked_at={"recent": (NOW - timedelta(minutes=30)).isoformat()},
    ).decisions[0]
    old = plan_settlement_polls(
        [pair],
        now=NOW,
        last_checked_at={"recent": NOW - timedelta(hours=2)},
    ).decisions[0]

    assert recent.disposition is PollDisposition.PENDING
    assert recent.pending_reason_codes == (PendingReasonCode.CHECKED_TOO_RECENTLY,)
    assert old.disposition is PollDisposition.READY


def test_missing_timing_is_explicitly_pending_and_order_is_repeatable():
    first = _pair("first", None, None)
    second = _pair("second", NOW - timedelta(hours=1), NOW - timedelta(minutes=30))

    plan_a = plan_settlement_polls([first, second], now=NOW)
    plan_b = plan_settlement_polls([second, first], now=NOW)

    assert [decision.pair_id for decision in plan_a.decisions] == ["second", "first"]
    assert [decision.pair_id for decision in plan_a.decisions] == [
        decision.pair_id for decision in plan_b.decisions
    ]
    assert plan_a.pending[-1].pending_reason_codes == (
        PendingReasonCode.MISSING_SETTLEMENT_TIMING,
    )
