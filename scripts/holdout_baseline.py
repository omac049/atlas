"""Baseline characterization of the frozen holdout, before any training spend.

Answers three questions with no model involved:
  1. What is in the train/eval split (label + family mix)?
  2. What does the majority-class baseline score, i.e. what must a model beat?
  3. How does the deterministic verifier itself score against settlement truth?
"""

import asyncio
import json
from collections import Counter

from atlas.learning import example_family
from atlas.storage import AtlasStore


def load(path):
    with open(path) as handle:
        return [json.loads(line) for line in handle if line.strip()]


def split_report(name, rows):
    labels = Counter(row["messages"][-1]["content"] for row in rows)
    families = Counter(row.get("family", "?") for row in rows)
    total = len(rows)
    print(f"\n{name}: {total} rows")
    for label, count in labels.most_common():
        print(f"  {label:20s} {count:3d}  ({count / total:.0%})")
    print(f"  families: {dict(families)}")
    if labels:
        top, top_count = labels.most_common(1)[0]
        print(f"  majority-class baseline: answer '{top}' every time -> {top_count / total:.1%}")
    return labels


async def verifier_scorecard():
    store = AtlasStore()
    examples = await store.labeled_learning_examples()
    matrix = Counter()
    for example in examples:
        label = example.get("label")
        if label not in {"APPROVED_EQUIVALENT", "REJECTED"}:
            continue
        payload = example.get("payload") or {}
        decision = payload.get("decision") or {}
        verdict = str(decision.get("status") or "UNKNOWN")
        matrix[(verdict, label, example_family(example))] += 1

    print("\nDeterministic verifier vs settlement truth (all 72 trusted labels)")
    print(f"  {'verifier said':22s} {'truth':20s} {'family':10s} count")
    for (verdict, label, family), count in sorted(matrix.items()):
        print(f"  {verdict:22s} {label:20s} {family:10s} {count}")

    approved = {k: v for k, v in matrix.items() if k[0].startswith("APPROVED")}
    approved_total = sum(approved.values())
    approved_correct = sum(v for k, v in approved.items() if k[1] == "APPROVED_EQUIVALENT")
    print(f"\n  Approval precision: {approved_correct}/{approved_total} pairs the verifier")
    print("  approved settled consistently on both venues (false approvals are the")
    print("  only error class that could ever cost real money).")


async def main():
    train = load("data/training/atlas.jsonl")
    evaluation = load("data/training/atlas-eval.jsonl")
    split_report("TRAIN", train)
    eval_labels = split_report("HOLDOUT", evaluation)
    positives = eval_labels.get("APPROVED_EQUIVALENT", 0)
    print(f"\n  Holdout positives: {positives}")
    if positives < 5:
        print("  -> Too few to measure approval performance. Any approval metric from")
        print("     this holdout would be noise, whatever the headline accuracy says.")
    await verifier_scorecard()


asyncio.run(main())
