"""Test A of the fine-print proof charter: disputes vs. matched controls.

Implements the charter's pre-registered mechanics
(``docs/decisions/2026-08-31-fine-print-proof-charter.md``) and nothing else:
deterministic control selection, blind grading of both arms, and the two pass
criteria exactly as signed. The thresholds are copied here as constants so a
drift between code and charter is a diff, not a judgment call — change them
only by amending the charter first.

This module is study tooling. It is a read-only consumer of
``atlas.clarity`` and must never be imported by the verification, settlement,
or normalization paths; a proof harness that could influence the instrument it
tests would be worthless.

Blind protocol: the corpus file records WHICH markets were disputed and why
(with citations) before any grade is computed. This module takes that file as
given and never reorders, drops, or reweights a dispute based on its grade.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from statistics import median

from atlas.clarity import clarity_score
from atlas.models import Market

# The charter's pre-named trouble class. Membership is fixed; a new clarity
# finding code does NOT join this set without a charter amendment.
TROUBLE_FINDINGS = frozenset(
    {
        "DISCRETIONARY_FAIR_PRICE_SETTLEMENT",
        "NO_EXPLICIT_EXCEPTION_FALLBACK",
        "MISSING_AUTHORITATIVE_SOURCE",
        "CONFLICTING_AUTHORITATIVE_SOURCE",
        "UNPARSED_SETTLEMENT_POLICY",
    }
)

# Pass criteria, verbatim from the charter: BOTH must hold.
MIN_MEDIAN_SCORE_GAP = 10
MIN_TROUBLE_RATE_RATIO = Decimal(2)
MIN_TROUBLE_RATE_DISPUTED = Decimal("0.60")

MIN_CORPUS_SIZE = 15
CONTROLS_PER_DISPUTE = 3


def select_controls(
    disputed: Market, candidates: list[Market], excluded_ids: set[str]
) -> list[Market]:
    """The charter's control rule: nearest close-times, never hand-picked.

    ``candidates`` must already be same-venue, same-category, settled markets —
    assembling that pool is the caller's job because "category" is a per-venue
    field. This function only applies the deterministic tiebreak: sort by
    absolute close-time distance to the disputed market, then by market_id so
    equal distances cannot be reordered by fetch order, and take the first
    three that are not themselves disputed.
    """
    anchor = disputed.close_time
    eligible = [
        market
        for market in candidates
        if market.market_id not in excluded_ids
        and market.market_id != disputed.market_id
        and market.close_time is not None
    ]

    def distance(market: Market) -> tuple[float, str]:
        assert anchor is not None  # corpus entries without close times are rejected upstream
        return (abs((market.close_time - anchor).total_seconds()), market.market_id)

    if anchor is None:
        # No anchor, no honest "nearest": deterministic fallback is market_id
        # order, and the report records the dispute as anchorless.
        return sorted(eligible, key=lambda market: market.market_id)[:CONTROLS_PER_DISPUTE]
    return sorted(eligible, key=distance)[:CONTROLS_PER_DISPUTE]


def _arm_stats(markets: list[Market], graded_at: datetime) -> dict:
    grades = [clarity_score(market, graded_at=graded_at) for market in markets]
    scores = [grade["score"] for grade in grades]
    troubled = [
        grade
        for grade in grades
        if TROUBLE_FINDINGS & {finding["code"] for finding in grade["findings"]}
    ]
    return {
        "markets": len(grades),
        "median_score": median(scores) if scores else None,
        "trouble_rate": (
            str((Decimal(len(troubled)) / Decimal(len(grades))).quantize(Decimal("0.001")))
            if grades
            else None
        ),
        "grades": [
            {
                "market_id": grade["market_id"],
                "grade": grade["grade"],
                "score": grade["score"],
                "trouble_findings": sorted(
                    TROUBLE_FINDINGS & {finding["code"] for finding in grade["findings"]}
                ),
            }
            for grade in grades
        ],
    }


def evaluate_test_a(
    disputed: list[Market],
    controls: list[Market],
    graded_at: datetime,
    corpus_size_documented: int,
) -> dict:
    """Grade both arms and apply the charter's two criteria. Pure; no fetching.

    ``corpus_size_documented`` is the number of disputes the corpus FILE
    documents with citations — it may exceed ``len(disputed)`` when some
    disputed markets' rules text could no longer be fetched. Both numbers are
    reported: an unfetchable dispute is a coverage limit, never a silent drop.
    """
    disputed_stats = _arm_stats(disputed, graded_at)
    control_stats = _arm_stats(controls, graded_at)

    gap = None
    if disputed_stats["median_score"] is not None and control_stats["median_score"] is not None:
        gap = control_stats["median_score"] - disputed_stats["median_score"]

    disputed_rate = (
        Decimal(disputed_stats["trouble_rate"]) if disputed_stats["trouble_rate"] else None
    )
    control_rate = (
        Decimal(control_stats["trouble_rate"]) if control_stats["trouble_rate"] else None
    )
    rate_ratio = None
    if disputed_rate is not None and control_rate is not None:
        rate_ratio = (
            None if control_rate == 0 and disputed_rate == 0
            else "inf" if control_rate == 0
            else str((disputed_rate / control_rate).quantize(Decimal("0.01")))
        )

    criterion_gap = gap is not None and gap >= MIN_MEDIAN_SCORE_GAP
    criterion_rate = (
        disputed_rate is not None
        and disputed_rate >= MIN_TROUBLE_RATE_DISPUTED
        and (
            control_rate == 0
            if control_rate is not None and disputed_rate > 0 and control_rate == 0
            else control_rate is not None and disputed_rate >= control_rate * MIN_TROUBLE_RATE_RATIO
        )
    )
    adequate = len(disputed) >= MIN_CORPUS_SIZE

    return {
        "test": "A",
        "charter": "docs/decisions/2026-08-31-fine-print-proof-charter.md",
        "graded_at": graded_at.isoformat(),
        "corpus_size_documented": corpus_size_documented,
        "corpus_size_gradeable": len(disputed),
        "adequate_corpus": adequate,
        "disputed": disputed_stats,
        "controls": control_stats,
        "median_score_gap": gap,
        "trouble_rate_ratio": rate_ratio,
        "criteria": {
            "median_gap_ge_10": criterion_gap,
            "trouble_rate_60pct_and_2x": criterion_rate,
        },
        # The charter's exact decision language: pass needs both criteria on an
        # adequate corpus; failing with an adequate corpus is a FAIL; a thin
        # corpus is INCONCLUSIVE no matter how the numbers lean.
        "outcome": (
            "PASS" if adequate and criterion_gap and criterion_rate
            else "FAIL" if adequate
            else "INCONCLUSIVE_CORPUS_TOO_SMALL"
        ),
    }
