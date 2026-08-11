from atlas.storage import AtlasStore


async def learning_loop_status(
    store: AtlasStore, minimum_labels: int = 50
) -> dict[str, object]:
    """Return a stable, automation-facing status for the trusted learning set.

    This deliberately reports eligibility, not model quality. Only settlement-
    verified labels are counted, and the paper-only safeguards are explicit so
    an automation cannot mistake observations or review items for training data
    or an execution authorization.
    """
    if minimum_labels < 1:
        raise ValueError("minimum_labels must be at least 1")

    counts = await store.trusted_learning_counts()
    all_counts = await store.learning_counts()
    validation = await store.validation_summary()
    approved = counts.get("APPROVED_EQUIVALENT", 0)
    rejected = counts.get("REJECTED", 0)
    labels = approved + rejected
    blockers: list[str] = []
    if not approved or not rejected:
        blockers.append("both approved and rejected labels are required")
    if labels < minimum_labels:
        blockers.append(f"need {minimum_labels - labels} more trusted labels")
    if blockers:
        status = "LABEL_MIX_BLOCKED" if not approved or not rejected else "MINIMUM_LABELS_BLOCKED"
    else:
        status = "READY"
    return {
        "status": status,
        "ready": not blockers,
        "minimum_labels": minimum_labels,
        "trusted_labels": labels,
        "label_counts": {
            "APPROVED_EQUIVALENT": approved,
            "REJECTED": rejected,
        },
        "unlabeled_observations": all_counts.get("UNLABELED", 0),
        "validation": validation,
        "blockers": blockers,
        "paper_only": True,
        "execution_enabled": False,
    }


async def learning_readiness(store: AtlasStore, minimum_labels: int = 50) -> dict[str, object]:
    loop_status = await learning_loop_status(store, minimum_labels)
    label_counts = loop_status["label_counts"]
    assert isinstance(label_counts, dict)
    reasons = list(loop_status["blockers"])
    return {
        "ready": loop_status["ready"],
        "status": loop_status["status"],
        "labels": loop_status["trusted_labels"],
        "approved": label_counts["APPROVED_EQUIVALENT"],
        "rejected": label_counts["REJECTED"],
        "observations": loop_status["unlabeled_observations"],
        "validation": loop_status["validation"],
        "reasons": reasons,
        "loop": loop_status,
    }
