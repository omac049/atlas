import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from atlas.models import ContractPair
from atlas.storage import AtlasStore

TRUSTED_LEARNING_LABELS = frozenset({"APPROVED_EQUIVALENT", "REJECTED"})


async def record_verified_pair(
    store: AtlasStore,
    pair: ContractPair,
    label: str,
    *,
    settlement_evidence: dict[str, object] | None = None,
) -> None:
    if label not in {"APPROVED_EQUIVALENT", "REJECTED"}:
        raise ValueError("learning labels must be APPROVED_EQUIVALENT or REJECTED")
    if not settlement_evidence or settlement_evidence.get("settlement_verified") is not True:
        raise ValueError("trusted learning labels require settlement-verified evidence")
    await store.save_learning_example(
        f"{pair.pair_id}:{label}",
        label,
        {
            "pair_id": pair.pair_id,
            "market_a": pair.market_a.model_dump(mode="json"),
            "market_b": pair.market_b.model_dump(mode="json"),
            "decision": pair.decision.model_dump(mode="json") if pair.decision else None,
            "evidence": settlement_evidence,
        },
    )


async def export_training_jsonl(store: AtlasStore, path: str) -> int:
    examples = await _trusted_export_examples(store)
    _write_examples(examples, path)
    return len(examples)


async def export_learning_splits(
    store: AtlasStore,
    training_path: str,
    evaluation_path: str,
    evaluation_ratio: float = 0.2,
) -> dict[str, int]:
    if not 0 < evaluation_ratio < 1:
        raise ValueError("evaluation_ratio must be between 0 and 1")
    examples = await _trusted_export_examples(store)
    by_label: dict[str, list[dict[str, object]]] = {}
    for example in examples:
        by_label.setdefault(str(example["label"]), []).append(example)
    training: list[dict[str, object]] = []
    evaluation: list[dict[str, object]] = []
    for label_examples in by_label.values():
        ordered = sorted(
            label_examples,
            key=lambda item: sha256(str(item["example_id"]).encode()).hexdigest(),
        )
        evaluation_count = (
            max(1, round(len(ordered) * evaluation_ratio)) if len(ordered) > 1 else 0
        )
        evaluation.extend(ordered[:evaluation_count])
        training.extend(ordered[evaluation_count:])
    _write_examples(training, training_path)
    _write_examples(evaluation, evaluation_path)
    return {"training": len(training), "evaluation": len(evaluation)}


async def export_training_bundle(
    store: AtlasStore,
    directory: str = "data/training",
    *,
    evaluation_ratio: float = 0.2,
    backfill_report: dict[str, object] | None = None,
) -> dict[str, object]:
    """Export trusted splits plus a machine-readable provenance manifest.

    The export boundary is deliberately stricter than a label column check:
    only the two trusted labels with settlement-verified evidence are written.
    Review, inconclusive, unlabeled, and otherwise unverified rows remain out
    of both JSONL files and are represented only as aggregate exclusions.
    """
    if backfill_report is None:
        backfill_report = {}
    destination = Path(directory)
    training_path = destination / "atlas.jsonl"
    evaluation_path = destination / "atlas-eval.jsonl"
    split_counts = await export_learning_splits(
        store,
        str(training_path),
        str(evaluation_path),
        evaluation_ratio,
    )
    from atlas.evaluation import learning_loop_status

    all_counts = await store.learning_counts()
    trusted_counts = await store.trusted_learning_counts()
    trusted_labels = sum(trusted_counts.values())
    total_rows = sum(all_counts.values())
    excluded_rows_by_label = {
        label: count
        for label, count in sorted(all_counts.items())
        if label not in TRUSTED_LEARNING_LABELS
    }
    for label in sorted(TRUSTED_LEARNING_LABELS):
        unverified = max(0, all_counts.get(label, 0) - trusted_counts.get(label, 0))
        if unverified:
            excluded_rows_by_label[f"{label}:UNVERIFIED"] = unverified
    loop = await learning_loop_status(store)
    manifest = {
        "schema_version": 1,
        "artifact": "atlas-learning-export",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "kind": "HISTORICAL_BACKFILL",
            "status": backfill_report.get("status"),
            "started_at": backfill_report.get("started_at"),
            "completed_at": backfill_report.get("completed_at"),
            "venue_coverage": backfill_report.get("venue_coverage", {}),
        },
        "paper_only": True,
        "execution_enabled": False,
        "live_orders_enabled": False,
        "training_file": str(training_path),
        "evaluation_file": str(evaluation_path),
        "split_counts": split_counts,
        "counts": {
            "learning_rows_total": total_rows,
            "trusted_labels": trusted_labels,
            "approved_labels": trusted_counts.get("APPROVED_EQUIVALENT", 0),
            "rejected_labels": trusted_counts.get("REJECTED", 0),
            "training_examples": split_counts["training"],
            "evaluation_examples": split_counts["evaluation"],
            "excluded_rows": max(0, total_rows - trusted_labels),
            "untrusted_or_inconclusive_examples_exported": 0,
        },
        "label_mix": {
            "APPROVED_EQUIVALENT": trusted_counts.get("APPROVED_EQUIVALENT", 0),
            "REJECTED": trusted_counts.get("REJECTED", 0),
        },
        "excluded_rows_by_label": excluded_rows_by_label,
        "trust_policy": {
            "trusted_labels_only": True,
            "settlement_verified_required": True,
            "review_and_inconclusive_exported": False,
        },
        "learning_loop": loop,
        "backfill": {
            key: backfill_report[key]
            for key in (
                "status",
                "completed_at",
                "venue_coverage",
                "new_labels",
                "approved_labels",
                "rejected_labels",
            )
            if backfill_report and key in backfill_report
        },
    }
    manifest_path = destination / "manifest.json"
    temporary_path = destination / ".manifest.json.tmp"
    destination.mkdir(parents=True, exist_ok=True)
    temporary_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(manifest_path)
    return {
        "training": split_counts["training"],
        "evaluation": split_counts["evaluation"],
        "manifest": str(manifest_path),
        "label_mix": manifest["label_mix"],
        "trusted_labels": trusted_labels,
        "status": loop["status"],
        "paper_only": True,
        "execution_enabled": False,
    }


async def _trusted_export_examples(store: AtlasStore) -> list[dict[str, object]]:
    examples = await store.labeled_learning_examples()
    trusted: list[dict[str, object]] = []
    for example in examples:
        payload = example.get("payload")
        evidence = payload.get("evidence") if isinstance(payload, dict) else None
        if (
            example.get("label") in TRUSTED_LEARNING_LABELS
            and isinstance(payload, dict)
            and isinstance(evidence, dict)
            and evidence.get("settlement_verified") is True
        ):
            trusted.append(example)
    return trusted


def _write_examples(examples: list[dict[str, object]], path: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        for example in examples:
            payload = example["payload"]
            handle.write(
                json.dumps(
                    {
                        "messages": [
                            {
                                "role": "system",
                                "content": "Propose equivalent prediction-market contracts; never override deterministic rules.",
                            },
                            {
                                "role": "user",
                                "content": json.dumps(payload["market_a"])
                                + "\n"
                                + json.dumps(payload["market_b"]),
                            },
                            {"role": "assistant", "content": example["label"]},
                        ]
                    }
                )
                + "\n"
            )
