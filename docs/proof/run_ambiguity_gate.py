"""Charter §4 feasibility gate for the Predicate Ambiguity Score.

Scores the Test A DEVELOPMENT set — the 15 gradeable disputes and their 45
rule-selected controls — with `atlas.ambiguity`. Output:
ambiguity-gate-result.json beside this file.

THIS IS NOT EVIDENCE. The charter says so in §2 and §6, and it is repeated
here because a number this file prints could otherwise be quoted as a result.
The author of the instrument read these disputes before writing it, so
separation here measures FIT. The gate exists only to decide whether
assembling a holdout is worth the effort.

Reuses run_test_a.py's resolution and control-selection path verbatim so the
two runs are comparable market-for-market.

Run:  .venv/bin/python docs/proof/run_ambiguity_gate.py
"""

import asyncio
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_test_a import (
    MIN_CANDIDATES,
    POLYMARKET_SLUGS,
    WIDE_WINDOW_DAYS,
    WINDOW_DAYS,
    gamma_candidates,
    gamma_event_primary_tag,
    gamma_market,
)

from atlas.ambiguity import AMBIGUITY_FLAG_THRESHOLD, ambiguity_score
from atlas.proof import select_controls
from atlas.venues.polymarket_global import PolymarketGlobalHistoricalVenue

HERE = Path(__file__).resolve().parent

# Charter §4, fixed before this file was run.
GATE_MIN_MEDIAN_GAP = 15
GATE_MIN_FLAG_RATE = Decimal("0.60")
GATE_MIN_FLAG_RATIO = Decimal(2)


def _arm(markets: list, scored_at: datetime) -> dict:
    scores = [ambiguity_score(market, scored_at=scored_at) for market in markets]
    measurable = [score for score in scores if score["score"] is not None]
    flagged = [score for score in measurable if score["flagged"]]
    return {
        "markets": len(scores),
        "measurable": len(measurable),
        "unmeasurable": len(scores) - len(measurable),
        "median_score": median([s["score"] for s in measurable]) if measurable else None,
        "flag_rate": (
            str((Decimal(len(flagged)) / Decimal(len(measurable))).quantize(Decimal("0.001")))
            if measurable
            else None
        ),
        "scores": [
            {
                "market_id": score["market_id"],
                "score": score["score"],
                "flagged": score["flagged"],
                "features": [feature["code"] for feature in score["features"]],
            }
            for score in scores
        ],
    }


async def main() -> None:
    normalize = PolymarketGlobalHistoricalVenue._normalize_market
    disputed, controls = [], []
    disputed_ids: set[str] = set()

    async with httpx.AsyncClient(timeout=40) as client:
        raw_by_id = {}
        for dispute_id, slug in POLYMARKET_SLUGS.items():
            raw = await gamma_market(client, slug)
            if raw is None:
                continue
            raw_by_id[dispute_id] = raw
            market = normalize(raw)
            disputed.append(market)
            disputed_ids.add(market.market_id)

        for raw in raw_by_id.values():
            events = raw.get("events") or []
            event_slug = events[0].get("slug") if events else None
            tag = await gamma_event_primary_tag(client, event_slug) if event_slug else None
            if tag is None:
                continue
            market = normalize(raw)
            anchor = market.close_time or datetime.now(UTC)
            candidates_raw = await gamma_candidates(client, str(tag["id"]), anchor, WINDOW_DAYS)
            if len(candidates_raw) < MIN_CANDIDATES:
                candidates_raw = await gamma_candidates(
                    client, str(tag["id"]), anchor, WIDE_WINDOW_DAYS
                )
            controls.extend(
                select_controls(
                    market, [normalize(item) for item in candidates_raw], excluded_ids=disputed_ids
                )
            )

    scored_at = datetime.now(UTC)
    disputed_arm = _arm(disputed, scored_at)
    control_arm = _arm(controls, scored_at)

    gap = None
    if disputed_arm["median_score"] is not None and control_arm["median_score"] is not None:
        gap = disputed_arm["median_score"] - control_arm["median_score"]
    disputed_rate = Decimal(disputed_arm["flag_rate"]) if disputed_arm["flag_rate"] else None
    control_rate = Decimal(control_arm["flag_rate"]) if control_arm["flag_rate"] else None

    gap_ok = gap is not None and gap >= GATE_MIN_MEDIAN_GAP
    rate_ok = (
        disputed_rate is not None
        and control_rate is not None
        and disputed_rate >= GATE_MIN_FLAG_RATE
        and (control_rate == 0 or disputed_rate >= control_rate * GATE_MIN_FLAG_RATIO)
    )

    report = {
        "gate": "charter-4-feasibility",
        "charter": "docs/decisions/2026-09-02-predicate-ambiguity-charter.md",
        "NOT_EVIDENCE": (
            "Development-set separation measures FIT: the instrument's author read "
            "these disputes before writing it. Only the post-freeze holdout can "
            "support a claim. See charter sections 2 and 6."
        ),
        "scored_at": scored_at.isoformat(),
        "flag_threshold": AMBIGUITY_FLAG_THRESHOLD,
        "thresholds": {
            "min_median_gap": GATE_MIN_MEDIAN_GAP,
            "min_flag_rate": str(GATE_MIN_FLAG_RATE),
            "min_flag_ratio": str(GATE_MIN_FLAG_RATIO),
        },
        "disputed": disputed_arm,
        "controls": control_arm,
        "median_gap": gap,
        "flag_rate_ratio": (
            None
            if disputed_rate is None or control_rate is None
            else "inf"
            if control_rate == 0
            else str((disputed_rate / control_rate).quantize(Decimal("0.01")))
        ),
        "criteria": {"median_gap_ge_15": gap_ok, "flag_rate_60pct_and_2x": rate_ok},
        "outcome": "PROCEED_TO_HOLDOUT" if gap_ok and rate_ok else "ABANDON_BEFORE_HOLDOUT",
    }
    (HERE / "ambiguity-gate-result.json").write_text(json.dumps(report, indent=2) + "\n")
    print("NOT EVIDENCE — development-set fit only (charter sections 2 and 6)")
    print(f"outcome={report['outcome']}")
    print(
        f"disputed median={disputed_arm['median_score']} (n={disputed_arm['measurable']}) "
        f"control median={control_arm['median_score']} (n={control_arm['measurable']}) gap={gap}"
    )
    print(
        f"flag rates: disputed={disputed_arm['flag_rate']} control={control_arm['flag_rate']} "
        f"ratio={report['flag_rate_ratio']}"
    )
    print(f"criteria: {report['criteria']}")


if __name__ == "__main__":
    asyncio.run(main())
