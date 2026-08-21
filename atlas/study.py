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
  gates nothing and never upgrades a pair's trust. Rows recorded before the
  annotation shipped are counted as ``unannotated_observations``, never pooled
  with annotated rows whose venues published no anchor; and when no watched
  pair carries the tag the curve says so in ``asymmetry_blind_spot`` rather
  than letting a null median pass for a measured result.
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

# Frequency alone cannot fail. It read 66.7 against a threshold of 10 on study
# DAY 2, because it counts pair-days over a handful of recurring pairs. The
# charter's own decision rule needs three more things to be true, and these
# encode two of them so the headline can move in both directions.
#
# 1) The edge has to beat leaving the money alone. Every executable observation
#    recorded before 2026-08-20 locked capital for 160-250 days, and the
#    dominant family annualizes to ~3.4% — below T-bills. Charging the horizon
#    is the charter's phase-2 deliverable ("return on locked capital until
#    settlement, not profit per basket").
# 2) Size has to matter — the charter's words are "not pennies on 3-contract
#    books". The median executable basket to date is ~$474 of capital for
#    ~$4.74 of gross edge.
#
# PROVISIONAL: both numbers are placeholders pending owner sign-off. 15%
# annualized is roughly the risk-free rate plus a premium for settlement
# divergence, legging, and venue risk on an unverified twin; $500 is the
# smallest basket worth an operator's attention. Neither is owner-signed yet —
# record the decision before day 90 reads either as authoritative.
GO_MIN_ANNUALIZED_RETURN_ON_LOCKED_CAPITAL = Decimal("0.15")
GO_MIN_MEDIAN_BASKET_NOTIONAL_USD = Decimal(500)

# Families brought into gap-radar scope AFTER the study began, mapped to the
# date they were added. Their opportunities are measured and reported in full,
# but held OUT of the go/no-go rate.
#
# Why: the go/no-go asks whether opportunities occur often enough. If the
# instrument widens mid-study, the rate rises because we changed what we look
# at, not because the market changed — and week 1-2 stops being comparable to
# week 3+. Quarantining keeps the headline rate on the scope frozen at
# STUDY_START while still surfacing the new family's numbers, so the day-90
# reader can see both and combine them deliberately if they choose.
#
# us_house_control / us_senate_control (2026-08-20): the only pairs on either
# venue that carry a settlement-timing asymmetry, and therefore the only reason
# the asymmetric-vs-symmetric split has an eligible population. See the charter
# amendment in docs/NINETY_DAY_STUDY.md.
POST_START_SCOPE_FAMILIES: dict[str, str] = {
    "us_house_control": "2026-08-20",
    "us_senate_control": "2026-08-20",
}

# Days-to-settlement buckets for the gap-vs-horizon curve; the last bucket is
# open-ended. Boundaries are inclusive upper bounds.
SETTLEMENT_HORIZON_BUCKETS: tuple[tuple[str, int | None], ...] = (
    ("0-7", 7),
    ("8-30", 30),
    ("31-90", 90),
    ("90+", None),
)

