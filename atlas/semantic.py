"""Semantic candidate proposal adapters.

Adapters may suggest relationships, but their output is always review-only.
Atlas's deterministic verifier remains the authority for equivalence.
"""

import asyncio
import json
import os
from collections import Counter
from collections.abc import Sequence
from typing import Any, Protocol

import httpx

from atlas.discovery import propose_market_pairs
from atlas.fingerprints import build_fingerprint
from atlas.models import Market


class SemanticProposer(Protocol):
    async def propose(self, market_a: Sequence[Market], market_b: Sequence[Market], limit: int) -> list[dict[str, Any]]: ...


class LocalSemanticProposer:
    source = "local_lexical_fallback"

    async def propose(self, market_a: Sequence[Market], market_b: Sequence[Market], limit: int) -> list[dict[str, Any]]:
        return propose_market_pairs(list(market_a), list(market_b), limit)


class OpenAISemanticProposer:
    """Optional Responses API adapter for semantic, structured proposals."""

    source = "openai_responses_structured"

    def __init__(self, api_key: str, model: str = "gpt-5", timeout: float = 30.0) -> None:
        self.api_key, self.model, self.timeout = api_key, model, timeout

    @classmethod
    def from_environment(cls) -> "OpenAISemanticProposer | None":
        api_key = os.getenv("OPENAI_API_KEY")
        return cls(api_key, os.getenv("ATLAS_SEMANTIC_MODEL", "gpt-5")) if api_key else None

    async def propose(self, market_a: Sequence[Market], market_b: Sequence[Market], limit: int) -> list[dict[str, Any]]:
        payload = {
            "model": self.model,
            "store": False,
            "input": [
                {"role": "system", "content": "Propose possible cross-venue prediction-market relationships. Never claim equivalence or approve a trade. Return only supplied market IDs."},
                {"role": "user", "content": json.dumps({"kalshi": [_market_summary(m) for m in market_a], "polymarket_us": [_market_summary(m) for m in market_b], "limit": limit})},
            ],
            "text": {"format": {"type": "json_schema", "name": "atlas_candidate_proposals", "strict": True, "schema": {
                "type": "object", "properties": {"proposals": {"type": "array", "items": {"type": "object", "properties": {
                    "kalshi_market_id": {"type": "string"}, "polymarket_market_id": {"type": "string"}, "confidence": {"type": "number"}, "reason": {"type": "string"}
                }, "required": ["kalshi_market_id", "polymarket_market_id", "confidence", "reason"], "additionalProperties": False}}},
                "required": ["proposals"], "additionalProperties": False
            }}}
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post("https://api.openai.com/v1/responses", headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json=payload)
            response.raise_for_status()
        return self._validated_proposals(response.json(), market_a, market_b, limit)

    @staticmethod
    def _validated_proposals(response: dict[str, Any], market_a: Sequence[Market], market_b: Sequence[Market], limit: int) -> list[dict[str, Any]]:
        raw_text = response.get("output_text")
        if not raw_text:
            raw_text = next((content.get("text") for item in response.get("output", []) for content in item.get("content", []) if content.get("type") == "output_text"), None)
        if not raw_text:
            return []
        left, right = {m.market_id: m for m in market_a}, {m.market_id: m for m in market_b}
        result = []
        for item in json.loads(raw_text).get("proposals", [])[:limit]:
            if item.get("kalshi_market_id") not in left or item.get("polymarket_market_id") not in right:
                continue
            result.append({"kalshi_market_id": item["kalshi_market_id"], "polymarket_market_id": item["polymarket_market_id"], "kalshi_title": left[item["kalshi_market_id"]].title, "polymarket_title": right[item["polymarket_market_id"]].title, "score": max(0.0, min(1.0, float(item["confidence"]))), "shared_terms": [], "status": "REVIEW_REQUIRED", "review_kind": "MODEL_PROPOSAL", "model_reason": item["reason"]})
        return result


JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
# Pinned, not `jev-latest`: an alias can move under us and silently change rankings.
JEV_DEFAULT_MODEL = "jev-1.13.0"
JEV_SHORTLIST_FACTOR = 4
JEV_POOL_LIMIT = 100_000  # lexical candidates considered before caps and number checks
# The raw lexical order is dominated by player-prop ladders: live, the top 100
# candidates came from 4 of 392 Kalshi events. Caps spread the shortlist out.
JEV_PER_EVENT = 5
JEV_PER_MARKET = 2
JEV_MIN_P_SAME = 0.2  # below this Jev is confident they differ; don't send to the verifier
JEV_CONCURRENCY = 4
JEV_MAX_ATTEMPTS = 3
JEV_RETRY_DELAY_SECONDS = 2.0
# Jev's accuracy drops as state fills with irrelevant detail; rules boilerplate is long.
JEV_TEXT_CHARS = 1500

# Jev reads literally and is weak on numbers and dates (docs: jev-1.13 jaggedness),
# so numbers are compared in code before a pair reaches Jev (`jev_shortlist`) and
# the questions ask about meaning only. Settlement stays with the deterministic verifier.
JEV_QUESTIONS: dict[str, dict[str, Any]] = {
    "relation": {
        "type": "score",
        "instructions": (
            "Do `market_a` and `market_b` ask the same real-world question: the same "
            "subject, the same outcome, and the same line or threshold? Ignore which "
            "source or authority publishes the result, and ignore wording differences."
        ),
        "criteria": [
            "Different: they are about different events, subjects, or outcomes.",
            (
                "Related: same event or subject, but a different outcome, line, threshold, "
                "or time period."
            ),
            "Same: the same outcome for the same subject with the same line or threshold.",
        ],
    },
    "same_subject": {
        "type": "noul",
        "instructions": "Are `market_a` and `market_b` about the same person, entity, or event?",
    },
    "same_resolution_source": {
        "type": "noul",
        "instructions": (
            "Do `market_a` and `market_b` name the same source or authority to decide the result?"
        ),
    },
    "inverse": {
        "type": "noul",
        "instructions": "Would a Yes on `market_a` correspond to a No on `market_b`?",
    },
}


class JevSemanticProposer:
    """TypeSafe Jev adapter: re-ranks lexical candidates with calibrated probabilities.

    Jev cannot generate pairs, so candidates come from the lexical proposer and Jev
    only orders them. Output is review-only like every other proposer.
    """

    source = "typesafe_jev"

    def __init__(
        self,
        api_key: str,
        model: str = JEV_DEFAULT_MODEL,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key, self.model, self.timeout, self.transport = api_key, model, timeout, transport

    @classmethod
    def from_environment(cls) -> "JevSemanticProposer | None":
        api_key = os.getenv("TYPESAFE_API_KEY") or os.getenv("typesafe_ai")
        return cls(api_key, os.getenv("ATLAS_JEV_MODEL", JEV_DEFAULT_MODEL)) if api_key else None

    async def propose(self, market_a: Sequence[Market], market_b: Sequence[Market], limit: int) -> list[dict[str, Any]]:
        shortlist = jev_shortlist(market_a, market_b, limit * JEV_SHORTLIST_FACTOR)
        if not shortlist:
            return []
        left, right = {m.market_id: m for m in market_a}, {m.market_id: m for m in market_b}
        gate = asyncio.Semaphore(JEV_CONCURRENCY)

        async def rank(client: httpx.AsyncClient, candidate: dict[str, Any]) -> dict[str, Any] | None:
            a, b = left[candidate["kalshi_market_id"]], right[candidate["polymarket_market_id"]]
            async with gate:
                response = await self._ask(client, {"market_a": _jev_summary(a), "market_b": _jev_summary(b)})
            return self._proposal(candidate, response)

        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            results = await asyncio.gather(*(rank(client, c) for c in shortlist), return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors and len(errors) == len(results):
            raise errors[0]
        proposals = [r for r in results if isinstance(r, dict) and r["score"] >= JEV_MIN_P_SAME]
        proposals.sort(key=lambda p: p["score"], reverse=True)
        return proposals[:limit]

    async def _ask(self, client: httpx.AsyncClient, state: dict[str, Any]) -> dict[str, Any]:
        payload = {"model": self.model, "state": state, "questions": JEV_QUESTIONS}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        for attempt in range(JEV_MAX_ATTEMPTS):
            response = await client.post(JEV_ENDPOINT, headers=headers, json=payload)
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt == JEV_MAX_ATTEMPTS - 1:
                break
            await asyncio.sleep(JEV_RETRY_DELAY_SECONDS * (attempt + 1))
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _proposal(candidate: dict[str, Any], response: dict[str, Any]) -> dict[str, Any] | None:
        answers = response.get("answers") or {}
        relation = answers.get("relation") or {}
        try:
            p_same = float(relation["probabilities"]["2"])
            confidence = float(relation.get("confidence", 0.0))
        except (KeyError, TypeError, ValueError):
            return None
        nouls = {
            key: max(0.0, min(1.0, float(answer["noul"])))
            for key, answer in answers.items()
            if key != "relation" and isinstance(answer, dict) and isinstance(answer.get("noul"), int | float)
        }
        p_same = max(0.0, min(1.0, p_same))
        model = response.get("model", "jev")
        detail = ", ".join(f"{key}={value:.2f}" for key, value in sorted(nouls.items()))
        return {
            **candidate,
            "score": p_same,
            "lexical_score": candidate.get("score"),
            "status": "REVIEW_REQUIRED",
            "review_kind": "MODEL_PROPOSAL",
            "model_answers": {"p_same": p_same, "confidence": confidence, **nouls},
            "model_reason": f"{model}: P(same question)={p_same:.2f} confidence={confidence:.2f}; {detail}",
        }


def jev_shortlist(market_a: Sequence[Market], market_b: Sequence[Market], size: int) -> list[dict[str, Any]]:
    """Lexical candidates worth a Jev call: numbers agree, spread across events."""
    pool = propose_market_pairs(list(market_a), list(market_b), JEV_POOL_LIMIT)
    left, right = {m.market_id: m for m in market_a}, {m.market_id: m for m in market_b}
    per_event: Counter[str] = Counter()
    per_market: Counter[str] = Counter()
    shortlist: list[dict[str, Any]] = []
    for candidate in pool:
        a, b = left[candidate["kalshi_market_id"]], right[candidate["polymarket_market_id"]]
        event = str(a.raw_market_json.get("event_ticker") or a.market_id)
        if per_event[event] >= JEV_PER_EVENT or per_market[a.market_id] >= JEV_PER_MARKET:
            continue
        if _numbers_disagree(a, b):
            continue
        per_event[event] += 1
        per_market[a.market_id] += 1
        shortlist.append(candidate)
        if len(shortlist) >= size:
            break
    return shortlist


def _numbers_disagree(a: Market, b: Market) -> bool:
    """True only when both legs state the same kind of line and the lines differ.

    Mixed operators never drop: Kalshi "Hike >25bps" and Polymarket "50+ bps" are
    trusted equivalents on the Fed's 25bp grid (docs/decisions/2026-08-12-fed-rounding-preimage-equality.md).
    Unknown never drops either.
    """
    try:
        fa, fb = build_fingerprint(a), build_fingerprint(b)
    except (ArithmeticError, ValueError):
        return False
    if not fa.threshold_operator or fa.threshold_operator != fb.threshold_operator:
        return False
    return any(
        x is not None and y is not None and x != y
        for x, y in ((fa.threshold, fb.threshold), (fa.threshold_upper, fb.threshold_upper))
    )


def _jev_summary(market: Market) -> dict[str, Any]:
    return {
        "market_id": market.market_id,
        "title": market.title,
        "subtitle": market.subtitle,
        "yes_outcome": market.outcome_yes_label,
        "closes": market.close_time.date().isoformat() if market.close_time else None,
        "rules": (market.raw_rules_text or market.description or "")[:JEV_TEXT_CHARS],
    }


def _market_summary(market: Market) -> dict[str, Any]:
    return {"market_id": market.market_id, "title": market.title, "description": market.description, "rules": market.raw_rules_text, "venue": market.venue.value}
