"""Jev (TypeSafe) proposer: ranks lexical candidates, never approves them.

No network: TypeSafe is an httpx.MockTransport.
"""

import json

import httpx
import pytest

from atlas.agent import AtlasAgent
from atlas.discovery import propose_market_pairs
from atlas.semantic import JevSemanticProposer, LocalSemanticProposer, OpenAISemanticProposer
from atlas.venues.fixtures import fixture_books, fixture_markets


def _answers(p_same: float) -> dict:
    rest = (1.0 - p_same) / 2
    return {
        "model": "jev-1.13.0",
        "answers": {
            "relation": {
                "type": "score",
                "score": 2 * p_same + rest,
                "legend": {"0": "different", "1": "related", "2": "same"},
                "probabilities": {"0": rest, "1": rest, "2": p_same},
                "confidence": 0.9,
            },
            "same_subject": {"type": "noul", "noul": 0.97},
            "same_resolution_source": {"type": "noul", "noul": 0.6},
            "inverse": {"type": "noul", "noul": 0.02},
        },
        "usage": {"input_tokens": 400, "output_tokens": 20},
    }


def _proposer(handler) -> JevSemanticProposer:
    return JevSemanticProposer("test-key", transport=httpx.MockTransport(handler))


async def test_jev_request_shape_and_review_only_output():
    markets = fixture_markets()
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json=_answers(0.8))

    proposals = await _proposer(handler).propose(markets["kalshi"], markets["polymarket_us"], 5)

    assert proposals, "fixture catalogs have one lexical candidate"
    body = seen[0]
    assert body["model"] == "jev-1.13.0"  # pinned, not an alias that can move
    assert set(body["state"]) == {"market_a", "market_b"}
    assert body["questions"]["relation"]["type"] == "score"
    assert len(body["questions"]["relation"]["criteria"]) == 3
    for proposal in proposals:
        assert proposal["status"] == "REVIEW_REQUIRED"
        assert proposal["review_kind"] == "MODEL_PROPOSAL"
        assert proposal["score"] == pytest.approx(0.8)
        assert proposal["model_answers"]["same_subject"] == pytest.approx(0.97)
        assert "jev-1.13.0" in proposal["model_reason"]


async def test_jev_ranks_by_probability_of_same_question():
    markets = fixture_markets()
    lexical = propose_market_pairs(markets["kalshi"], markets["polymarket_us"], 10)
    ranked = {c["polymarket_market_id"]: 0.1 + 0.2 * i for i, c in enumerate(lexical)}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json=_answers(ranked[body["state"]["market_b"]["market_id"]]))

    proposals = await _proposer(handler).propose(markets["kalshi"], markets["polymarket_us"], 10)

    scores = [p["score"] for p in proposals]
    assert scores == sorted(scores, reverse=True)


async def test_jev_skips_malformed_answers_and_clamps():
    markets = fixture_markets()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = _answers(1.7)  # out of range must clamp, not propagate
        del payload["answers"]["inverse"]
        return httpx.Response(200, json=payload)

    proposals = await _proposer(handler).propose(markets["kalshi"], markets["polymarket_us"], 5)

    assert proposals and all(p["score"] == 1.0 for p in proposals)
    assert all("inverse" not in p["model_answers"] for p in proposals)

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "jev-1.13.0", "answers": {}})

    assert await _proposer(broken).propose(markets["kalshi"], markets["polymarket_us"], 5) == []


async def test_jev_total_failure_raises_so_the_agent_falls_back():
    markets = fixture_markets()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "bad key"})

    with pytest.raises(httpx.HTTPStatusError):
        await _proposer(handler).propose(markets["kalshi"], markets["polymarket_us"], 5)
    assert calls == len(propose_market_pairs(markets["kalshi"], markets["polymarket_us"], 20))


async def test_jev_retries_rate_limits_a_bounded_number_of_times(monkeypatch):
    monkeypatch.setattr("atlas.semantic.JEV_RETRY_DELAY_SECONDS", 0)
    markets = fixture_markets()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429) if calls == 1 else httpx.Response(200, json=_answers(0.5))

    proposals = await _proposer(handler).propose(markets["kalshi"], markets["polymarket_us"], 1)
    assert proposals and calls == 2

    def always_limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    with pytest.raises(httpx.HTTPStatusError):
        await _proposer(always_limited).propose(markets["kalshi"], markets["polymarket_us"], 1)


def test_jev_key_from_environment(monkeypatch):
    for name in ("TYPESAFE_API_KEY", "typesafe_ai"):
        monkeypatch.delenv(name, raising=False)
    assert JevSemanticProposer.from_environment() is None
    monkeypatch.setenv("typesafe_ai", "k1")
    assert JevSemanticProposer.from_environment().api_key == "k1"
    monkeypatch.setenv("TYPESAFE_API_KEY", "k2")  # the SDK's standard name wins
    assert JevSemanticProposer.from_environment().api_key == "k2"


@pytest.mark.parametrize(
    ("enabled", "provider", "expected"),
    [
        (None, "jev", LocalSemanticProposer),  # off unless explicitly enabled
        ("1", None, OpenAISemanticProposer),  # existing default is unchanged
        ("1", "jev", JevSemanticProposer),
    ],
)
def test_agent_selects_proposer(monkeypatch, enabled, provider, expected):
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("TYPESAFE_API_KEY", "t")
    for name, value in (("ATLAS_SEMANTIC_ENABLED", enabled), ("ATLAS_SEMANTIC_PROVIDER", provider)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    markets = fixture_markets()
    agent = AtlasAgent(
        {"kalshi": markets["kalshi"], "polymarket_us": markets["polymarket_us"]},
        books=fixture_books(),
    )
    assert isinstance(agent.semantic_proposer, expected)


async def test_agent_run_with_jev_still_verifies_deterministically():
    markets = fixture_markets()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_answers(0.99))

    run = await AtlasAgent(
        {"kalshi": markets["kalshi"], "polymarket_us": markets["polymarket_us"]},
        books=fixture_books(),
        semantic_proposer=_proposer(handler),
    ).run()

    assert run.state["proposal_source"] == "typesafe_jev"
    # A confident model answer is still only a candidate; approval comes from the verifier.
    for pair in run.state["approved_pairs"]:
        assert pair.decision and not pair.decision.mismatch_codes