# Why the asymmetric-vs-symmetric gap comparison has no eligible population.
# Reported instead of leaving a null median to be misread as a measured result.
NO_ANNOTATED_OBSERVATIONS = "NO_ANNOTATED_OBSERVATIONS"
NO_WATCHED_PAIR_PUBLISHES_EARLY_DETERMINATION = "NO_WATCHED_PAIR_PUBLISHES_EARLY_DETERMINATION"

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
    frozen_observations = 0
    post_start_observations = 0
    post_start_executable: list[dict] = []
    post_start_families: Counter = Counter()
    post_start_opportunities: set[tuple[str, str, str]] = set()

    for observation in sorted(observations, key=lambda o: str(o.get("observed_at") or "")):
        at = _parse_at(observation.get("observed_at"))
        if at is None:
            continue
        if first_observed is None:
            first_observed = at.date()
        basket = _best_basket(observation)
        # Fee provenance is a property of the row, not of the scope: count it
        # for every observation so the two fee populations stay reconcilable.
        fee_models[
            "venue_published" if "kalshi_fee" in basket else "legacy_flat_buffer"
        ] += 1
        family = str(observation.get("event_subject") or "").split("|")[0]
        gap = _decimal(observation.get("best_gap")) if observation.get("executable_gap") else None
        opportunity = (*_pair_key(observation), at.date().isoformat())

        # Quarantine, not exclusion. A family added to radar scope mid-study is
        # measured in full on its own ledger and kept out of BOTH the go/no-go
        # rate and the weekly table — the weekly table is the artifact the study
        # compares across weeks, so widening what feeds it mid-flight is exactly
        # what would destroy the comparison.
        if family in POST_START_SCOPE_FAMILIES:
            post_start_observations += 1
            if gap is not None and gap > 0:
                post_start_executable.append(observation)
                post_start_families[family] += 1
                post_start_opportunities.add(opportunity)
            continue

        frozen_observations += 1
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
        if gap is None or gap <= 0:
            continue
        executable.append(observation)
        week["executable_observations"] += 1
        week["gaps"].append(gap)
        week["families"][family] += 1
        if opportunity not in opportunities:
            opportunities.add(opportunity)
            week["opportunities"] += 1
            codes = set(observation.get("mismatch_codes") or [])
            if codes and codes <= VENUE_TEXT_ONLY_CODES:
                week["venue_text_only_opportunities"].add(opportunity)
                text_only.add(opportunity)

    survivals, singletons = _survival_runs(executable)
    sizes = _executable_sizes(executable)
    locked_capital = _return_on_locked_capital(executable)
    notionals = _basket_notionals(executable)
    median_notional = median(notionals) if notionals else None
    # The tradeable subset. Every observation before 2026-08-20 priced a
    # Polymarket GLOBAL leg — offshore, no published book, and declared
    # non-executable by its own adapter. Those rows stay in the corpus as
    # research; only these could have been taken with real money.
    tradeable = [
        observation for observation in executable if observation.get("tradeable_venue_pair")
    ]
    tradeable_opportunities = {
        (*_pair_key(observation), str(observation.get("observed_at") or "")[:10])
        for observation in tradeable
    }

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
        # Frequency alone. Kept under its original name so week-over-week
        # comparisons stay readable, but it is no longer the decision.
        "meets_frequency_threshold": verified_rate >= GO_MIN_OPPORTUNITIES_PER_MONTH,
        "meets_go_threshold": _go_decision(
            verified_rate, locked_capital, median_notional, tradeable_opportunities
        ),
        "distinct_pairs": len({_pair_key(observation) for observation in executable}),
        "return_on_locked_capital": locked_capital,
        "median_basket_notional_usd": (
            str(median_notional.quantize(Decimal("0.01"))) if median_notional is not None else None
        ),
        "tradeable": {
            "venue": "polymarket_us",
            "executable_observations": len(tradeable),
            "distinct_opportunities": len(tradeable_opportunities),
            "distinct_pairs": len({_pair_key(observation) for observation in tradeable}),
            "return_on_locked_capital": _return_on_locked_capital(tradeable),
            "rationale": (
                "Polymarket Global legs are offshore, publish no order book, and "
                "the adapter states they can never reach an executable path. Only "
                "the US venue's rows speak to whether a basket could be taken."
            ),
        },
        "survival": {
            "runs": len(survivals),
            "single_sweep_only": singletons,
            "median_minutes": str(median(survivals)) if survivals else None,
            "max_minutes": str(max(survivals)) if survivals else None,
        },
        "executable_size_contracts": sizes,
        "fee_model_rows": dict(fee_models),
        "frozen_scope_observations": frozen_observations,
        # Measured in full, deliberately outside every number above.
        "post_start_scope": {
            "families": dict(POST_START_SCOPE_FAMILIES),
            "observations": post_start_observations,
            "executable_observations": len(post_start_executable),
            "distinct_opportunities": len(post_start_opportunities),
            "opportunities_per_30_days": str(
                (len(post_start_opportunities) * Decimal(30) / Decimal(elapsed_days)).quantize(
                    Decimal("0.1")
                )
            ),
            "executable_size_contracts": _executable_sizes(post_start_executable),
            "family_executable_observations": dict(post_start_families.most_common()),
            "excluded_from_go_threshold": True,
            "rationale": (
                "Added to radar scope after STUDY_START; folding it into the rate would "
                "raise the headline because the instrument widened, not because the market "
                "changed. See the charter amendment."
            ),
        },
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


