"""Readers that must not be cut to the newest 50,000 observations.

`all_gap_observations()` keeps the newest 50,000 rows by default, about eight days
at the radar's rate by 2026-10-06. Two readers described themselves as more than
that: the public site's 21-day window, and the paper $2k meter, which compounds
every executable gap since the start. Each test runs against a store whose cap
is one row, so any reader still going through the capped load sees one pair, not two.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from atlas.storage import AtlasStore


class OneRowCapStore(AtlasStore):
    async def all_gap_observations(self, limit: int | None = 50000) -> list[dict]:
        return await super().all_gap_observations(limit=None if limit is None else 1)


def _executable(pair: str, at: datetime) -> dict:
    return {
        "observation_id": f"{pair}-{at.isoformat()}",
        "observed_at": at.isoformat(),
        "event_subject": "us_cpi_yoy|2026-08",
        "kalshi_market_id": f"kalshi:K-{pair}",
        "kalshi_title": f"Kalshi {pair}",
        "polymarket_market_id": f"polymarket_us:p-{pair}",
        "polymarket_title": f"Polymarket {pair}",
        "polymarket_venue": "polymarket_us",
        "tradeable_venue_pair": True,
        "verification_status": "REVIEW_REQUIRED",
        "mismatch_codes": ["SETTLEMENT_POLICY_MISMATCH"],
        "executable_gap": True,
        "best_gap": "0.03",
        "best_basket": "kalshi_yes+polymarket_no",
        "baskets": [
            {"legs": "kalshi_yes+polymarket_no", "cost": "0.95", "kalshi_fee": "0.01",
             "polymarket_fee": "0.0125", "kalshi_size": "40", "gap": "0.03"}
        ],
        "settlement_timing": {"asymmetric": False, "days_to_settlement": "7.0"},
    }


async def _seeded(path: str, *observations: dict) -> OneRowCapStore:
    store = OneRowCapStore(path)
    for observation in observations:
        await store.save_gap_observation(observation)
    return store


async def test_observations_since_reads_every_row_from_the_cutoff(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    for day in (1, 2, 3):
        await store.save_gap_observation(
            {"observation_id": f"o{day}", "observed_at": f"2026-09-0{day}T00:00:00+00:00"}
        )
    since = await store.gap_observations_since(datetime(2026, 9, 2, tzinfo=UTC))
    assert [row["observation_id"] for row in since] == ["o2", "o3"]


async def test_executable_observations_without_a_start_read_the_whole_table(tmp_path):
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    await store.save_gap_observation(_executable("a", datetime(2026, 8, 12, tzinfo=UTC)))
    await store.save_gap_observation(_executable("b", datetime(2026, 10, 1, tzinfo=UTC)))
    assert len(await store.executable_gap_observations()) == 2


async def test_the_site_sees_every_pair_in_its_21_day_window(tmp_path, monkeypatch, capsys):
    from atlas import cli

    now = datetime.now(UTC)
    path = str(tmp_path / "atlas.sqlite3")
    await _seeded(
        path,
        _executable("older", now - timedelta(days=10)),
        _executable("newer", now - timedelta(days=1)),
    )
    monkeypatch.setattr(cli, "AtlasStore", lambda: OneRowCapStore(path))

    await cli.site_build(str(tmp_path / "site"), "https://example.test", live=False)

    assert " pairs=2 " in capsys.readouterr().out


async def test_the_status_meter_counts_every_executable_gap_since_the_start(
    tmp_path, monkeypatch, capsys
):
    from atlas import cli

    path = str(tmp_path / "atlas.sqlite3")
    await _seeded(
        path,
        _executable("older", datetime(2026, 8, 20, tzinfo=UTC)),
        _executable("newer", datetime(2026, 10, 1, tzinfo=UTC)),
    )
    monkeypatch.setattr(cli, "AtlasStore", lambda: OneRowCapStore(path))

    await cli.gaps_status()

    summary, _ = json.JSONDecoder().raw_decode(capsys.readouterr().out)
    assert summary["distinct_executable_opportunities"] == 2


@pytest.fixture
def capped_dashboard(tmp_path, monkeypatch):
    import apps.api.main
    from atlas import storage

    path = str(tmp_path / "atlas.sqlite3")

    class TempStore(OneRowCapStore):
        def __init__(self, db_path: str = path):
            super().__init__(db_path)

    monkeypatch.setattr(storage, "AtlasStore", TempStore)
    monkeypatch.setattr(apps.api.main, "_gap_snapshot", None)
    monkeypatch.setattr(apps.api.main, "_gap_refresh", None)
    return path


async def test_the_dashboard_meter_counts_every_executable_gap_since_the_start(
    capped_dashboard,
):
    import httpx

    from apps.api.main import app

    await _seeded(
        capped_dashboard,
        _executable("older", datetime(2026, 8, 20, tzinfo=UTC)),
        _executable("newer", datetime(2026, 10, 1, tzinfo=UTC)),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        payload = (await client.get("/api/overview")).json()
    assert payload["gap_radar"]["summary"]["distinct_executable_opportunities"] == 2
