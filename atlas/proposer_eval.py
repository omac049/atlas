"""Does a proposer's ranking separate trusted APPROVED pairs from REJECTED ones?

Research only. Reads the trusted labels (settlement-verified APPROVED_EQUIVALENT
and REJECTED) and scores each pair three ways: title word overlap (the lexical
proposer's Jaccard), Jev's P(same question), and Jev after the in-code number
check that `jev_shortlist` applies. Nothing here writes a label, feeds the
verifier, or changes a proposal; the verifier is frozen for the 90-day study.

The headline number is ROC AUC: the chance that a random APPROVED pair outranks
a random REJECTED pair (0.5 = coin flip, 1.0 = perfect). REJECTED labels are
hard negatives by construction — the matcher already found them lexically
similar — so lexical overlap is expected to separate them poorly.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from atlas.discovery import _tokens
from atlas.models import Market
from atlas.semantic import (
    JEV_CONCURRENCY,
    JEV_MIN_P_SAME,
    JevSemanticProposer,
    _jev_summary,
    _numbers_disagree,
)

APPROVED = "APPROVED_EQUIVALENT"


def roc_auc(scores: list[float], positives: list[bool]) -> float | None:
    """Mann-Whitney AUC with ties counted as half; None without both classes."""
    pos = [s for s, p in zip(scores, positives, strict=True) if p]
    neg = [s for s, p in zip(scores, positives, strict=True) if not p]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def lexical_overlap(a: Market, b: Market) -> float:
    left, right = _tokens(a.title), _tokens(b.title)
    union = left | right
    return len(left & right) / len(union) if union else 0.0


async def evaluate_proposer(
    examples: list[dict[str, Any]], proposer: JevSemanticProposer
) -> dict[str, Any]:
    pairs = [
        (
            Market.model_validate(example["payload"]["market_a"]),
            Market.model_validate(example["payload"]["market_b"]),
            example["label"] == APPROVED,
        )
        for example in examples
    ]
    gate = asyncio.Semaphore(JEV_CONCURRENCY)

    async def jev(client: httpx.AsyncClient, a: Market, b: Market) -> float | None:
        async with gate:
            response = await proposer._ask(
                client, {"market_a": _jev_summary(a), "market_b": _jev_summary(b)}
            )
        proposal = proposer._proposal({}, response)
        return proposal["score"] if proposal else None

    async with httpx.AsyncClient(timeout=proposer.timeout, transport=proposer.transport) as client:
        jev_scores = await asyncio.gather(*(jev(client, a, b) for a, b, _ in pairs))

    rows = [
        {
            "approved": approved,
            "lexical": lexical_overlap(a, b),
            "jev": score,
            "jev_numbers": 0.0 if _numbers_disagree(a, b) else score,
        }
        for (a, b, approved), score in zip(pairs, jev_scores, strict=True)
        if score is not None
    ]
    labels = [row["approved"] for row in rows]
    report: dict[str, Any] = {
        "model": proposer.model,
        "pairs": len(pairs),
        "scored": len(rows),
        "approved": sum(labels),
        "rejected": len(labels) - sum(labels),
    }
    for method in ("lexical", "jev", "jev_numbers"):
        scores = [row[method] for row in rows]
        approved_scores = [s for s, p in zip(scores, labels, strict=True) if p]
        rejected_scores = [s for s, p in zip(scores, labels, strict=True) if not p]
        report[method] = {
            "auc": roc_auc(scores, labels),
            "mean_approved": _mean(approved_scores),
            "mean_rejected": _mean(rejected_scores),
            # At the proposer's floor: how many true pairs survive, how many false ones do.
            "approved_kept_at_floor": sum(s >= JEV_MIN_P_SAME for s in approved_scores),
            "rejected_kept_at_floor": sum(s >= JEV_MIN_P_SAME for s in rejected_scores),
        }
    return report


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None