def _asymmetry_blind_spot(
    annotated_pairs: set[tuple[str, str]], asymmetric_pairs: set[tuple[str, str]]
) -> str | None:
    """Why the asymmetric-vs-symmetric comparison could not be made, or None.

    An empty asymmetric bucket has two very different causes, and the day-90
    reader must not confuse them with a third, "asymmetry was measured and made
    no difference". Naming the cause keeps a null median from being read as a
    finding.
    """
    if not annotated_pairs:
        return NO_ANNOTATED_OBSERVATIONS
    if not asymmetric_pairs:
        return NO_WATCHED_PAIR_PUBLISHES_EARLY_DETERMINATION
    return None


def _settlement_timing_curve(observations: list[dict]) -> dict:
    """Gap against time-to-settlement, split by settlement-timing asymmetry.

    Every observation carrying a parseable ``best_gap`` counts — negative gaps
    included, because the question is how the cross-venue price relationship
    behaves as the lock-up shortens, not how often it clears fees. The
    asymmetry split is descriptive: an asymmetric pair is one where a venue
    published an early-determination clause its twin did not, which is a
    caution tag, never an approval input.

    Two populations are reported separately rather than pooled, for the same
    reason ``fee_model_rows`` separates its two fee models: a row recorded
    before the annotation shipped is not evidence that the venues published no
    anchor. ``unannotated_observations`` counts rows with no
    ``settlement_timing`` key at all; ``observations_without_horizon`` counts
    annotated rows whose venues published no usable anchor. Pooling them would
    permanently understate coverage, because the pre-annotation block never
    shrinks while the study runs.

    The asymmetry split also reports its own blind spot. A null
    ``asymmetric_median_gap`` beside a populated symmetric one must not read as
    "we measured asymmetry and it did not matter" — if no watched pair carries
    the tag, the comparison had no eligible population and was never made.
    """
    buckets: dict[str, dict] = {
        label: {"gaps": [], "executable": 0, "asymmetric": [], "symmetric": []}
        for label, _ in SETTLEMENT_HORIZON_BUCKETS
    }
    unannotated = 0
    without_horizon = 0
    after_horizon = 0
    asymmetric_gaps: list[Decimal] = []
    symmetric_gaps: list[Decimal] = []
    asymmetric_pairs: set[tuple[str, str]] = set()
    annotated_pairs: set[tuple[str, str]] = set()
    annotated_observations = 0

    for observation in observations:
        # Key presence, not truthiness: an annotated pair with nothing to say
        # persists a dict of nulls, which is a measurement, not a missing row.
        annotated = "settlement_timing" in observation
        timing = observation.get("settlement_timing") or {}
        asymmetric = bool(timing.get("asymmetric"))
        if annotated:
            annotated_observations += 1
            annotated_pairs.add(_pair_key(observation))
        if asymmetric:
            asymmetric_pairs.add(_pair_key(observation))
        gap = _decimal(observation.get("best_gap"))
        if gap is None:
            continue
        if not annotated:
            unannotated += 1
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
        "unannotated_observations": unannotated,
        "observations_after_horizon": after_horizon,
        "annotated_observations": annotated_observations,
        "annotated_pairs": len(annotated_pairs),
        "asymmetric_pairs": len(asymmetric_pairs),
        "asymmetric_observations": len(asymmetric_gaps),
        "symmetric_observations": len(symmetric_gaps),
        "asymmetric_median_gap": _median_gap(asymmetric_gaps),
        "symmetric_median_gap": _median_gap(symmetric_gaps),
        "asymmetry_measured": bool(asymmetric_pairs),
        "asymmetry_blind_spot": _asymmetry_blind_spot(annotated_pairs, asymmetric_pairs),
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


