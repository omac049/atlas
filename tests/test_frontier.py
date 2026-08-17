from datetime import UTC, datetime, timedelta

import pytest

from atlas.frontier import RULES_CHANGE_RECENT_DAYS, approval_frontier
from atlas.models import VenueName
from atlas.storage import AtlasStore

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def _candidate(subject, *, codes, queue_status="BLOCKED", distance=2, suffix="a"):
    return {
        "event_subject": subject,
        "market_type": "economic",
        "event_action": "binary",
        "guarantee_status": "UNKNOWN",
        "guarantee_reachable": True,
        "rule_distance": distance,
        "mismatch_codes": codes,
        "pair_status": "REVIEW_REQUIRED",
        "queue_status": queue_status,
        "next_gate": "CLEAR_DETERMINISTIC_RULE_MISMATCHES",
        "kalshi_market_id": f"kalshi:K-{suffix}",
        "polymarket_market_id": f"polymarket_us:P-{suffix}",
    }


def _snapshot(market_id, venue, observed_at, rules_hash):
    return {
        "market_id": market_id,
        "venue": venue,
        "observed_at": observed_at,
        "evidence_hash": f"{rules_hash}-{observed_at}",
        "rules_hash": rules_hash,
        "status": "OPEN",
        "outcome": None,
        "reason": "TEST",
        "payload": {},
    }


async def _store(tmp_path, candidates, snapshots=()):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_settlement_candidates(candidates)
    if snapshots:
        await store.save_market_evidence_snapshots(list(snapshots))
    return store


@pytest.mark.asyncio
async def test_frontier_reports_only_blocked_candidates(tmp_path):
    store = await _store(
        tmp_path,
        [
            _candidate("us_cpi_mom|2026-08", codes=["SETTLEMENT_GUARANTEE_UNKNOWN"], suffix="a"),
            _candidate(
                "us_fomc_rate_decision|2026-09",
                codes=[],
                queue_status="AWAITING_SETTLEMENT",
                suffix="b",
            ),
        ],
    )

    report = await approval_frontier(store, now=NOW)

    assert report["paper_only"] is True
    assert report["blocked_candidates"] == 1
    assert [entry["event_subject"] for entry in report["entries"]] == ["us_cpi_mom|2026-08"]


@pytest.mark.asyncio
async def test_frontier_separates_text_clearable_from_structural_blockers(tmp_path):
    store = await _store(
        tmp_path,
        [
            _candidate(
                "us_cpi_mom|2026-08",
                codes=["SETTLEMENT_GUARANTEE_UNKNOWN", "THRESHOLD_OPERATOR_MISMATCH"],
                suffix="a",
            )
        ],
    )

    entry = (await approval_frontier(store, now=NOW))["entries"][0]

    # A different strike is not something a venue can fix by publishing more text.
    assert entry["text_clearable_codes"] == ["SETTLEMENT_GUARANTEE_UNKNOWN"]
    assert entry["structural_codes"] == ["THRESHOLD_OPERATOR_MISMATCH"]
    assert entry["blocked_only_on_venue_text"] is False


@pytest.mark.asyncio
async def test_frontier_flags_and_ranks_a_pair_whose_published_rules_changed(tmp_path):
    recent = (NOW - timedelta(days=1)).isoformat()
    store = await _store(
        tmp_path,
        [
            _candidate("quiet|2026-08", codes=["SETTLEMENT_GUARANTEE_UNKNOWN"], distance=1),
            _candidate(
                "moved|2026-08",
                codes=["SETTLEMENT_GUARANTEE_UNKNOWN"],
                distance=3,
                suffix="b",
            ),
        ],
        snapshots=[
            _snapshot("kalshi:K-a", "kalshi", "2026-08-01T00:00:00+00:00", "hash-quiet"),
            _snapshot("polymarket_us:P-a", "polymarket_us", "2026-08-01T00:00:00+00:00", "hash-q2"),
            _snapshot("kalshi:K-b", "kalshi", "2026-08-01T00:00:00+00:00", "hash-old"),
            _snapshot("kalshi:K-b", "kalshi", recent, "hash-new"),
            _snapshot("polymarket_us:P-b", "polymarket_us", "2026-08-01T00:00:00+00:00", "hash-p"),
        ],
    )

    report = await approval_frontier(store, now=NOW)

    assert report["rules_changed_recently"] == 1
    # The moved pair outranks a closer-but-static one: something actually happened.
    assert [entry["event_subject"] for entry in report["entries"]] == [
        "moved|2026-08",
        "quiet|2026-08",
    ]
    moved = report["entries"][0]
    assert moved["recheck_reason"] == "PUBLISHED_RULES_CHANGED"
    assert moved["kalshi"]["rules_versions"] == 2
    assert moved["kalshi"]["rules_changed_at"] == recent
    assert report["entries"][1]["recheck_reason"] is None


