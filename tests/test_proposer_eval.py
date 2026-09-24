"""Proposer evaluation against trusted labels. No network: Jev is a MockTransport."""

import httpx
import pytest

from atlas.proposer_eval import evaluate_proposer, roc_auc
from atlas.semantic import JevSemanticProposer
from atlas.venues.fixtures import fixture_markets


def test_roc_auc():
    assert roc_auc([0.9, 0.8, 0.1, 0.2], [True, True, False, False]) == 1.0
    assert roc_auc([0.1, 0.9], [True, False]) == 0.0
    assert roc_auc([0.5, 0.5], [True, False]) == 0.5  # ties count half
    assert roc_auc([0.5], [True]) is None


def _example(label: str, title_b: str) -> dict:
    markets = fixture_markets()
    a, b = markets["kalshi"][0], markets["polymarket_us"][0].model_copy(update={"title": title_b})
    return {
        "label": label,
        "payload": {"market_a": a.model_dump(mode="json"), "market_b": b.model_dump(mode="json")},
    }


async def test_evaluate_scores_each_method_and_never_sends_outcomes():
    examples = [
        _example("APPROVED_EQUIVALENT", "twin"),
        _example("REJECTED", "different"),
    ]
    sent: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        p_same = 0.95 if b'"twin"' in request.content else 0.05
        return httpx.Response(200, json={
            "model": "jev-1.13.0",
            "answers": {"relation": {
                "type": "score", "score": 2 * p_same,
                "probabilities": {"0": 1 - p_same, "1": 0.0, "2": p_same}, "confidence": 0.9,
            }},
        })

    proposer = JevSemanticProposer("k", transport=httpx.MockTransport(handler))
    report = await evaluate_proposer(examples, proposer)

    assert report["scored"] == 2 and report["approved"] == 1 and report["rejected"] == 1
    assert report["jev"]["auc"] == 1.0
    assert report["jev"]["mean_approved"] == pytest.approx(0.95)
    assert report["jev"]["rejected_kept_at_floor"] == 0
    assert set(report) >= {"lexical", "jev", "jev_numbers"}
    # Labels and settlement evidence stay local; Jev sees contract text only.
    assert all(b"REJECTED" not in body and b"evidence" not in body for body in sent)
