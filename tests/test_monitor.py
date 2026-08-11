import pytest

from atlas.monitor import run_once
from atlas.storage import AtlasStore


@pytest.mark.asyncio
async def test_monitor_once_persists_books_and_opportunity(tmp_path):
    store = AtlasStore(str(tmp_path / "monitor.sqlite3"))
    opportunity = await run_once(store)
    assert opportunity is not None
    assert len(await store.latest_orderbooks()) == 2
    assert await store.latest_opportunity() is not None
