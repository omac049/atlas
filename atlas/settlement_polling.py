"""Deterministic, paper-only scheduling for settlement evidence checks.

This module plans *observation* work.  It does not fetch venue data, mutate a
market, approve a pair, or place an order.  Callers select venue-specific
terminal evidence endpoints themselves when executing a plan.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from atlas.models import ContractPair


class PendingReasonCode(StrEnum):
    """Machine-readable reasons a pair should remain pending."""

    NOT_CLOSED = "NOT_CLOSED"
    RESOLUTION_NOT_DUE = "RESOLUTION_NOT_DUE"
    CHECKED_TOO_RECENTLY = "CHECKED_TOO_RECENTLY"
    MISSING_SETTLEMENT_TIMING = "MISSING_SETTLEMENT_TIMING"
    TERMINAL_EVIDENCE_MISSING = "TERMINAL_EVIDENCE_MISSING"
    VENUE_EVIDENCE_UNAVAILABLE = "VENUE_EVIDENCE_UNAVAILABLE"
    # No adapter was supplied for the leg's venue. Distinct from
    # VENUE_EVIDENCE_UNAVAILABLE, which means the adapter was asked and could
    # not answer: this one means it was never asked, so retrying is pointless
    # until the caller wires the venue in. Kept visible rather than silent,
    # because a Global leg reconciled through the US adapter would 404 forever
    # and read as a venue outage.
    VENUE_ADAPTER_MISSING = "VENUE_ADAPTER_MISSING"


class PollDisposition(StrEnum):
    READY = "READY"
    PENDING = "PENDING"


@dataclass(frozen=True)
class PollDecision:
    """A deterministic scheduling decision for one pair."""

    pair_id: str
    disposition: PollDisposition
    priority: tuple[int, float, float, str]
    expected_ready_at: datetime | None
    last_checked_at: datetime | None
    pending_reason_codes: tuple[PendingReasonCode, ...]
    next_poll_at: datetime

    @property
    def ready(self) -> bool:
        return self.disposition is PollDisposition.READY


@dataclass(frozen=True)
class PollPlan:
    """Sorted polling work and explicit pending explanations."""

    generated_at: datetime
    paper_only: bool
    decisions: tuple[PollDecision, ...]

    @property
    def ready(self) -> tuple[PollDecision, ...]:
        return tuple(decision for decision in self.decisions if decision.ready)

    @property
    def pending(self) -> tuple[PollDecision, ...]:
        return tuple(decision for decision in self.decisions if not decision.ready)


def plan_settlement_polls(
    pairs: Iterable[ContractPair],
    *,
    now: datetime,
    last_checked_at: dict[str, datetime | str | None] | None = None,
    minimum_check_interval: timedelta = timedelta(hours=1),
) -> PollPlan:
    """Return a stable, readiness-prioritized plan for terminal evidence checks.

    A pair is ready when both legs are closed and the latest known terminal timing
    (resolution, otherwise close) has arrived.  A prior check suppresses repeated
    work until ``minimum_check_interval`` has elapsed, except when no timing is
    known: that case remains pending with an explicit reason rather than being
    treated as ready.

    Sorting is deterministic: ready work first, then expected readiness, then the
    oldest check, then the pair id.  No network, storage, trading, or approval
    operation occurs here.
    """
    now = _aware(now)
    if minimum_check_interval < timedelta(0):
        raise ValueError("minimum_check_interval must not be negative")
    checked = last_checked_at or {}
    decisions = [
        _decision(pair, now, _parse_datetime(checked.get(pair.pair_id)), minimum_check_interval)
        for pair in pairs
    ]
    return PollPlan(
        generated_at=now,
        paper_only=True,
        decisions=tuple(sorted(decisions, key=lambda decision: decision.priority)),
    )


def _decision(
    pair: ContractPair,
    now: datetime,
    checked_at: datetime | None,
    minimum_check_interval: timedelta,
) -> PollDecision:
    expected_ready_at = _expected_ready_at(pair)
    reasons: list[PendingReasonCode] = []
    if expected_ready_at is None:
        reasons.append(PendingReasonCode.MISSING_SETTLEMENT_TIMING)
    else:
        if _latest_close(pair) > now:
            reasons.append(PendingReasonCode.NOT_CLOSED)
        elif expected_ready_at > now:
            reasons.append(PendingReasonCode.RESOLUTION_NOT_DUE)
    if checked_at is not None and now - checked_at < minimum_check_interval:
        reasons.append(PendingReasonCode.CHECKED_TOO_RECENTLY)

    ready = not reasons
    readiness_bucket = 0 if ready else (1 if expected_ready_at is not None else 2)
    readiness_timestamp = expected_ready_at.timestamp() if expected_ready_at else float("inf")
    checked_timestamp = checked_at.timestamp() if checked_at else float("-inf")
    priority = (readiness_bucket, readiness_timestamp, checked_timestamp, pair.pair_id)
    next_poll_at = _next_poll_at(now, expected_ready_at, checked_at, minimum_check_interval)
    return PollDecision(
        pair_id=pair.pair_id,
        disposition=PollDisposition.READY if ready else PollDisposition.PENDING,
        priority=priority,
        expected_ready_at=expected_ready_at,
        last_checked_at=checked_at,
        pending_reason_codes=tuple(reasons),
        next_poll_at=next_poll_at,
    )


def _next_poll_at(
    now: datetime,
    expected_ready_at: datetime | None,
    checked_at: datetime | None,
    minimum_check_interval: timedelta,
) -> datetime:
    candidates = [now + minimum_check_interval]
    if expected_ready_at is not None and expected_ready_at > now:
        candidates.append(expected_ready_at)
    if checked_at is not None:
        candidates.append(checked_at + minimum_check_interval)
    return max(now, min(candidates))


def retry_delay(
    retry_count: int,
    *,
    base: timedelta = timedelta(hours=1),
    maximum: timedelta = timedelta(hours=24),
) -> timedelta:
    """Return bounded exponential delay for a failed terminal evidence poll."""
    if retry_count < 0:
        raise ValueError("retry_count must not be negative")
    return min(maximum, base * (2 ** min(retry_count, 10)))


def _expected_ready_at(pair: ContractPair) -> datetime | None:
    timings = [
        timing
        for market in (pair.market_a, pair.market_b)
        for timing in (_aware_or_none(market.resolution_time), _aware_or_none(market.close_time))
        if timing is not None
    ]
    return max(timings) if timings else None


def _latest_close(pair: ContractPair) -> datetime:
    closes = [
        timing
        for market in (pair.market_a, pair.market_b)
        for timing in (_aware_or_none(market.close_time),)
        if timing is not None
    ]
    return max(closes) if closes else datetime.min.replace(tzinfo=UTC)


def _parse_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value)
    try:
        return _aware(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _aware_or_none(value: datetime | None) -> datetime | None:
    return _aware(value) if value is not None else None
