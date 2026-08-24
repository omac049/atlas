"""Settlement Clarity Score — a deterministic A–F grade of one contract's fine print.

This is the contract-intelligence product's core asset: for ANY single market on
either venue, a named grade of how completely the venue published what decides
the payout. It needs no cross-venue twin, no order book, and no model — it reads
the venue's own text through the deterministic assessors that already exist and
subtracts a fixed, published number of points for each branch the text leaves
undetermined.

READ-ONLY CONSUMER — READ THIS BEFORE USING ANYTHING HERE.

Nothing here decides truth. This module imports `atlas.settlement`,
`atlas.policy_evidence`, and `atlas.settlement_timing`; those modules must never
import it back. The dependency is one-way on purpose: `assess_settlement_guarantee`
and `verify_equivalence` are frozen for the 90-day study, and a grade must never
become an input to an approval label, a mismatch code, or a settlement verdict.
A market graded `A` is still a candidate; a market graded `F` is still whatever
the deterministic pipeline says it is. Paper-only: this module places nothing,
prices nothing, and reads no credentials.

The grade must never overclaim. It measures the PUBLISHED TEXT, not the venue's
honesty and not the probability of a bad settlement — a venue with sloppy prose
and a spotless record still grades badly, and the prose below says so in the
terms a reader can check. Every deduction names the branch that is missing and
what the venue would have to publish to clear it.

Scoring (fixed; change it only with a version bump and a note in the report):

* Start at 100 points.
* No published text at all -> grade ``F``, score ``0``, the single finding
  ``NO_RULES_TEXT``. Every other blocker is a consequence of that absence, not
  an independent defect, so listing them would be padding.
* Each remaining finding subtracts its published points ONCE. Codes this module
  does not recognize subtract nothing — an unknown code is not evidence of a
  defect, and inventing a penalty for one would be the kind of overclaim the
  product cannot afford.
* A discretionary fair-price clause CAPS the grade at ``D`` regardless of score:
  discretion is the opposite of clarity, and no amount of complete prose
  elsewhere compensates for the exchange reserving the outcome to itself. The cap
  is a ceiling, not a deduction, so the score still reports how complete the rest
  of the text is.
* Bands: A >= 90, B >= 75, C >= 60, D >= 40, F < 40. Score clamps at 0.

Clarity is NOT the settlement guarantee. A `GUARANTEED` contract can still lose
points here for text the guarantee path never required (the guarantee asks
whether every outcome-determining branch is covered; clarity also asks whether a
reader can find the cancellation and revision terms at all). That divergence is
the point: it is what makes the grade informative catalog-wide.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from atlas.models import Market
from atlas.policy_evidence import parse_market_policy_evidence
from atlas.settlement import assess_settlement_guarantee
from atlas.settlement_timing import detect_settlement_timing_asymmetry

SCORING_VERSION = "1.0"
SCAN_KIND = "SETTLEMENT_CLARITY_SCAN"

NO_RULES_TEXT = "NO_RULES_TEXT"
DISCRETIONARY_FAIR_PRICE_SETTLEMENT = "DISCRETIONARY_FAIR_PRICE_SETTLEMENT"

# Worst-to-best, so a cap is `max(grade, cap)` on this ordering.
GRADES = ("A", "B", "C", "D", "F")
GRADE_BANDS: tuple[tuple[int, str], ...] = ((90, "A"), (75, "B"), (60, "C"), (40, "D"))
DISCRETION_GRADE_CAP = "D"

# Two frozen assessors describe the same defect — "the rules never name the
# publication whose number settles this" — under two different code names. They
# collapse into one finding so a contract is never docked twice for it.
_CANONICAL_CODES = {"MISSING_RESOLUTION_SOURCE": "MISSING_AUTHORITATIVE_SOURCE"}

# Points removed per finding. A defect that leaves a whole payout branch
# undetermined costs more than one that leaves a reader guessing about wording.
_DEDUCTIONS: dict[str, int] = {
    "MISSING_AUTHORITATIVE_SOURCE": 20,
    "CONFLICTING_AUTHORITATIVE_SOURCE": 20,
    "MISSING_CANCELLATION_POLICY": 20,
    "NON_GUARANTEED_CANCELLATION_POLICY": 20,
    "FAMILY_POLICY_INCOMPLETE": 20,
    "MISSING_REVISION_POLICY": 15,
    "CONFLICTING_REVISION_POLICY": 15,
    "NO_EXPLICIT_EXCEPTION_FALLBACK": 15,
    "MISSING_AFFIRMATIVE_BRANCH": 15,
    "UNPARSED_SETTLEMENT_POLICY": 10,
    "UNPARSED_CANCELLATION_POLICY": 10,
    "MISSING_NEGATIVE_BRANCH": 10,
}

# Plain English for every code this module can emit. Same pattern as
# `_BLOCKER_PROSE` in `atlas.intel`: the code stays beside the prose so a reader
# can grep the repo for it.
_FINDING_PROSE: dict[str, str] = {
    NO_RULES_TEXT: (
        "the venue publishes no rules, resolution, or description text for this "
        "contract at all, so there is nothing to grade"
    ),
    DISCRETIONARY_FAIR_PRICE_SETTLEMENT: (
        "the venue reserves settlement at a discretionary fair price, so the "
        "exchange decides the payout rather than the published terms"
    ),
    "MISSING_AUTHORITATIVE_SOURCE": (
        "the rules never name the authoritative publication whose number decides "
        "the outcome"
    ),
    "CONFLICTING_AUTHORITATIVE_SOURCE": (
        "the rules name more than one authoritative source, and those sources can "
        "disagree with each other"
    ),
    "MISSING_CANCELLATION_POLICY": (
        "the rules never say what happens to open positions if the event is "
        "canceled, voided, or never takes place"
    ),
    "UNPARSED_CANCELLATION_POLICY": (
        "the rules mention cancellation but never state the resulting payout in "
        "terms a reader can act on"
    ),
    "NON_GUARANTEED_CANCELLATION_POLICY": (
        "the published cancellation terms leave the payout to the exchange's "
        "discretion"
    ),
    "FAMILY_POLICY_INCOMPLETE": (
        "the rules omit terms this contract family requires — the measurement "
        "basis, the revision rule, or the missing-release branch its peers publish"
    ),
    "MISSING_REVISION_POLICY": (
        "the rules never say whether a later revision of the underlying number "
        "changes the outcome"
    ),
    "CONFLICTING_REVISION_POLICY": (
        "the rules describe more than one revision rule and they contradict each "
        "other"
    ),
    "NO_EXPLICIT_EXCEPTION_FALLBACK": (
        "the rules publish no terminal branch for the case where the deciding "
        "number is never released or the criterion never fires"
    ),
    "UNPARSED_SETTLEMENT_POLICY": (
        "no settlement terms could be read from the published text in a form that "
        "determines an outcome"
    ),
    "MISSING_NEGATIVE_BRANCH": (
        "the rules say what settles the contract Yes but never state what settles "
        "it No"
    ),
    "MISSING_AFFIRMATIVE_BRANCH": (
        "the rules never state the condition that settles the contract Yes"
    ),
}

# The one thing the VENUE would have to publish to clear each finding. Always an
# act of publication: no code change on this side can make an unpublished branch
# exist, and a "fix" that implied otherwise would be the product lying.
_FINDING_FIX: dict[str, str] = {
    NO_RULES_TEXT: "publish the settlement rules on the market page",
    DISCRETIONARY_FAIR_PRICE_SETTLEMENT: (
        "replace the discretionary fair-price clause with published terms that "
        "name the outcome in every branch"
    ),
    "MISSING_AUTHORITATIVE_SOURCE": "name the exact source and release in the rules text",
    "CONFLICTING_AUTHORITATIVE_SOURCE": (
        "name a single source, or publish which one wins when they differ"
    ),
    "MISSING_CANCELLATION_POLICY": "publish the payout for a canceled or abandoned event",
    "UNPARSED_CANCELLATION_POLICY": (
        "state the cancellation payout explicitly — Yes, No, half payout, or refund"
    ),
    "NON_GUARANTEED_CANCELLATION_POLICY": (
        "publish a fixed cancellation payout instead of a discretionary one"
    ),
    "FAMILY_POLICY_INCOMPLETE": (
        "publish the terms other contracts in this family already carry"
    ),
    "MISSING_REVISION_POLICY": (
        "publish whether the first release or a later revision settles the market"
    ),
    "CONFLICTING_REVISION_POLICY": "publish a single revision rule",
    "NO_EXPLICIT_EXCEPTION_FALLBACK": "publish what happens when the data never arrives",
    "UNPARSED_SETTLEMENT_POLICY": (
        "publish the settlement terms as explicit conditions, not prose alone"
    ),
    "MISSING_NEGATIVE_BRANCH": (
        "publish the No branch explicitly, e.g. 'otherwise the market resolves No'"
    ),
    "MISSING_AFFIRMATIVE_BRANCH": "publish the Yes condition explicitly",
}

# Disclosed, never scored: an early-determination clause is a fact about WHEN a
# contract can settle, not about whether its terms are clear. Scoring it would
# conflate two different things the product sells separately.
_FLAG_PROSE = {
    "EARLY_MEDIA_CONSENSUS": (
        "the rules allow settling on a consensus of media calls, before the "
        "official result exists"
    ),
    "EARLY_DETERMINATION_CLAUSE": (
        "the rules reserve the right to determine this contract early"
    ),
    "EARLY_MEDIA_PROJECTION": (
        "the rules refer to media calls or projections as settlement input"
    ),
    "EARLY_PROJECTED_WINNER": (
        "the rules can settle on a projected winner rather than a final one"
    ),
    "EARLY_RACE_CALL": (
        "the rules can settle on a race called by news organizations"
    ),
    "EARLY_BEFORE_CERTIFICATION": (
        "the rules can settle before the official certification of the result"
    ),
}

_TENTH = Decimal("0.1")

# What a scan artifact must say about itself. The per-venue means invite a
# cross-venue comparison, and one of them is not currently fair — saying so is
# cheaper than being caught by a reader who checks.
SCAN_LIMITS: tuple[str, ...] = (
    ("grades only the text carried on the canonical Market object from each "
    "venue's catalog sweep; nothing is fetched from a second endpoint and "
    "nothing is inferred from titles or categories"),
    ("Kalshi publishes settlement sources on the EVENT record, which this sweep "
    "does not read, so its MISSING_AUTHORITATIVE_SOURCE findings overstate the "
    "gap and its mean score is NOT comparable to Polymarket US's"),
    ("a grade measures published text, not a venue's settlement record: a "
    "vaguely worded contract that always settles correctly still grades badly"),
    ("category means only separate contracts whose venue publishes a category "
    "on the market record; everything else is pooled as uncategorized"),
)


def clarity_score(market: Market, *, graded_at: datetime | None = None) -> dict:
    """Grade one market's published settlement text. Pure, deterministic, read-only.

    ``graded_at`` may be supplied so a report is byte-reproducible across runs;
    it is the only value in the output that is not a function of the market.
    """
    stamp = (graded_at or datetime.now(UTC)).isoformat()
    header = {
        "market_id": market.market_id,
        "venue": market.venue.value,
        "title": market.title,
    }

    if not _has_published_text(market):
        return {
            **header,
            "grade": "F",
            "score": 0,
            "guarantee_status": "UNKNOWN",
            "findings": [_finding(NO_RULES_TEXT, 100)],
            "flags": [],
            "graded_at": stamp,
        }

    guarantee = assess_settlement_guarantee(market)
    evidence = parse_market_policy_evidence(market)
    raw_codes = [
        *(str(code) for code in guarantee.get("reason_codes") or []),
        *(str(code) for code in evidence.blockers),
    ]
    codes = list(dict.fromkeys(_CANONICAL_CODES.get(code, code) for code in raw_codes))

    score = 100
    findings = []
    for code in codes:
        if code == DISCRETIONARY_FAIR_PRICE_SETTLEMENT:
            # A ceiling, not a deduction: see the module docstring.
            findings.append(_finding(code, 0))
            continue
        points = _DEDUCTIONS.get(code)
        if points is None:
            continue
        score -= points
        findings.append(_finding(code, points))

    score = max(score, 0)
    grade = _band(score)
    status = str(guarantee.get("status") or "UNKNOWN")
    if status == "NON_GUARANTEED" or DISCRETIONARY_FAIR_PRICE_SETTLEMENT in codes:
        grade = max(grade, DISCRETION_GRADE_CAP, key=GRADES.index)

    findings.sort(key=lambda finding: (-finding["points"], finding["code"]))
    return {
        **header,
        "grade": grade,
        "score": score,
        "guarantee_status": status,
        "findings": findings,
        "flags": _flags(market),
        "graded_at": stamp,
    }


def flag_prose(code: str) -> str:
    """Plain English for a disclosed flag, or the code itself when it is new."""
    return _FLAG_PROSE.get(code, code)


def clarity_scan_report(
    markets: list[Market],
    *,
    generated_at: datetime | None = None,
    worst_limit: int = 20,
    degraded_venues: list[str] | None = None,
    scope: dict | None = None,
) -> dict:
    """Aggregate a bounded catalog sweep into the dated scan artifact.

    Takes markets rather than grades so the per-category means can read the
    venue's own published category field. ``degraded_venues`` records a venue
    whose catalog fetch failed and ``scope`` records what the sweep covered:
    the artifact must never let a missing venue read as a venue with no bad
    contracts, nor a truncated sweep read as the whole catalog.
    """
    now = generated_at or datetime.now(UTC)
    grades = [clarity_score(market, graded_at=now) for market in markets]
    rows = [
        {
            "market_id": grade["market_id"],
            "venue": grade["venue"],
            "title": grade["title"],
            "grade": grade["grade"],
            "score": grade["score"],
            "findings": [finding["code"] for finding in grade["findings"]],
        }
        for grade in grades
    ]

    per_venue: dict[str, dict] = {}
    per_category: dict[str, dict[str, str]] = {}
    venue_scores: dict[str, list[int]] = defaultdict(list)
    venue_grades: dict[str, Counter] = defaultdict(Counter)
    category_scores: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for market, grade in zip(markets, grades, strict=True):
        venue = grade["venue"]
        venue_scores[venue].append(grade["score"])
        venue_grades[venue][grade["grade"]] += 1
        category_scores[venue][market.category or "uncategorized"].append(grade["score"])
    for venue, scores in sorted(venue_scores.items()):
        per_venue[venue] = {
            "markets": len(scores),
            # Every band is present even at zero: an absent band must read as
            # "none graded there", never as "not measured".
            "grade_distribution": {band: venue_grades[venue][band] for band in GRADES},
            "mean_score": _mean(scores),
        }
        per_category[venue] = {
            category: _mean(values)
            for category, values in sorted(category_scores[venue].items())
        }

    worst = _worst_offenders(rows, worst_limit)
    return {
        "paper_only": True,
        "scan_kind": SCAN_KIND,
        "scoring_version": SCORING_VERSION,
        "generated_at": now.isoformat(),
        "degraded_venues": list(degraded_venues or []),
        "scope": dict(scope or {}),
        "limits": list(SCAN_LIMITS),
        "aggregates": {
            "markets_graded": len(rows),
            "per_venue": per_venue,
            "mean_score_per_category": per_category,
            "worst": worst,
        },
        "markets": rows,
    }


def render_scan_summary(report: dict) -> list[str]:
    """The lines the CLI prints. One per venue, then the totals."""
    aggregates = report["aggregates"]
    lines = []
    for venue, stats in aggregates["per_venue"].items():
        distribution = " ".join(
            f"{band}={stats['grade_distribution'][band]}" for band in GRADES
        )
        lines.append(
            f"clarity_scan_venue={venue} markets={stats['markets']} "
            f"{distribution} mean_score={stats['mean_score']}"
        )
    for venue in report.get("degraded_venues") or []:
        lines.append(f"clarity_scan_degraded={venue} (venue not graded, not 'no defects')")
    for venue in (report.get("scope") or {}).get("truncated_venues") or []:
        cap = (report.get("scope") or {}).get("max_markets_per_venue")
        lines.append(f"clarity_scan_truncated={venue} at={cap} (sample, not the full catalog)")
    lines.append(
        f"clarity_scan: paper_only=true graded={aggregates['markets_graded']} "
        f"venues={len(aggregates['per_venue'])} "
        f"worst={aggregates['worst'][0]['grade'] if aggregates['worst'] else '—'}"
    )
    lines.append(
        "clarity_scan_caveat=per-venue means are not cross-venue comparable "
        "(see limits[] in the artifact)"
    )
    return lines


def _worst_offenders(rows: list[dict], limit: int) -> list[dict]:
    """Lowest scores first, one row per distinct wording.

    A venue's ladder repeats the same rules text across dozens of strikes, so an
    undeduped list is ten copies of one contract — padding, which reads as
    padding and costs the report its credibility. The representative carries how
    many contracts share its title so the scale is not lost.
    """
    ordered = sorted(rows, key=lambda row: (row["score"], row["market_id"]))
    grouped: dict[tuple[str, str], dict] = {}
    for row in ordered:
        key = (row["venue"], row["title"])
        entry = grouped.get(key)
        if entry is None:
            entry = {**row, "contracts_with_this_title": 0}
            grouped[key] = entry
        entry["contracts_with_this_title"] += 1
    return sorted(grouped.values(), key=lambda row: (row["score"], row["market_id"]))[:limit]


def _finding(code: str, points: int) -> dict:
    return {
        "code": code,
        "points": points,
        "prose": _FINDING_PROSE[code],
        "fix": _FINDING_FIX[code],
    }


def _band(score: int) -> str:
    for floor, grade in GRADE_BANDS:
        if score >= floor:
            return grade
    return "F"


def _has_published_text(market: Market) -> bool:
    return any(
        (value or "").strip()
        for value in (market.raw_rules_text, market.resolution_text, market.description)
    )


def _flags(market: Market) -> list[str]:
    timing = detect_settlement_timing_asymmetry(market)
    if not timing or not timing["early_determination"]:
        return []
    return list(timing["early_codes"])


def _mean(scores: list[int]) -> str:
    if not scores:
        return "0.0"
    return str((Decimal(sum(scores)) / Decimal(len(scores))).quantize(_TENTH))