def _go_decision(
    verified_rate: Decimal,
    locked_capital: dict,
    median_notional: Decimal | None,
    tradeable_opportunities: set,
) -> dict:
    """All four sub-tests, reported individually so a GO is never a black box.

    The charter's decision rule is a conjunction; publishing only the aggregate
    made a day-2 GO look like evidence. Each leg is named, each carries its own
    measured value, and a leg with no eligible population reports `null` rather
    than passing by default — an untested condition is not a satisfied one.
    """
    annualized = _decimal(locked_capital.get("median_annualized"))
    tests = {
        "frequency": verified_rate >= GO_MIN_OPPORTUNITIES_PER_MONTH,
        "return_on_locked_capital": (
            None
            if annualized is None
            else annualized >= GO_MIN_ANNUALIZED_RETURN_ON_LOCKED_CAPITAL
        ),
        "basket_size": (
            None
            if median_notional is None
            else median_notional >= GO_MIN_MEDIAN_BASKET_NOTIONAL_USD
        ),
        # A basket priced against a venue that cannot be traded is not evidence
        # that an opportunity exists, however often it recurs.
        "tradeable_venue_evidence": bool(tradeable_opportunities),
    }
    return {
        "go": all(result is True for result in tests.values()),
        "tests": tests,
        "thresholds": {
            "opportunities_per_30_days": GO_MIN_OPPORTUNITIES_PER_MONTH,
            "annualized_return_on_locked_capital": str(
                GO_MIN_ANNUALIZED_RETURN_ON_LOCKED_CAPITAL
            ),
            "median_basket_notional_usd": str(GO_MIN_MEDIAN_BASKET_NOTIONAL_USD),
            "provisional_pending_owner_signoff": [
                "annualized_return_on_locked_capital",
                "median_basket_notional_usd",
            ],
        },
    }


def _return_on_locked_capital(executable: list[dict]) -> dict:
    """The charter's phase-2 metric: edge divided by how long capital is stuck.

    A paired basket frees capital only when BOTH legs settle, so the horizon is
    the later leg's published anchor — already computed by
    ``atlas.settlement_timing`` and carried on every observation. Nothing
    divided by it until now, which is why a 2.2c gap on a 252-day FOMC contract
    read as an opportunity: it annualizes to 3.4%, below the risk-free rate.

    Reported per family as well as overall, because the families differ by
    ~4x and a single median hides which one carries the headline. Observations
    without a published horizon are counted, never assumed to be short-dated.
    """
    rows: list[tuple[str, Decimal]] = []
    missing_horizon = 0
    for observation in executable:
        basket = _best_basket(observation)
        gap = _decimal(observation.get("best_gap"))
        cost = _decimal(basket.get("cost"))
        timing = observation.get("settlement_timing") or {}
        days = _decimal(timing.get("days_to_settlement"))
        if gap is None or cost is None or cost <= 0 or days is None or days <= 0:
            missing_horizon += 1
            continue
        annualized = (gap / cost) * (Decimal(365) / days)
        rows.append((str(observation.get("event_subject") or "").split("|")[0], annualized))
    if not rows:
        return {
            "observations_with_horizon": 0,
            "observations_without_horizon": missing_horizon,
            "median_annualized": None,
        }
    values = [value for _, value in rows]
    by_family: dict[str, list[Decimal]] = {}
    for family, value in rows:
        by_family.setdefault(family, []).append(value)
    return {
        "observations_with_horizon": len(rows),
        "observations_without_horizon": missing_horizon,
        "median_annualized": str(median(values).quantize(Decimal("0.0001"))),
        "max_annualized": str(max(values).quantize(Decimal("0.0001"))),
        "by_family_median_annualized": {
            family: str(median(vals).quantize(Decimal("0.0001")))
            for family, vals in sorted(by_family.items())
        },
    }


def _basket_notionals(executable: list[dict]) -> list[Decimal]:
    """Dollar capital per basket at the binding leg's displayed depth."""
    notionals = []
    for observation in executable:
        basket = _best_basket(observation)
        size = _decimal(basket.get("basket_size")) or _decimal(basket.get("kalshi_size"))
        cost = _decimal(basket.get("cost"))
        if size is not None and cost is not None:
            notionals.append(size * cost)
    return notionals


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
