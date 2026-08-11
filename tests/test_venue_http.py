import asyncio

import httpx
import pytest

from atlas.venues.http import get_json


@pytest.mark.asyncio
async def test_public_get_retries_timeout_then_succeeds():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("temporary timeout", request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    async with httpx.AsyncClient(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    ) as client:
        assert await get_json(client, "/markets", retry_delay=0) == {"ok": True}
    assert calls == 2


@pytest.mark.asyncio
async def test_public_get_does_not_retry_client_error():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": "bad request"}, request=request)

    async with httpx.AsyncClient(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await get_json(client, "/markets", retry_delay=0)
    assert calls == 1


@pytest.mark.asyncio
async def test_public_get_enforces_total_retry_budget():
    class SlowClient:
        async def get(self, *_args, **_kwargs):
            await asyncio.sleep(1)

    with pytest.raises(httpx.TimeoutException, match="total timeout budget"):
        await get_json(
            SlowClient(),
            "/markets",
            attempts=3,
            total_timeout_seconds=0.01,
        )