@pytest.mark.asyncio
async def test_frontier_does_not_flag_a_rules_change_older_than_the_window(tmp_path):
    stale = (NOW - timedelta(days=RULES_CHANGE_RECENT_DAYS + 1)).isoformat()
    store = await _store(
        tmp_path,
        [_candidate("stale|2026-08", codes=["SETTLEMENT_GUARANTEE_UNKNOWN"])],
        snapshots=[
            _snapshot("kalshi:K-a", "kalshi", "2026-07-01T00:00:00+00:00", "hash-old"),
            _snapshot("kalshi:K-a", "kalshi", stale, "hash-new"),
            _snapshot("polymarket_us:P-a", "polymarket_us", "2026-07-01T00:00:00+00:00", "hash-p"),
        ],
    )

    entry = (await approval_frontier(store, now=NOW))["entries"][0]

    assert entry["kalshi"]["rules_versions"] == 2
    assert entry["rules_changed_recently"] is False


@pytest.mark.asyncio
async def test_frontier_surfaces_legs_with_no_rules_baseline_as_blind_spots(tmp_path):
    """A leg with no snapshot cannot be compared, so waiting on its text is blind."""
    store = await _store(
        tmp_path,
        [_candidate("unwatched|2026-08", codes=["SETTLEMENT_GUARANTEE_UNKNOWN"])],
        snapshots=[
            _snapshot("kalshi:K-a", "kalshi", "2026-08-01T00:00:00+00:00", "hash-k"),
        ],
    )

    report = await approval_frontier(store, now=NOW)
    entry = report["entries"][0]

    assert entry["kalshi"]["rules_monitored"] is True
    assert entry["polymarket"]["rules_monitored"] is False
    assert entry["unmonitored_legs"] == ["polymarket"]
    assert entry["rules_fully_monitored"] is False
    assert report["unmonitored_pairs"] == 1


@pytest.mark.asyncio
async def test_frontier_capture_records_a_baseline_for_blocked_legs(tmp_path):
    """Blocked legs are exactly what the validation universe skips: a pair is
    blocked because a leg's guarantee is unknown, and Global legs never reach it."""
    from atlas.frontier import capture_frontier_rules_evidence
    from atlas.venues.fixtures import fixture_markets

    markets = fixture_markets()
    kalshi = markets[VenueName.KALSHI][0]
    polymarket = markets[VenueName.POLYMARKET_US][0]
    candidates = [
        {
            **_candidate("blocked|2026-08", codes=["SETTLEMENT_GUARANTEE_UNKNOWN"]),
            "kalshi_market_id": kalshi.market_id,
            "polymarket_market_id": polymarket.market_id,
        },
        {
            **_candidate("settling|2026-09", codes=[], queue_status="AWAITING_SETTLEMENT"),
            "kalshi_market_id": kalshi.market_id,
            "polymarket_market_id": polymarket.market_id,
        },
    ]
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))

    captured = await capture_frontier_rules_evidence(
        store, candidates, [kalshi, polymarket]
    )

    assert captured["frontier_legs_observed"] == 2
    assert captured["frontier_new_versions"] == 2
    assert captured["frontier_legs_unavailable"] == 0

    history = await store.rules_version_history([kalshi.market_id, polymarket.market_id])
    assert set(history) == {kalshi.market_id, polymarket.market_id}


@pytest.mark.asyncio
async def test_frontier_capture_counts_legs_missing_from_the_scan(tmp_path):
    from atlas.frontier import capture_frontier_rules_evidence

    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))

    captured = await capture_frontier_rules_evidence(
        store,
        [_candidate("blocked|2026-08", codes=["SETTLEMENT_GUARANTEE_UNKNOWN"])],
        [],
    )

    # Never silently dropped: an unfetched leg is reported so the blind spot stays visible.
    assert captured["frontier_legs_unavailable"] == 2
    assert captured["frontier_legs_observed"] == 0


@pytest.mark.asyncio
async def test_frontier_capture_clears_the_unmonitored_blind_spot(tmp_path):
    from atlas.frontier import capture_frontier_rules_evidence
    from atlas.venues.fixtures import fixture_markets

    markets = fixture_markets()
    kalshi = markets[VenueName.KALSHI][0]
    polymarket = markets[VenueName.POLYMARKET_US][0]
    candidate = {
        **_candidate("blocked|2026-08", codes=["SETTLEMENT_GUARANTEE_UNKNOWN"]),
        "kalshi_market_id": kalshi.market_id,
        "polymarket_market_id": polymarket.market_id,
    }
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_settlement_candidates([candidate])

    before = await approval_frontier(store, now=NOW)
    assert before["unmonitored_pairs"] == 1

    await capture_frontier_rules_evidence(store, [candidate], [kalshi, polymarket])

    after = await approval_frontier(store, now=NOW)
    assert after["unmonitored_pairs"] == 0
    assert after["entries"][0]["rules_fully_monitored"] is True
