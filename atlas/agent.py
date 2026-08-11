"""Bounded agent frame for adaptive, paper-only Atlas research runs.

The frame owns planning and tool selection. Existing deterministic modules own
meaning, verification, pricing, and execution safety; an agent can never
promote a review candidate or place a live order through this interface.
"""

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from atlas.arbitrage import calculate_opportunity
from atlas.discovery import (
    compatibility_report,
    propose_market_pairs,
    review_market_pairs,
    scan_market_pairs,
)
from atlas.models import ContractPair, Market, Opportunity
from atlas.paper import PaperExecutor
from atlas.semantic import LocalSemanticProposer, OpenAISemanticProposer, SemanticProposer
from atlas.verification import verify_equivalence

Tool = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class AgentPolicy:
    """Non-bypassable operating limits for an agent run."""

    execution_mode: str = "paper_only"
    max_steps: int = 6
    max_candidates: int = 25

    def validate(self) -> None:
        if self.execution_mode != "paper_only":
            raise ValueError("Atlas agent runs must use execution_mode='paper_only'")
        if self.max_steps < 1:
            raise ValueError("max_steps must be positive")


@dataclass
class AgentStep:
    action: str
    reason: str
    result: dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class AgentRun:
    status: str
    objective: str
    steps: list[AgentStep]
    opportunities: list[Opportunity]
    state: dict[str, Any]

    def model_dump(self) -> dict[str, Any]:
        state = {
            key: [_jsonable(item) for item in value]
            if isinstance(value, list)
            else _jsonable(value)
            for key, value in self.state.items()
        }
        return {
            "status": self.status,
            "objective": self.objective,
            "steps": [
                {
                    "action": step.action,
                    "reason": step.reason,
                    "result": _jsonable(step.result),
                    "created_at": step.created_at.isoformat(),
                }
                for step in self.steps
            ],
            "opportunities": [item.model_dump(mode="json") for item in self.opportunities],
            "state": state,
        }


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


class AtlasAgent:
    """A small observe-plan-act loop around Atlas's safe research primitives.

    Tools are replaceable, so a future model, queue, or scheduler can supply
    different research capabilities without changing verification semantics.
    """

    def __init__(
        self,
        markets: dict[str, list[Market]],
        books: dict[str, Any] | None = None,
        policy: AgentPolicy | None = None,
        tools: dict[str, Tool] | None = None,
        semantic_proposer: SemanticProposer | None = None,
    ) -> None:
        self.markets = markets
        self.books = books or {}
        self.policy = policy or AgentPolicy()
        self.policy.validate()
        self.semantic_proposer = semantic_proposer or (
            OpenAISemanticProposer.from_environment()
            if os.getenv("ATLAS_SEMANTIC_ENABLED") == "1"
            else None
        ) or LocalSemanticProposer()
        self.tools: dict[str, Tool] = {
            "discover_catalogs": self._discover_catalogs,
            "review_candidates": self._review_candidates,
            "verify_candidates": self._verify_candidates,
            "evaluate_opportunities": self._evaluate_opportunities,
        }
        if tools:
            self.tools.update(tools)

    async def run(self, objective: str = "find safe paper-trading opportunities") -> AgentRun:
        state: dict[str, Any] = {"objective": objective}
        steps: list[AgentStep] = []
        opportunities: list[Opportunity] = []

        for _ in range(self.policy.max_steps):
            action, reason = self._next_action(state)
            if action == "stop":
                return AgentRun("completed", objective, steps, opportunities, state)
            if action not in self.tools:
                raise RuntimeError(f"agent selected unregistered tool: {action}")
            if action in {"execute_live", "place_order"}:
                raise PermissionError("live execution is not an Atlas agent capability")
            result = await self.tools[action](state)
            steps.append(AgentStep(action, reason, result))
            state.update(result)
            opportunities.extend(result.get("opportunities", []))

        return AgentRun("step_limit_reached", objective, steps, opportunities, state)

    def _next_action(self, state: dict[str, Any]) -> tuple[str, str]:
        if "catalog" not in state:
            return "discover_catalogs", "observe both venue catalogs before choosing a search path"
        if not state.get("approved_pairs") and not state.get("candidate_reviews"):
            return "review_candidates", "no deterministic pair found; broaden to same-event review"
        if not state.get("approved_pairs") and state.get("candidate_reviews"):
            return "verify_candidates", "review candidates exist; run deterministic rule verification"
        if state.get("approved_pairs") and not state.get("evaluated"):
            return "evaluate_opportunities", "approved pairs exist; inspect executable paper edge"
        return "stop", "research path is exhausted under the active safety policy"

    async def _discover_catalogs(self, _: dict[str, Any]) -> dict[str, Any]:
        left, right = self.markets["kalshi"], self.markets["polymarket_us"]
        return {"catalog": compatibility_report(left, right), "catalog_counts": {"kalshi": len(left), "polymarket_us": len(right)}}

    async def _review_candidates(self, _: dict[str, Any]) -> dict[str, Any]:
        left, right = self.markets["kalshi"], self.markets["polymarket_us"]
        proposal_error = None
        try:
            reviews = await self.semantic_proposer.propose(left, right, self.policy.max_candidates)
        except (httpx.HTTPError, ValueError) as error:
            reviews = []
            response = getattr(error, "response", None)
            status = getattr(response, "status_code", None)
            proposal_error = f"{type(error).__name__}{':' + str(status) if status else ''}"
        if not reviews:
            reviews = review_market_pairs(left, right, self.policy.max_candidates)
        if not reviews:
            reviews = propose_market_pairs(left, right, self.policy.max_candidates)
        source = getattr(self.semantic_proposer, "source", "custom_proposer")
        if proposal_error:
            source = f"{source}_fallback"
        return {
            "candidate_reviews": reviews,
            "proposal_source": source,
            "proposal_count": len(reviews),
            "proposal_error": proposal_error,
        }

    async def _verify_candidates(self, state: dict[str, Any]) -> dict[str, Any]:
        left = {market.market_id: market for market in self.markets["kalshi"]}
        right = {market.market_id: market for market in self.markets["polymarket_us"]}
        pairs: list[ContractPair] = scan_market_pairs(
            self.markets["kalshi"], self.markets["polymarket_us"]
        )
        seen = {pair.pair_id for pair in pairs}
        for proposal in state.get("candidate_reviews", []):
            market_a = left.get(proposal.get("kalshi_market_id"))
            market_b = right.get(proposal.get("polymarket_market_id"))
            if not market_a or not market_b:
                continue
            pair_id = f"{market_a.market_id}::{market_b.market_id}"
            if pair_id not in seen:
                pairs.append(verify_equivalence(market_a, market_b, pair_id))
                seen.add(pair_id)
        return {"verified_pairs": pairs, "approved_pairs": [pair for pair in pairs if pair.decision and not pair.decision.mismatch_codes]}

    async def _evaluate_opportunities(self, state: dict[str, Any]) -> dict[str, Any]:
        opportunities: list[Opportunity] = []
        for pair in state.get("approved_pairs", []):
            book_a = self.books.get(pair.market_a.market_id)
            book_b = self.books.get(pair.market_b.market_id)
            if book_a and book_b:
                opportunity = calculate_opportunity(pair, book_a, book_b, Decimal(100), Decimal("0.83"), Decimal("0.20"))
                if opportunity:
                    PaperExecutor().execute(opportunity)
                    opportunities.append(opportunity)
        return {"evaluated": True, "opportunities": opportunities}
