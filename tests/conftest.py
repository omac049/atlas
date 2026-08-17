import pytest


@pytest.fixture(autouse=True)
def block_live_batch_catalog_fetch(monkeypatch):
    """Keep the batch's shared-catalog fetch off the network by default.

    `learning_backfill_batch` prefetches the tag-independent catalog through
    `atlas.cli._prefetch_batch_catalog`, which builds real venues. Tests that
    monkeypatch only the backfill seam would otherwise reach live Kalshi and
    Polymarket. The batch already degrades to per-tag fetching when the shared
    fetch fails, so this raises the caught error type: tests keep their previous
    behavior, and any test that means to exercise sharing patches this itself.
    """

    async def refuse(**_kwargs):
        raise ValueError("live shared-catalog fetch attempted in tests")

    monkeypatch.setattr("atlas.cli._prefetch_batch_catalog", refuse)
