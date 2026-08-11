import asyncio

import httpx


async def get_json(
    client: httpx.AsyncClient,
    path: str,
    params: dict | None = None,
    attempts: int = 3,
    retry_delay: float = 0.25,
    total_timeout_seconds: float | None = 30.0,
) -> dict | list:
    """Read public venue JSON with retries bounded by a total request budget."""

    async def _with_retries() -> dict | list:
        for attempt in range(attempts):
            try:
                response = await client.get(path, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                    exc.response.status_code == 429 or exc.response.status_code >= 500
                )
                if not retryable or attempt + 1 >= attempts:
                    raise
                await asyncio.sleep(retry_delay * (2**attempt))

        raise RuntimeError("unreachable retry state")

    try:
        return await asyncio.wait_for(_with_retries(), timeout=total_timeout_seconds)
    except TimeoutError as exc:
        raise httpx.TimeoutException(
            f"venue request exceeded total timeout budget: {path}"
        ) from exc
