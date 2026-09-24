"""Semantic candidate proposal adapters.

Adapters may suggest relationships, but their output is always review-only.
Atlas's deterministic verifier remains the authority for equivalence.
"""

import asyncio
import json
import os
from collections.abc import Sequence
from typing import Any, Protocol

import httpx

from atlas.discovery import propose_market_pairs
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
JEV_CONCURRENCY = 4
JEV_MAX_ATTEMPTS = 3
JEV_RETRY_DELAY_SECONDS = 2.0
JEV_RULES_CHARS = 4000  # keep each request far below Jev's 32k state budget

# Jev reads literally and is weak on numbers and dates (docs: jev-1.13 jaggedness),
# so the questions ask about meaning only. Thresholds, deadlines, and settlement
# stay with the deterministic verifier.
JEV_QUESTIONS: dict[str, dict[str, Any]] = {
    "relation": {
        "type": "score",
        "instructions": (
            "Do `market_a` and `market_b` ask the same real-world question, so that one "
            "outcome would settle both the same way?"
        ),
        "criteria": [
            "Different: they are about different events, subjects, or outcomes.",
            (
                "Related: same topic or event, but the subject, outcome, threshold, date, or "
                "resolution source may differ."
            ),
            "Same: the same outcome for the same subject, judged the same way.",
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
        shortlist = propose_market_pairs(list(market_a), list(market_b), limit * JEV_SHORTLIST_FACTOR)
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
        proposals = [r for r in results if isinstance(r, dict)]
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


def _jev_summary(market: Market) -> dict[str, Any]:
    summary = _market_summary(market)
    summary["rules"] = (summary["rules"] or "")[:JEV_RULES_CHARS]
    return summary


def _market_summary(market: Market) -> dict[str, Any]:
    return {"market_id": market.market_id, "title": market.title, "description": market.description, "rules": market.raw_rules_text, "venue": market.venue.value}
