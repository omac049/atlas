"""The 90-day opportunity study: honest weekly metrics from the gap radar.

Charter: docs/NINETY_DAY_STUDY.md. This module only measures what the radar has
already recorded — it never fetches, trades, or re-verifies. Every number is
computed from persisted observations so a report can be regenerated bit-for-bit
from the database alone.

Definitions (shared with the charter; change either only via a documented
amendment):

- **Opportunity** — a (kalshi market, polymarket market, UTC day) with at least
  one executable observation: the same dedup rule as the paper bankroll, so a
  gap persisting across many 5-minute sweeps in one day counts once.
- **Survival run** — consecutive executable observations of one pair no more
  than ``RUN_GAP_TOLERANCE_SECONDS`` apart; its duration is last minus first.
  A single isolated observation has duration zero and is counted separately —
  it means the gap outlived at most one sweep.
- **Venue-text-only blocker** — an executable observation whose verification
  refusal consists solely of codes a venue text revision would clear. These are
  the review's "verified opportunity" precursors: nothing structural stands
  between them and approval except published wording.
- **Settlement-timing curve** — observations bucketed by days until the LATER
  leg settles (the basket's capital lock-up), split by whether the pair carries
  a settlement-timing asymmetry tag (``atlas.settlement_timing``). It answers
  whether an observed gap is carry compensation (gaps shrink as settlement
  nears, and widen with lock-up) or mispricing. The tag is DESCRIPTIVE: it
  gates nothing and never upgrades a pair's trust.
"""

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median

STUDY_START = date(2026, 8, 19)
STUDY_DAYS = 90
PHASE_2_START_DAY = 31  # latency-adjusted shadow execution must exist by here
PHASE_3_START_DAY = 61  # willingness-to-pay interviews (owner-led)

# Two consecutive monitor sweeps are ~300s apart; three missed sweeps ends a run.
RUN_GAP_TOLERANCE_SECONDS = 900

# Go/no-go thresholds adopted from the 2026-08-19 external viability review.
GO_MIN_OPPORTUNITIES_PER_MONTH = 10

# Days-to-settlement buckets for the gap-vs-horizon curve; the last bucket is
# open-ended. Boundaries are inclusive upper bounds.
SETTLEMENT_HORIZON_BUCKETS: tuple[tuple[str, int | None], ...] = (
    ("0-7", 7),
    ("8-30", 30),
    ("31-90", 90),
    ("90+", None),
)

