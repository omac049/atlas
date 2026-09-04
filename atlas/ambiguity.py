"""Predicate Ambiguity Score — does this market turn on an undefined word?

Third hypothesis, pre-registered in
``docs/decisions/2026-09-02-predicate-ambiguity-charter.md``. Test A disproved
the theory that settlement-MACHINERY gaps predict disputes: disputed markets
graded identically to controls. What the disputes actually turned on was
MEANING — "suit", "performance", "permanent", "invasion", "involved", "found",
"banned". This module scores that, and only that.

Scale is 0-100 where HIGHER MEANS MORE AMBIGUOUS — the opposite direction from
``atlas.clarity``, deliberately, so the two scores can never be confused for
each other in a report or a headline.

READ THIS BEFORE USING ANYTHING HERE.

- **Read-only, one-way.** Nothing in verification, settlement, normalization,
  or the approval pipeline may import this module. A score that could feed an
  approval label would be an instrument grading its own inputs.
- **The instrument is FROZEN at the charter's freeze commit.** After that hash,
  no feature, weight, or threshold changes for the duration of the test. A
  change is a new instrument and a new charter, not a tweak.
- **Development-set performance is not evidence.** The charter's Test A corpus
  was read by this module's author before it was written, so separation there
  measures fit. Only the post-freeze holdout can support a claim.

Scoring: start at 0, add for ambiguity signals, subtract for anchoring
signals, clamp to 0-100. A market is FLAGGED at >= AMBIGUITY_FLAG_THRESHOLD.
Every feature carries plain-English prose so a reader can check the machine's
reasoning against the text themselves.

What this does NOT read: outcomes, prices, volume, news, dispute status, or
the market's identity. Its only input is published resolution text. It cannot
know a market was disputed, which is what makes the holdout test meaningful.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from atlas.models import Market

SCORING_VERSION = "1.0"

# Fixed by the charter (§3). Changing this is changing the instrument.
AMBIGUITY_FLAG_THRESHOLD = 50

# --- Ambiguity signals: the text leaves the predicate to judgment -----------

# Judgment language quoted from the development-set disputes' own rules text.
# These are the phrases that made "did it happen?" a matter of opinion.
_JUDGMENT_QUALIFIERS = (
    r"consensus of credible reporting",
    r"credible reporting",
    r"generally (?:understood|recognized|accepted)",
    r"substantially",
    r"definitively",
    r"explicitly (?:indicate|state|say)",
    r"intended to",
    r"in the physical presence of",
    r"any portion of",
    r"widely (?:reported|regarded|considered)",
    r"reasonable(?:ly)? (?:interpret|determin|judg)",
    r"at (?:its|their) (?:sole )?discretion",
    r"deemed",
    r"appears? to",
)

# The venue reserving interpretive power. Distinct from a fair-price
# cancellation clause (that is machinery, which atlas.clarity already scores).
_VENUE_CLARIFICATION = (
    r"reserves? the right to (?:clarify|interpret|determine|resolve)",
    r"(?:sole|final) (?:discretion|judgment|determination)",
    r"may (?:clarify|amend) (?:these|the) (?:rules|terms)",
    r"final(?:ity)? of (?:the )?(?:resolution|determination) (?:rests|lies)",
)

_MULTI_SOURCE_DISCRETION = (
    r"will also suffice",
    r"or a consensus",
    r"(?:primary|secondary) resolution source",
    r"if .{0,40} (?:is )?unavailable, .{0,60}(?:source|report)",
    r"any (?:credible|reputable) (?:source|outlet)",
)

# --- Anchoring signals: the predicate points at something measurable -------

_MEASURABLE_ANCHOR = (
    # A number with a comparison — the shape of every macro contract.
    (
        r"(?:above|below|at least|greater than|less than|exceeds?|under)\s+"
        r"[-+]?\$?\d[\d,.]*\s*(?:%|percent|bps|k\b|million|billion)?"
    ),
    # A named statistical series or issuing body.
    r"bureau of labor statistics|federal reserve|bureau of economic analysis",
    r"\bBLS\b|\bFOMC\b|\bBEA\b|\bCPI\b|nonfarm payroll",
    # An official act by a named body.
    r"(?:signed into law|enacted by|certified by|officially announced by)",
    # A scheduled, dated event.
    r"scheduled for \w+ \d{1,2}",
)

_DEFINITION_CLAUSE = (
    r"for (?:the )?purposes of this market",
    r"(?:shall|will) be defined as",
    r"is defined as",
    r'"[^"]{2,40}" means',
    r"means, for this (?:market|contract)",
)

_EDGE_CASES_ENUMERATED = (
    r"for the avoidance of doubt",
    r"(?:does|do) not count",
    r"(?:shall|will) not (?:qualify|count|be considered)",
    r"excluding\b",
    r"the following (?:do|does) not",
)

# Weights. Fixed in code at the freeze commit per charter §3; each is bounded
# so no single feature can carry a market across the flag threshold alone.
_AMBIGUITY_WEIGHTS: dict[str, int] = {
    "OPERATIVE_TERM_UNDEFINED": 30,
    "JUDGMENT_QUALIFIER": 25,
    "MULTI_SOURCE_DISCRETION": 20,
    "VENUE_CLARIFICATION_RESERVED": 20,
}
_ANCHOR_WEIGHTS: dict[str, int] = {
    "MEASURABLE_ANCHOR": -30,
    "DEFINITION_CLAUSE_PRESENT": -25,
    "EDGE_CASES_ENUMERATED": -15,
}

_PROSE: dict[str, str] = {
    "OPERATIVE_TERM_UNDEFINED": (
        "the market turns on a word the rules never define, so two readers can "
        "disagree about whether it happened"
    ),
    "JUDGMENT_QUALIFIER": (
        "resolution depends on a judgment call — consensus, intent, or what is "
        "generally understood — rather than on something countable"
    ),
    "MULTI_SOURCE_DISCRETION": (
        "more than one source can decide the outcome, and the rules do not say "
        "which one wins when they disagree"
    ),
    "VENUE_CLARIFICATION_RESERVED": (
        "the venue reserves the right to interpret or clarify the terms after "
        "the fact"
    ),
    "MEASURABLE_ANCHOR": (
        "the predicate points at something measurable — a number, a named data "
        "series, or an official act by a named body"
    ),
    "DEFINITION_CLAUSE_PRESENT": (
        "the rules define the operative term explicitly"
    ),
    "EDGE_CASES_ENUMERATED": (
        "the rules enumerate what does and does not count"
    ),
}

# Words that carry the predicate in the development-set disputes. Presence
# WITHOUT a definition clause is the signal; presence alone is not.
_JUDGMENT_LADEN_TERMS = (
    "suit", "perform", "permanent", "invade", "invasion", "involved",
    "found", "banned", "ban", "declassif", "control of", "peace deal",
    "wear", "attend", "appear", "meaningful", "significant", "major",
)


def _compiled(patterns: tuple[str, ...]) -> list[re.Pattern[str]]:
    return [re.compile(pattern, re.IGNORECASE) for pattern in patterns]


_JUDGMENT_RE = _compiled(_JUDGMENT_QUALIFIERS)
_VENUE_RE = _compiled(_VENUE_CLARIFICATION)
_MULTI_SOURCE_RE = _compiled(_MULTI_SOURCE_DISCRETION)
_ANCHOR_RE = _compiled(_MEASURABLE_ANCHOR)
_DEFINITION_RE = _compiled(_DEFINITION_CLAUSE)
_EDGE_RE = _compiled(_EDGE_CASES_ENUMERATED)


def _resolution_text(market: Market) -> str:
    """Only the text that decides the payout — never the title.

    The title is how a market is marketed; the rules are what settles it. A
    title-driven score would drift toward measuring headline style.
    """
    raw = market.raw_market_json or {}
    parts = (
        market.raw_rules_text,
        market.resolution_text,
        raw.get("rules_primary"),
        raw.get("rules_secondary"),
        market.description,
    )
    return " ".join(str(part) for part in parts if part)


def _first_match(text: str, patterns: list[re.Pattern[str]]) -> str | None:
    for pattern in patterns:
        found = pattern.search(text)
        if found:
            return found.group(0)[:80]
    return None


def ambiguity_score(market: Market, *, scored_at: datetime | None = None) -> dict:
    """Score one market's predicate. Pure, deterministic, read-only.

    Returns the score, the flag, and every feature that fired with the exact
    text that triggered it — a reader must be able to check the machine
    against the contract without rerunning anything.
    """
    stamp = (scored_at or datetime.now(UTC)).isoformat()
    header = {
        "market_id": market.market_id,
        "venue": market.venue.value,
        "title": market.title,
        "scoring_version": SCORING_VERSION,
    }
    text = _resolution_text(market)
    if not text.strip():
        # No published predicate is not the same as an unambiguous one; it is
        # unmeasurable, and saying so beats scoring a zero that reads "clear".
        return {
            **header,
            "score": None,
            "flagged": False,
            "features": [],
            "unmeasurable": "NO_RESOLUTION_TEXT",
            "scored_at": stamp,
        }

    features: list[dict] = []

    def fire(code: str, weight: int, evidence: str | None) -> None:
        features.append(
            {
                "code": code,
                "points": weight,
                "prose": _PROSE[code],
                "evidence": evidence,
            }
        )

    has_definition = _first_match(text, _DEFINITION_RE)

    lowered = text.lower()
    laden = next((term for term in _JUDGMENT_LADEN_TERMS if term in lowered), None)
    # An undefined judgment-laden term is the theory's core signal. With a
    # definition clause present the term is anchored, so the signal does not
    # fire — that is the whole distinction the hypothesis rests on.
    if laden and not has_definition:
        fire("OPERATIVE_TERM_UNDEFINED", _AMBIGUITY_WEIGHTS["OPERATIVE_TERM_UNDEFINED"], laden)

    for code, patterns in (
        ("JUDGMENT_QUALIFIER", _JUDGMENT_RE),
        ("MULTI_SOURCE_DISCRETION", _MULTI_SOURCE_RE),
        ("VENUE_CLARIFICATION_RESERVED", _VENUE_RE),
    ):
        evidence = _first_match(text, patterns)
        if evidence:
            fire(code, _AMBIGUITY_WEIGHTS[code], evidence)

    for code, patterns in (
        ("MEASURABLE_ANCHOR", _ANCHOR_RE),
        ("EDGE_CASES_ENUMERATED", _EDGE_RE),
    ):
        evidence = _first_match(text, patterns)
        if evidence:
            fire(code, _ANCHOR_WEIGHTS[code], evidence)
    if has_definition:
        fire("DEFINITION_CLAUSE_PRESENT", _ANCHOR_WEIGHTS["DEFINITION_CLAUSE_PRESENT"], has_definition)

    score = min(100, max(0, sum(feature["points"] for feature in features)))
    features.sort(key=lambda feature: (-feature["points"], feature["code"]))
    return {
        **header,
        "score": score,
        "flagged": score >= AMBIGUITY_FLAG_THRESHOLD,
        "features": features,
        "unmeasurable": None,
        "scored_at": stamp,
    }
