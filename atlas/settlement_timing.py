"""Descriptive settlement-TIMING annotation for cross-venue candidate pairs.

OBSERVABILITY ONLY — READ THIS BEFORE USING ANYTHING HERE.

Nothing in this module may upgrade a pair's trust. It produces a *descriptive
tag* parsed from published venue rules text; it never touches
``assess_settlement_guarantee``'s verdicts or blocker codes, never influences
``verify_equivalence``, and can never create or support an
``APPROVED_EQUIVALENT``/``APPROVED_INVERSE`` label. If anything, a timing
asymmetry is a CAUTION signal: it means the two venues can pay out weeks apart
(and, in edge cases, disagree), which is a reason to trust a pair less, never
more. Deterministic rules still decide truth; this only records what the rules
text says about *when* each leg can settle.

Because the tag is a caution signal, over-flagging is the safe direction: a leg
whose text merely mentions media calls is tagged even if the sentence turns out
to disclaim them. An over-flagged pair loses nothing — the tag gates nothing.

Motivating case (verified live 2026-08-19): Kalshi's chamber-control contracts
(CONTROLH-2026 / CONTROLS-2026) publish "This market may be determined early
based on a consensus of media calls projecting control of the U.S. House ...
Otherwise, victory will be determined by the party identification of the
Speaker of the House on February 1, 2027." The Polymarket twin settles on
official state electoral authorities and the House, with a January 4 deadline.
Same event, potentially weeks apart in payout. The gap radar records the
asymmetry so the study can ask whether the cross-venue gap is compensation for
capital lock-up time or genuine mispricing.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

from atlas.models import Market

# Pair-level code emitted when exactly one leg publishes an early-determination
# clause. It is a caution tag, never an approval input.
SETTLEMENT_TIMING_ASYMMETRIC = "SETTLEMENT_TIMING_ASYMMETRIC"
# Secondary codes describing what the LATE leg publishes instead.
LATE_LEG_OFFICIAL_SOURCE_ANCHORED = "LATE_LEG_OFFICIAL_SOURCE_ANCHORED"
LATE_LEG_NO_EARLY_CLAUSE_PUBLISHED = "LATE_LEG_NO_EARLY_CLAUSE_PUBLISHED"

# Clauses that let a venue settle BEFORE the official/terminal criterion fires.
EARLY_DETERMINATION_RULES: tuple[tuple[str, str], ...] = (
    ("EARLY_MEDIA_CONSENSUS", r"consensus of media (?:calls?|projections?)"),
    (
        "EARLY_DETERMINATION_CLAUSE",
        r"(?:may|can|could|might|will) be (?:determined|resolved|settled|decided) early",
    ),
    ("EARLY_MEDIA_PROJECTION", r"\bmedia (?:calls?|projections?|reports?)\b"),
    ("EARLY_PROJECTED_WINNER", r"projected (?:winner|to win)|projecting (?:the )?(?:control|winner)"),
    (
        "EARLY_RACE_CALL",
        (
            r"called by (?:the )?(?:associated press|ap\b|decision desk|major (?:news|media)|"
            r"news networks?|media outlets?)"
        ),
    ),
    ("EARLY_BEFORE_CERTIFICATION", r"(?:prior to|before) (?:the )?(?:official )?certification"),
)

# Official-source / dated-deadline language: what a leg WITHOUT an early clause
# publishes instead. Descriptive only — never a guarantee input.
OFFICIAL_DETERMINATION_RULES: tuple[tuple[str, str], ...] = (
    ("OFFICIAL_STATE_ELECTORAL_AUTHORITIES", r"state electoral authorit(?:y|ies)"),
    (
        "OFFICIAL_CHAMBER_SOURCE",
        r"(?:united states|u\.?s\.?) (?:house of representatives|senate)\b",
    ),
    ("OFFICIAL_CERTIFIED_RESULTS", r"certified (?:election )?results?"),
    (
        "OFFICIAL_DATED_DEADLINE",
        (
            r"\b(?:by|on or before|no later than)\s+"
            r"(?:january|february|march|april|may|june|july|august|september|october|"
            r"november|december)\s+\d{1,2}"
        ),
    ),
)

_MONTH = (
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
)

# Terminal criterion published for the case the primary one never fires.
FALLBACK_RULES: tuple[tuple[str, str], ...] = (
    ("FALLBACK_SPEAKER_PARTY_ON_DATE", rf"speaker of the house on ({_MONTH}\s+\d{{1,2}},?\s+\d{{4}})"),
    ("FALLBACK_FIRST_ELECTED_SPEAKER_PARTY", r"first elected speaker of the house"),
    ("FALLBACK_FIRST_PRESIDENT_PRO_TEMPORE", r"first (?:selected|elected) president pro tempore"),
)

_MONTH_NUMBERS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_DAY = Decimal(86400)
_TENTH = Decimal("0.1")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower().strip())


def _market_text(market: Market) -> str:
    """Same text surface the fingerprints use: everything the venue published."""
    raw = market.raw_market_json
    return _clean(
        " ".join(
            str(value)
            for value in (
                market.title,
                market.subtitle or "",
                raw.get("question") or "",
                market.description or "",
                market.raw_rules_text,
                market.resolution_text,
            )
            if value
        )
    )


def _fallback_date(raw: str) -> str | None:
    match = re.match(rf"({_MONTH})\s+(\d{{1,2}}),?\s+(\d{{4}})", _clean(raw))
    if not match:
        return None
    month = _MONTH_NUMBERS[match.group(1)]
    return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(2)):02d}"


def detect_settlement_timing_asymmetry(market: Market) -> dict | None:
    """Tag one leg's published settlement-timing language. Descriptive only.

    Returns ``None`` when the venue text carries no timing signal at all.
    Otherwise a small deterministic dict: whether an early-determination clause
    exists, which phrases matched, the official-source/deadline language found,
    and the terminal fallback criterion when detectable. This NEVER feeds a
    verification status or a settlement-guarantee verdict.
    """
    text = _market_text(market)
    early = sorted({code for code, pattern in EARLY_DETERMINATION_RULES if re.search(pattern, text)})
    official = sorted(
        {code for code, pattern in OFFICIAL_DETERMINATION_RULES if re.search(pattern, text)}
    )
    fallback: dict[str, str | None] | None = None
    for code, pattern in FALLBACK_RULES:
        match = re.search(pattern, text)
        if not match:
            continue
        fallback = {
            "code": code,
            "date": _fallback_date(match.group(1)) if match.groups() else None,
        }
        break
    if not early and not official and fallback is None:
        return None
    return {
        "venue": market.venue.value,
        "early_determination": bool(early),
        "early_codes": early,
        "official_codes": official,
        "fallback": fallback,
    }


def compare_settlement_timing(kalshi_market: Market, polymarket_market: Market) -> dict | None:
    """Pair-level timing asymmetry, or ``None`` when both legs read the same.

    Emits ``SETTLEMENT_TIMING_ASYMMETRIC`` when exactly one leg publishes an
    early-determination clause, naming which venue can settle earlier and why.
    Both-early and neither-early pairs emit nothing. Caution tag only: it can
    never approve, upgrade, or unblock a pair.
    """
    legs = (
        (kalshi_market, detect_settlement_timing_asymmetry(kalshi_market)),
        (polymarket_market, detect_settlement_timing_asymmetry(polymarket_market)),
    )
    early_flags = [bool(tags and tags["early_determination"]) for _, tags in legs]
    if early_flags[0] == early_flags[1]:
        return None
    early_index = 0 if early_flags[0] else 1
    early_market, early_tags = legs[early_index]
    late_market, late_tags = legs[1 - early_index]
    late_codes = list(late_tags["official_codes"]) if late_tags else []
    codes = [SETTLEMENT_TIMING_ASYMMETRIC]
    codes.append(
        LATE_LEG_OFFICIAL_SOURCE_ANCHORED if late_codes else LATE_LEG_NO_EARLY_CLAUSE_PUBLISHED
    )
    return {
        "codes": codes,
        "early_venue": early_market.venue.value,
        "early_codes": list(early_tags["early_codes"]) if early_tags else [],
        "late_venue": late_market.venue.value,
        "late_codes": late_codes,
        "late_fallback": (late_tags or {}).get("fallback"),
        "early_fallback": (early_tags or {}).get("fallback"),
    }


def _parse_at(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _leg_horizon(market: Market) -> tuple[datetime, str] | None:
    for field in ("resolution_time", "close_time"):
        anchor = getattr(market, field, None)
        if anchor is None:
            continue
        stamped = anchor if anchor.tzinfo else anchor.replace(tzinfo=UTC)
        return stamped, f"{market.venue.value}_{field}"
    return None


def settlement_horizon_days(
    kalshi_market: Market, polymarket_market: Market, observed_at: object
) -> tuple[str | None, str | None]:
    """Days from ``observed_at`` until the LATER leg's published settlement anchor.

    A locked basket releases capital only once BOTH legs settle, so the later
    anchor is the honest lock-up horizon. Each leg prefers its published
    resolution time and falls back to its close time; the basis is recorded so
    a report never has to guess which timestamp it is reading.
    """
    observed = _parse_at(observed_at)
    if observed is None:
        return None, None
    anchors = [
        anchor for anchor in (_leg_horizon(kalshi_market), _leg_horizon(polymarket_market)) if anchor
    ]
    if not anchors:
        return None, None
    horizon, basis = max(anchors, key=lambda item: item[0])
    days = Decimal((horizon - observed).total_seconds()) / _DAY
    return str(days.quantize(_TENTH)), basis


def settlement_timing_annotation(
    kalshi_market: Market, polymarket_market: Market, observed_at: object = None
) -> dict:
    """The small, deterministic dict the gap radar persists per observation.

    OBSERVABILITY ONLY — it annotates, it never gates. Keys are stable and
    JSON-safe (Decimals are stringified) so persisted observations stay
    byte-comparable across runs.
    """
    asymmetry = compare_settlement_timing(kalshi_market, polymarket_market)
    days, basis = settlement_horizon_days(kalshi_market, polymarket_market, observed_at)
    return {
        "asymmetric": asymmetry is not None,
        "codes": list(asymmetry["codes"]) if asymmetry else [],
        "early_venue": asymmetry["early_venue"] if asymmetry else None,
        "early_codes": list(asymmetry["early_codes"]) if asymmetry else [],
        "days_to_settlement": days,
        "horizon_basis": basis,
    }