# Verification codes a venue text revision alone would clear.
VENUE_TEXT_ONLY_CODES = {
    "SETTLEMENT_GUARANTEE_UNKNOWN",
    "SETTLEMENT_POLICY_MISMATCH",
    "REVISION_POLICY_MISMATCH",
}


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_at(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _pair_key(observation: dict) -> tuple[str, str]:
    return (
        str(observation.get("kalshi_market_id")),
        str(observation.get("polymarket_market_id")),
    )


def _week_label(day: date) -> str:
    monday = day - timedelta(days=day.weekday())
    return monday.isoformat()


def _best_basket(observation: dict) -> dict:
    wanted = observation.get("best_basket")
    for basket in observation.get("baskets") or []:
        if basket.get("legs") == wanted:
            return basket
    return {}


def study_report(observations: list[dict], today: date | None = None) -> dict:
    """Deterministic study metrics over persisted radar observations."""
    today = today or datetime.now(UTC).date()
    executable: list[dict] = []
    weeks: dict[str, dict] = {}
    fee_models = Counter()
    opportunities: set[tuple[str, str, str]] = set()
    text_only: set[tuple[str, str, str]] = set()
    first_observed: date | None = None

    for observation in sorted(observations, key=lambda o: str(o.get("observed_at") or "")):
        at = _parse_at(observation.get("observed_at"))
        if at is None:
            continue
        if first_observed is None:
            first_observed = at.date()
        week = weeks.setdefault(
            _week_label(at.date()),
            {
                "observations": 0,
                "executable_observations": 0,
                "opportunities": 0,
                "gaps": [],
                "families": Counter(),
                "venue_text_only_opportunities": set(),
            },
        )
        week["observations"] += 1
        basket = _best_basket(observation)
        fee_models[
            "venue_published" if "kalshi_fee" in basket else "legacy_flat_buffer"
        ] += 1
        if not observation.get("executable_gap"):
            continue
        gap = _decimal(observation.get("best_gap"))
        if gap is None or gap <= 0:
            continue
        executable.append(observation)
        week["executable_observations"] += 1
        week["gaps"].append(gap)
        family = str(observation.get("event_subject") or "").split("|")[0]
        week["families"][family] += 1
        opportunity = (*_pair_key(observation), at.date().isoformat())
        if opportunity not in opportunities:
            opportunities.add(opportunity)
            week["opportunities"] += 1
            codes = set(observation.get("mismatch_codes") or [])
            if codes and codes <= VENUE_TEXT_ONLY_CODES:
                week["venue_text_only_opportunities"].add(opportunity)
                text_only.add(opportunity)

    survivals, singletons = _survival_runs(executable)
    sizes = _executable_sizes(executable)

    weekly = [
        {
            "week_of": label,
            "observations": data["observations"],
            "executable_observations": data["executable_observations"],
            "opportunities": data["opportunities"],
            "venue_text_only_opportunities": len(data["venue_text_only_opportunities"]),
            "median_gap": str(median(data["gaps"])) if data["gaps"] else None,
            "max_gap": str(max(data["gaps"])) if data["gaps"] else None,
            "families": dict(data["families"].most_common()),
        }
        for label, data in sorted(weeks.items())
    ]

    day_number = (today - STUDY_START).days + 1
    # Rate basis: the actual observed window, not the study day — retroactive
    # pre-charter observations are included, and dividing a week of data by
    # "day 1" would flatter the rate ~7x. The go test uses VENUE-TEXT-ONLY
    # opportunities (the charter's "verified" precursor), not raw candidates.
    span_start = min(first_observed or STUDY_START, STUDY_START)
    elapsed_days = max(1, (today - span_start).days + 1)
    candidate_rate = len(opportunities) * Decimal(30) / Decimal(elapsed_days)
    verified_rate = len(text_only) * Decimal(30) / Decimal(elapsed_days)
    return {
        "paper_only": True,
        "study_start": STUDY_START.isoformat(),
        "study_day": day_number,
        "phase": (
            1 if day_number < PHASE_2_START_DAY else 2 if day_number < PHASE_3_START_DAY else 3
        ),
        "observations_reviewed": len(observations),
        "rate_window_days": elapsed_days,
        "distinct_opportunities": len(opportunities),
        "venue_text_only_opportunities_total": len(text_only),
        "candidate_opportunities_per_30_days": str(candidate_rate.quantize(Decimal("0.1"))),
        "verified_opportunities_per_30_days": str(verified_rate.quantize(Decimal("0.1"))),
        "go_threshold_per_30_days": GO_MIN_OPPORTUNITIES_PER_MONTH,
        "meets_go_threshold": verified_rate >= GO_MIN_OPPORTUNITIES_PER_MONTH,
        "survival": {
            "runs": len(survivals),
            "single_sweep_only": singletons,
            "median_minutes": str(median(survivals)) if survivals else None,
            "max_minutes": str(max(survivals)) if survivals else None,
        },
        "executable_size_contracts": sizes,
        "fee_model_rows": dict(fee_models),
        "settlement_timing_curve": _settlement_timing_curve(observations),
        "weekly": weekly,
    }


def _horizon_bucket(days: Decimal) -> str:
    for label, upper in SETTLEMENT_HORIZON_BUCKETS:
        if upper is None or days <= upper:
            return label
    return SETTLEMENT_HORIZON_BUCKETS[-1][0]


def _median_gap(gaps: list[Decimal]) -> str | None:
    return str(median(gaps)) if gaps else None


def _settlement_timing_curve(observations: list[dict]) -> dict:
    """Gap against time-to-settlement, split by settlement-timing asymmetry.

    Every observation carrying a parseable ``best_gap`` counts — negative gaps
    included, because the question is how the cross-venue price relationship
    behaves as the lock-up shortens, not how often it clears fees. The
    asymmetry split is descriptive: an asymmetric pair is one where a venue
    published an early-determination clause its twin did not, which is a
    caution tag, never an approval input.
    """
    buckets: dict[str, dict] = {
        label: {"gaps": [], "executable": 0, "asymmetric": [], "symmetric": []}
        for label, _ in SETTLEMENT_HORIZON_BUCKETS
    }
    without_horizon = 0
    after_horizon = 0
    asymmetric_gaps: list[Decimal] = []
    symmetric_gaps: list[Decimal] = []
    asymmetric_pairs: set[tuple[str, str]] = set()

    for observation in observations:
        timing = observation.get("settlement_timing") or {}
        asymmetric = bool(timing.get("asymmetric"))
        if asymmetric:
            asymmetric_pairs.add(_pair_key(observation))
        gap = _decimal(observation.get("best_gap"))
        if gap is None:
            continue
        days = _decimal(timing.get("days_to_settlement"))
        if days is None:
            without_horizon += 1
            continue
        if days < 0:
            after_horizon += 1
            continue
        bucket = buckets[_horizon_bucket(days)]
        bucket["gaps"].append(gap)
        if observation.get("executable_gap") and gap > 0:
            bucket["executable"] += 1
        if asymmetric:
            bucket["asymmetric"].append(gap)
            asymmetric_gaps.append(gap)
        else:
            bucket["symmetric"].append(gap)
            symmetric_gaps.append(gap)

    return {
        "observations_with_horizon": sum(len(data["gaps"]) for data in buckets.values()),
        "observations_without_horizon": without_horizon,
        "observations_after_horizon": after_horizon,
        "asymmetric_pairs": len(asymmetric_pairs),
        "asymmetric_observations": len(asymmetric_gaps),
        "symmetric_observations": len(symmetric_gaps),
        "asymmetric_median_gap": _median_gap(asymmetric_gaps),
        "symmetric_median_gap": _median_gap(symmetric_gaps),
        "buckets": [
            {
                "bucket": label,
                "observations": len(buckets[label]["gaps"]),
                "executable_observations": buckets[label]["executable"],
                "median_gap": _median_gap(buckets[label]["gaps"]),
                "asymmetric_observations": len(buckets[label]["asymmetric"]),
                "asymmetric_median_gap": _median_gap(buckets[label]["asymmetric"]),
                "symmetric_observations": len(buckets[label]["symmetric"]),
                "symmetric_median_gap": _median_gap(buckets[label]["symmetric"]),
            }
            for label, _ in SETTLEMENT_HORIZON_BUCKETS
        ],
    }


def _survival_runs(executable: list[dict]) -> tuple[list[Decimal], int]:
    """Durations (minutes) of consecutive executable runs, plus singleton count."""
    by_pair: dict[tuple[str, str], list[datetime]] = {}
    for observation in executable:
        at = _parse_at(observation.get("observed_at"))
        if at is not None:
            by_pair.setdefault(_pair_key(observation), []).append(at)
    durations: list[Decimal] = []
    singletons = 0
    for stamps in by_pair.values():
        stamps.sort()
        run_start = stamps[0]
        previous = stamps[0]
        length = 1
        for at in stamps[1:] + [None]:
            if at is not None and (at - previous).total_seconds() <= RUN_GAP_TOLERANCE_SECONDS:
                previous = at
                length += 1
                continue
            if length == 1:
                singletons += 1
            else:
                minutes = Decimal((previous - run_start).total_seconds()) / 60
                durations.append(minutes.quantize(Decimal("0.1")))
            if at is not None:
                run_start = previous = at
                length = 1
    return durations, singletons


def _executable_sizes(executable: list[dict]) -> dict:
    sizes = [
        size
        for observation in executable
        if (size := _decimal(_best_basket(observation).get("kalshi_size"))) is not None
    ]
    if not sizes:
        return {"observations_with_size": 0}
    return {
        "observations_with_size": len(sizes),
        "median": str(median(sizes)),
        "max": str(max(sizes)),
    }
