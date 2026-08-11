"""Semantic candidate proposal adapters.

Adapters may suggest relationships, but their output is always review-only.
Atlas's deterministic verifier remains the authority for equivalence.
"""

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


def _market_summary(market: Market) -> dict[str, Any]:
    return {"market_id": market.market_id, "title": market.title, "description": market.description, "rules": market.raw_rules_text, "venue": market.venue.value}
