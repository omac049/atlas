"""Missing venue credentials must be a visible blocker, not silence.

The live paper-trade path (`run_pair`) is spawned with `asyncio.create_task`
from the continuous monitor, so before 2026-08-18 a missing credential raised a
bare KeyError that the event loop swallowed: the pair simply never streamed,
which looks exactly like "no opportunity was found". The credentials themselves
live in the environment (or .env) and are never logged.
"""

import asyncio

import pytest

from atlas.live_monitor import LiveStreamCredentialsMissing, run_pair
from atlas.models import MatchStatus
from atlas.venues.fixtures import fixture_markets
from atlas.verification import verify_equivalence

CREDENTIALS = (
    "KALSHI_API_KEY_ID",
    "KALSHI_PRIVATE_KEY_PATH",
    "POLYMARKET_US_API_KEY",
    "POLYMARKET_US_API_SECRET",
)


def _approved_pair():
    markets = fixture_markets()
    pair = verify_equivalence(markets["kalshi"][0], markets["polymarket_us"][0])
    assert pair.status is MatchStatus.APPROVED_EQUIVALENT
    pair.approved_by = "auto-deterministic"
    return pair


@pytest.mark.asyncio
async def test_missing_credentials_raise_a_named_blocker(monkeypatch):
    for name in CREDENTIALS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(LiveStreamCredentialsMissing) as exc:
        await run_pair(_approved_pair())

    message = str(exc.value)
    for name in CREDENTIALS:
        assert name in message, "every missing credential must be named"


@pytest.mark.asyncio
async def test_partial_credentials_name_only_what_is_missing(monkeypatch):
    monkeypatch.setenv("KALSHI_API_KEY_ID", "present")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", "/tmp/present.pem")
    for name in ("POLYMARKET_US_API_KEY", "POLYMARKET_US_API_SECRET"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(LiveStreamCredentialsMissing) as exc:
        await run_pair(_approved_pair())

    message = str(exc.value)
    assert "POLYMARKET_US_API_KEY" in message
    assert "POLYMARKET_US_API_SECRET" in message
    assert "KALSHI_API_KEY_ID" not in message


@pytest.mark.asyncio
async def test_unapproved_pairs_are_refused_before_credentials_are_read(monkeypatch):
    """The approval gate comes first: an unapproved pair must be refused even
    when every credential is present, so credentials can never widen what is
    allowed to stream."""
    for name in CREDENTIALS:
        monkeypatch.setenv(name, "present")
    markets = fixture_markets()
    unapproved = verify_equivalence(markets["kalshi"][0], markets["polymarket_us"][0])
    unapproved.approved_by = None

    with pytest.raises(ValueError, match="explicitly approved"):
        await run_pair(unapproved)


def test_monitor_reports_credential_blocker_instead_of_swallowing_it():
    """The done-callback must turn a failed pair monitor into a printed
    blocker; create_task otherwise discards the exception entirely."""
    from atlas.cli import _report_pair_monitor_exit

    async def failing():
        raise LiveStreamCredentialsMissing("needs POLYMARKET_US_API_KEY")

    async def drive():
        task = asyncio.ensure_future(failing())
        with pytest.raises(LiveStreamCredentialsMissing):
            await task
        return task

    task = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(drive())
    _report_pair_monitor_exit(task)
