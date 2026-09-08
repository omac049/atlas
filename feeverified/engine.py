"""Build-time access to the JavaScript fee engine via Node, so that pages can
show precomputed tables ("on a $100 sale you keep $X") from the same code the
browser runs. One implementation, two callers."""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"
FEES_DIR = Path(__file__).resolve().parent.parent / "docs" / "fees"

# Standard sale sizes shown on every platform page and used for comparisons.
STANDARD_SALES = (10, 25, 50, 100, 250, 500, 1000)


def _run(schedule_path: Path, inputs: dict) -> dict:
    out = subprocess.run(
        ["node", str(STATIC_DIR / "run.js"), str(schedule_path), json.dumps(inputs)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(out.stdout)


@lru_cache(maxsize=4096)
def _cached(platform: str, inputs_json: str) -> str:
    return json.dumps(_run(FEES_DIR / f"{platform}.json", json.loads(inputs_json)))


def compute(platform: str, inputs: dict) -> dict:
    return json.loads(_cached(platform, json.dumps(inputs, sort_keys=True)))


def default_inputs(schedule: dict, sale: float) -> dict:
    """The schedule's declared defaults with the sale amount substituted into
    its primary money field (the first money input)."""
    inputs = {}
    primary = None
    for spec in schedule.get("inputs", []):
        inputs[spec["id"]] = spec.get("default")
        if primary is None and spec.get("type") == "money":
            primary = spec["id"]
    if primary:
        inputs[primary] = sale
    return inputs


def standard_table(schedule: dict, sales=STANDARD_SALES) -> list[dict]:
    rows = []
    for sale in sales:
        result = compute(schedule["platform"], default_inputs(schedule, sale))
        rows.append({"sale": sale, "fees": result["total_fees"], "net": result["net"], "rate": result["effective_rate"]})
    return rows
