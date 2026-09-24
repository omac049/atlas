"""Jev (TypeSafe) proposer: ranks lexical candidates, never approves them.

No network: TypeSafe is an httpx.MockTransport.
"""

import json
from decimal import Decimal

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


# --- shortlist quality: numbers in code, spread across events, floor, compact state ---


def _leg(base, market_id: str, event: str | None = None):
    raw = {**base.raw_market_json, "event_ticker": event} if event else dict(base.raw_market_json)
    return base.model_copy(update={"market_id": market_id, "raw_market_json": raw})


def _pool(pairs):
    return [
        {"kalshi_market_id": a, "polymarket_market_id": b, "kalshi_title": a, "polymarket_title": b,
         "score": 1.0, "shared_terms": [], "status": "REVIEW_REQUIRED"}
        for a, b in pairs
    ]


class _Fingerprint:
    def __init__(self, threshold, upper=None, operator=">="):
        self.threshold, self.threshold_upper = threshold, upper
        self.threshold_operator = operator if threshold is not None else None


def test_shortlist_drops_pairs_whose_parsed_lines_differ(monkeypatch):
    from atlas import semantic

    base = fixture_markets()
    k, p = base["kalshi"][0], base["polymarket_us"][0]
    kalshi = [_leg(k, "k25"), _leg(k, "k80"), _leg(k, "kunknown")]
    poly = [_leg(p, "p25"), _leg(p, "p10")]
    lines = {"k25": 25, "k80": 80, "kunknown": None, "p25": 25, "p10": 10}
    monkeypatch.setattr(semantic, "build_fingerprint", lambda m: _Fingerprint(lines[m.market_id]))
    monkeypatch.setattr(
        semantic,
        "propose_market_pairs",
        lambda a, b, limit: _pool([("k25", "p25"), ("k80", "p10"), ("kunknown", "p10")]),
    )

    shortlist = semantic.jev_shortlist(kalshi, poly, 10)

    kept = {(c["kalshi_market_id"], c["polymarket_market_id"]) for c in shortlist}
    assert kept == {("k25", "p25"), ("kunknown", "p10")}  # an unknown line never drops a pair


def test_mixed_operators_never_drop_fed_grid_twins(monkeypatch):
    from atlas import semantic

    base = fixture_markets()
    hike = _leg(base["kalshi"][0], "hike-gt25")
    fifty = _leg(base["polymarket_us"][0], "hike-50plus")
    # Trusted APPROVED_EQUIVALENT on the 25bp grid: ">25" and ">=50" are the same bet.
    fingerprints = {"hike-gt25": _Fingerprint(25, operator=">"), "hike-50plus": _Fingerprint(50)}
    monkeypatch.setattr(semantic, "build_fingerprint", lambda m: fingerprints[m.market_id])

    assert semantic._numbers_disagree(hike, fifty) is False


def test_shortlist_caps_each_event_and_market(monkeypatch):
    from atlas import semantic

    base = fixture_markets()
    k, p = base["kalshi"][0], base["polymarket_us"][0]
    kalshi = [_leg(k, f"big{i}", "BIG-EVENT") for i in range(10)] + [_leg(k, "other", "OTHER")]
    poly = [_leg(p, f"p{i}") for i in range(4)]
    pool = [(f"big{i}", f"p{j}") for i in range(10) for j in range(4)] + [("other", "p0")]
    monkeypatch.setattr(semantic, "build_fingerprint", lambda m: _Fingerprint(None))
    monkeypatch.setattr(semantic, "propose_market_pairs", lambda a, b, limit: _pool(pool))

    shortlist = semantic.jev_shortlist(kalshi, poly, 100)

    from collections import Counter

    per_market = Counter(c["kalshi_market_id"] for c in shortlist)
    big = sum(n for m, n in per_market.items() if m.startswith("big"))
    assert big == semantic.JEV_PER_EVENT
    assert max(per_market.values()) <= semantic.JEV_PER_MARKET
    assert per_market["other"] == 1  # a later event still gets in


async def test_confident_different_answers_are_not_proposed():
    markets = fixture_markets()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_answers(0.05))

    assert await _proposer(handler).propose(markets["kalshi"], markets["polymarket_us"], 5) == []


async def test_state_is_compact_and_questions_ignore_the_resolution_source():
    markets = fixture_markets()
    long_rules = "x" * 10_000
    kalshi = [m.model_copy(update={"raw_rules_text": long_rules}) for m in markets["kalshi"]]
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_answers(0.9))

    await _proposer(handler).propose(kalshi, markets["polymarket_us"], 5)

    state = seen[0]["state"]["market_a"]
    assert len(state["rules"]) <= 1500
    assert {"market_id", "title", "rules"} <= set(state)
    assert "ignore which source" in seen[0]["questions"]["relation"]["instructions"].lower()


async def test_agent_verifies_once_then_stops_when_nothing_is_approved():
    markets = fixture_markets()

    class NoMatch:
        source = "test"

        async def propose(self, a, b, limit):
            return [{"kalshi_market_id": "missing", "polymarket_market_id": "missing"}]

    def fifty(m):
        return m.model_copy(update={
            field: getattr(m, field).replace("25", "50")
            for field in ("title", "resolution_text", "raw_rules_text", "description")
            if isinstance(getattr(m, field), str)
        } | {"threshold": Decimal(50)})

    poly = [fifty(m) for m in markets["polymarket_us"]]
    run = await AtlasAgent(
        {"kalshi": markets["kalshi"], "polymarket_us": poly},
        books=fixture_books(),
        semantic_proposer=NoMatch(),
    ).run()

    assert run.state["approved_pairs"] == []
    actions = [step.action for step in run.steps]
    assert actions.count("verify_candidates") == 1
    assert run.status == "completed"
