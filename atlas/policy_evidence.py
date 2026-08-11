"""Deterministic extraction and comparison of settlement-policy evidence.

This module is intentionally a sidecar: it performs no I/O and does not alter the
existing fingerprint or settlement pipeline.  Evidence comes only from supplied
policy text and an optional explicit resolution-source field.  Market titles,
categories, and other descriptive metadata are never used to fill missing policy.
"""

import re
from enum import StrEnum

from pydantic import BaseModel, Field

from atlas.models import Market


class ResolutionOutcome(StrEnum):
    YES = "YES"
    NO = "NO"


class CancellationDisposition(StrEnum):
    YES = "YES"
    NO = "NO"
    HALF_PAYOUT = "HALF_PAYOUT"
    VOID_REFUND = "VOID_REFUND"
    STANDARD_RULES = "STANDARD_RULES"
    FAIR_PRICE = "FAIR_PRICE"
    EXCHANGE_DISCRETION = "EXCHANGE_DISCRETION"
    UNPARSED = "UNPARSED"


class RevisionPolicy(StrEnum):
    LATEST_FINAL_REPORT = "LATEST_FINAL_REPORT"
    FIRST_OFFICIAL_RELEASE = "FIRST_OFFICIAL_RELEASE"
    LATEST_AVAILABLE_REVISION = "LATEST_AVAILABLE_REVISION"
    FINAL_PUBLISHED_VALUE = "FINAL_PUBLISHED_VALUE"
    CONFLICTING = "CONFLICTING"


class EvidenceOrigin(StrEnum):
    RULE_TEXT = "RULE_TEXT"
    EXPLICIT_SOURCE_FIELD = "EXPLICIT_SOURCE_FIELD"


class PolicyCompatibilityStatus(StrEnum):
    EXACT = "EXACT"
    COMPATIBLE = "COMPATIBLE"
    MISMATCH = "MISMATCH"
    INCOMPLETE = "INCOMPLETE"


class ResolutionBranchEvidence(BaseModel):
    outcome: ResolutionOutcome
    condition: str
    evidence_text: str


class CancellationEvidence(BaseModel):
    disposition: CancellationDisposition
    guaranteed: bool
    evidence_text: str


class RevisionEvidence(BaseModel):
    policy: RevisionPolicy
    matched_policies: list[RevisionPolicy] = Field(default_factory=list)
    evidence_text: str


class AuthoritativeSourceEvidence(BaseModel):
    canonical_name: str
    evidence_text: str
    origin: EvidenceOrigin


class EvidenceFieldPresence(BaseModel):
    """Capture provenance for every source field used by the policy gate."""

    raw_rules_text: bool = False
    resolution_text: bool = False
    description: bool = False
    resolution_source: bool = False
    explicit_binary_sides: bool = False
    affirmative_branch: bool = False
    negative_branch: bool = False
    cancellation_policy: bool = False
    revision_policy: bool = False
    authoritative_source: bool = False


class SettlementPolicyEvidence(BaseModel):
    schema_version: str = "1.0"
    affirmative_branch: ResolutionBranchEvidence | None = None
    negative_branch: ResolutionBranchEvidence | None = None
    cancellation: CancellationEvidence | None = None
    revision: RevisionEvidence | None = None
    authoritative_source: AuthoritativeSourceEvidence | None = None
    blockers: list[str] = Field(default_factory=list)
    complete: bool = False
    normalized_policy_text: str = ""
    field_presence: EvidenceFieldPresence = Field(default_factory=EvidenceFieldPresence)


class PolicyCompatibilityAssessment(BaseModel):
    status: PolicyCompatibilityStatus
    compatible: bool
    mismatch_codes: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


_YES_PATTERNS = (
    re.compile(
        r"\b(?:the|this)\s+market\s+(?:will\s+)?(?:resolve|settle)(?:s|d)?"
        r"(?:\s+to)?\s+yes\s+if\s+(?P<condition>[^.;]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bif\s+(?P<condition>[^.;]+?),\s*(?:then\s+)?(?:the|this)\s+market\s+"
        r"(?:will\s+)?(?:resolve|settle)(?:s|d)?(?:\s+to)?\s+yes\b",
        re.IGNORECASE,
    ),
)

_NO_PATTERNS = (
    re.compile(
        r"\botherwise\s*,?\s*(?:the|this)\s+market\s+(?:will\s+)?"
        r"(?:resolve|settle)(?:s|d)?(?:\s+to)?\s+no\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:the|this)\s+market\s+(?:will\s+)?(?:resolve|settle)(?:s|d)?"
        r"(?:\s+to)?\s+no\s+if\s+(?P<condition>[^.;]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bif\s+(?P<condition>[^.;]+?),\s*(?:then\s+)?(?:the|this)\s+market\s+"
        r"(?:will\s+)?(?:resolve|settle)(?:s|d)?(?:\s+to)?\s+no\b",
        re.IGNORECASE,
    ),
)

_CANCELLATION_MARKER = re.compile(
    r"\b(?:cancel(?:ed|led|lation)?|void(?:ed)?|abandon(?:ed|ment)?|"
    r"does not (?:begin|occur|take place)|will not (?:begin|occur|take place))\b",
    re.IGNORECASE,
)


def parse_policy_evidence(
    policy_text: str,
    authoritative_source: str | None = None,
) -> SettlementPolicyEvidence:
    """Extract explicit settlement evidence without consulting market metadata."""
    text = _collapse(policy_text)
    affirmative = _extract_branch(text, ResolutionOutcome.YES)
    negative = _extract_branch(text, ResolutionOutcome.NO)
    cancellation = _extract_cancellation(text)
    revision = _extract_revision(text)
    source, source_conflict = _extract_source(text, authoritative_source)

    blockers: list[str] = []
    if affirmative is None:
        blockers.append("MISSING_AFFIRMATIVE_BRANCH")
    if negative is None:
        blockers.append("MISSING_NEGATIVE_BRANCH")
    if cancellation is None:
        blockers.append("MISSING_CANCELLATION_POLICY")
    elif cancellation.disposition == CancellationDisposition.UNPARSED:
        blockers.append("UNPARSED_CANCELLATION_POLICY")
    elif not cancellation.guaranteed:
        blockers.append("NON_GUARANTEED_CANCELLATION_POLICY")
    if revision is None:
        blockers.append("MISSING_REVISION_POLICY")
    elif revision.policy == RevisionPolicy.CONFLICTING:
        blockers.append("CONFLICTING_REVISION_POLICY")
    if source is None:
        blockers.append("MISSING_AUTHORITATIVE_SOURCE")
    if source_conflict:
        blockers.append("CONFLICTING_AUTHORITATIVE_SOURCE")

    return SettlementPolicyEvidence(
        affirmative_branch=affirmative,
        negative_branch=negative,
        cancellation=cancellation,
        revision=revision,
        authoritative_source=source,
        blockers=blockers,
        complete=not blockers,
        normalized_policy_text=_normalize_text(text),
    )


def parse_market_policy_evidence(market: Market) -> SettlementPolicyEvidence:
    """Parse only explicit rule/description/source fields from a Market.

    In particular, this adapter deliberately excludes title, subtitle, category,
    event subject, event action, and fingerprint-derived policy fields.
    """
    explicit_binary_sides = _has_explicit_binary_sides(market)
    texts = [market.raw_rules_text, market.resolution_text, market.description or ""]
    policy_text = " ".join(dict.fromkeys(value.strip() for value in texts if value.strip()))
    evidence = parse_policy_evidence(policy_text, market.resolution_source)
    question = str(market.raw_market_json.get("question") or "").strip()
    if explicit_binary_sides and question.endswith("?"):
        condition = _normalize_condition(question[:-1])
        if evidence.affirmative_branch is None:
            evidence.affirmative_branch = ResolutionBranchEvidence(
                outcome=ResolutionOutcome.YES,
                condition=condition,
                evidence_text=question,
            )
            evidence.blockers = [
                code for code in evidence.blockers if code != "MISSING_AFFIRMATIVE_BRANCH"
            ]
        if evidence.negative_branch is None:
            evidence.negative_branch = ResolutionBranchEvidence(
                outcome=ResolutionOutcome.NO,
                condition=f"complement_of:{condition}",
                evidence_text="Explicit long=Yes and long=False No market sides",
            )
            evidence.blockers = [
                code for code in evidence.blockers if code != "MISSING_NEGATIVE_BRANCH"
            ]
        evidence.complete = not evidence.blockers
    if (
        explicit_binary_sides
        and evidence.affirmative_branch is not None
        and evidence.negative_branch is None
    ):
        # The affirmative condition came from explicit rule text; the venue's
        # published two-sided Yes/No contract structure supplies the complement.
        evidence.negative_branch = ResolutionBranchEvidence(
            outcome=ResolutionOutcome.NO,
            condition=f"complement_of:{evidence.affirmative_branch.condition}",
            evidence_text="Explicit venue-published binary Yes/No market sides",
        )
        evidence.blockers = [
            code for code in evidence.blockers if code != "MISSING_NEGATIVE_BRANCH"
        ]
        evidence.complete = not evidence.blockers
    evidence.field_presence = EvidenceFieldPresence(
        raw_rules_text=bool(market.raw_rules_text.strip()),
        resolution_text=bool(market.resolution_text.strip()),
        description=bool((market.description or "").strip()),
        resolution_source=bool(
            str(market.resolution_source or "").strip()
            and str(market.resolution_source).strip().lower() != "unknown"
        ),
        explicit_binary_sides=explicit_binary_sides,
        affirmative_branch=evidence.affirmative_branch is not None,
        negative_branch=evidence.negative_branch is not None,
        cancellation_policy=evidence.cancellation is not None,
        revision_policy=evidence.revision is not None,
        authoritative_source=evidence.authoritative_source is not None,
    )
    return evidence


def _has_explicit_binary_sides(market: Market) -> bool:
    raw = market.raw_market_json
    sides = raw.get("marketSides")
    if isinstance(sides, list):
        labels = {
            (bool(side.get("long")), str(side.get("description") or "").strip().lower())
            for side in sides
            if isinstance(side, dict) and side.get("long") is not None
        }
        if (True, "yes") in labels and (False, "no") in labels:
            return True
    # Kalshi publishes an explicit two-sided binary contract structure: the
    # market type is declared "binary" and both Yes and No sides are present
    # as venue fields.  This is published structure, not an inference.
    return (
        str(raw.get("market_type") or "").strip().lower() == "binary"
        and "yes_sub_title" in raw
        and "no_sub_title" in raw
    )


def assess_policy_compatibility(
    left: SettlementPolicyEvidence,
    right: SettlementPolicyEvidence,
) -> PolicyCompatibilityAssessment:
    """Compare complete policy evidence; incompleteness always dominates."""
    if not left.complete or not right.complete:
        blockers = [f"LEFT_{code}" for code in left.blockers]
        blockers.extend(f"RIGHT_{code}" for code in right.blockers)
        return PolicyCompatibilityAssessment(
            status=PolicyCompatibilityStatus.INCOMPLETE,
            compatible=False,
            blockers=blockers,
        )

    mismatches: list[str] = []
    if _branch_signature(left.affirmative_branch) != _branch_signature(
        right.affirmative_branch
    ):
        mismatches.append("AFFIRMATIVE_BRANCH_MISMATCH")
    if _branch_signature(left.negative_branch) != _branch_signature(right.negative_branch):
        mismatches.append("NEGATIVE_BRANCH_MISMATCH")
    if left.cancellation.disposition != right.cancellation.disposition:
        mismatches.append("CANCELLATION_POLICY_MISMATCH")
    if left.revision.policy != right.revision.policy:
        mismatches.append("REVISION_POLICY_MISMATCH")
    if (
        left.authoritative_source.canonical_name
        != right.authoritative_source.canonical_name
    ):
        mismatches.append("AUTHORITATIVE_SOURCE_MISMATCH")

    if mismatches:
        return PolicyCompatibilityAssessment(
            status=PolicyCompatibilityStatus.MISMATCH,
            compatible=False,
            mismatch_codes=mismatches,
        )

    status = (
        PolicyCompatibilityStatus.EXACT
        if left.normalized_policy_text == right.normalized_policy_text
        else PolicyCompatibilityStatus.COMPATIBLE
    )
    return PolicyCompatibilityAssessment(status=status, compatible=True)


def _extract_branch(
    text: str, outcome: ResolutionOutcome
) -> ResolutionBranchEvidence | None:
    patterns = _YES_PATTERNS if outcome == ResolutionOutcome.YES else _NO_PATTERNS
    for index, pattern in enumerate(patterns):
        for match in pattern.finditer(text):
            condition = "otherwise" if outcome == ResolutionOutcome.NO and index == 0 else (
                match.groupdict().get("condition") or ""
            )
            # Cancellation outcomes are exception evidence, not the ordinary binary branch.
            if _CANCELLATION_MARKER.search(condition):
                continue
            return ResolutionBranchEvidence(
                outcome=outcome,
                condition=_normalize_condition(condition),
                evidence_text=_evidence_snippet(text, match.start(), match.end()),
            )
    return None


def _extract_cancellation(text: str) -> CancellationEvidence | None:
    marker = _CANCELLATION_MARKER.search(text)
    if not marker:
        return None
    clause = _evidence_snippet(text, marker.start(), marker.end(), radius=360)
    normalized = _normalize_text(clause)

    if "fair market price" in normalized or "fair price" in normalized:
        disposition = CancellationDisposition.FAIR_PRICE
        guaranteed = False
    elif "sole discretion" in normalized or "exchange discretion" in normalized:
        disposition = CancellationDisposition.EXCHANGE_DISCRETION
        guaranteed = False
    elif re.search(r"(?:\$?0[.]50|half (?:payout|value)|50 cents)", normalized):
        disposition = CancellationDisposition.HALF_PAYOUT
        guaranteed = True
    elif re.search(r"(?:refund(?:ed)?|return(?:ed)?).{0,80}(?:stake|position|fund)", normalized) or re.search(r"(?:stake|position|fund).{0,80}(?:refund(?:ed)?|return(?:ed)?)", normalized):
        disposition = CancellationDisposition.VOID_REFUND
        guaranteed = True
    elif re.search(r"(?:resolve|settle)(?:s|d)?(?: to)? no\b", normalized):
        disposition = CancellationDisposition.NO
        guaranteed = True
    elif re.search(r"(?:resolve|settle)(?:s|d)?(?: to)? yes\b", normalized):
        disposition = CancellationDisposition.YES
        guaranteed = True
    elif re.search(r"(?:standard|ordinary|original) (?:settlement )?rules? (?:still )?apply", normalized):
        disposition = CancellationDisposition.STANDARD_RULES
        guaranteed = True
    else:
        disposition = CancellationDisposition.UNPARSED
        guaranteed = False
    return CancellationEvidence(
        disposition=disposition,
        guaranteed=guaranteed,
        evidence_text=clause,
    )


def _extract_revision(text: str) -> RevisionEvidence | None:
    normalized = _normalize_text(text)
    matches: list[RevisionPolicy] = []
    snippets: list[str] = []

    first_release = re.search(
        r"(?:subsequent|later) revisions?.{0,80}(?:not|will not).{0,40}(?:considered|used)"
        r"|first (?:officially )?(?:published|released) (?:figure|value|report|release)",
        normalized,
    )
    if first_release:
        matches.append(RevisionPolicy.FIRST_OFFICIAL_RELEASE)
        snippets.append(_evidence_snippet(text, first_release.start(), first_release.end()))

    latest_final = re.search(
        r"latest (?:available )?(?:version|final (?:version|report|value))"
        r"|official and final value.{0,100}latest version"
        r"|latest version.{0,100}official and final value",
        normalized,
    )
    if latest_final:
        matches.append(RevisionPolicy.LATEST_FINAL_REPORT)
        snippets.append(_evidence_snippet(text, latest_final.start(), latest_final.end()))
    else:
        latest_revision = re.search(
            r"(?:subsequent|later) revisions?.{0,80}(?:will be|are) (?:considered|used)"
            r"|most recent (?:revised|available) (?:figure|value|report)",
            normalized,
        )
        if latest_revision:
            matches.append(RevisionPolicy.LATEST_AVAILABLE_REVISION)
            snippets.append(
                _evidence_snippet(text, latest_revision.start(), latest_revision.end())
            )
        else:
            final_value = re.search(
                r"(?:official )?final (?:published )?(?:figure|value|report)", normalized
            )
            if final_value:
                matches.append(RevisionPolicy.FINAL_PUBLISHED_VALUE)
                snippets.append(_evidence_snippet(text, final_value.start(), final_value.end()))

    unique = list(dict.fromkeys(matches))
    if not unique:
        return None
    policy = unique[0] if len(unique) == 1 else RevisionPolicy.CONFLICTING
    return RevisionEvidence(
        policy=policy,
        matched_policies=unique,
        evidence_text=" ".join(dict.fromkeys(snippets)),
    )


def _extract_source(
    text: str, explicit_source: str | None
) -> tuple[AuthoritativeSourceEvidence | None, bool]:
    candidates: list[AuthoritativeSourceEvidence] = []
    text_source = _source_from_value(text, EvidenceOrigin.RULE_TEXT)
    if text_source:
        candidates.append(text_source)
    if explicit_source and _normalize_text(explicit_source) not in {"", "unknown", "n/a"}:
        explicit = _source_from_value(explicit_source, EvidenceOrigin.EXPLICIT_SOURCE_FIELD)
        if explicit:
            candidates.append(explicit)

    if not candidates:
        return None, False
    canonical_names = {candidate.canonical_name for candidate in candidates}
    return candidates[0], len(canonical_names) > 1


def _source_from_value(
    value: str, origin: EvidenceOrigin
) -> AuthoritativeSourceEvidence | None:
    normalized = _normalize_text(value)
    known_sources = (
        (
            "nws_climatological_report_daily",
            (
                r"(?:national weather service|\bnws\b).{0,100}"
                r"(?:climatological report ?(?:daily)?|daily climatological report|daily climate report)"
            ),
        ),
        ("national_weather_service", r"(?:national weather service|\bnws\b)"),
        (
            "us_bls_cpi",
            (
                r"(?:bureau of labor statistics|\bbls\b).{0,80}"
                r"(?:consumer price index|\bcpi\b)"
            ),
        ),
        ("us_bureau_of_labor_statistics", r"(?:bureau of labor statistics|\bbls\b)"),
        ("federal_reserve", r"(?:federal reserve|federal open market committee|\bfomc\b)"),
        ("the_weather_company", r"the weather company"),
    )
    for canonical, pattern in known_sources:
        match = re.search(pattern, normalized)
        if match:
            return AuthoritativeSourceEvidence(
                canonical_name=canonical,
                evidence_text=_evidence_snippet(value, match.start(), match.end()),
                origin=origin,
            )

    for pattern in (
        r"(?:resolution|authoritative) source(?: is|:)?\s+(?P<source>[^.;]+)",
        r"(?:reported|published|provided) by\s+(?P<source>[^.;]+)",
        r"according to\s+(?P<source>[^.;]+)",
    ):
        match = re.search(pattern, normalized)
        if match:
            source = _normalize_source_name(match.group("source"))
            if source:
                return AuthoritativeSourceEvidence(
                    canonical_name=source,
                    evidence_text=_evidence_snippet(value, match.start(), match.end()),
                    origin=origin,
                )
    if origin == EvidenceOrigin.EXPLICIT_SOURCE_FIELD:
        source = _normalize_source_name(value)
        if source:
            return AuthoritativeSourceEvidence(
                canonical_name=source,
                evidence_text=value.strip(),
                origin=origin,
            )
    return None


def _branch_signature(branch: ResolutionBranchEvidence | None) -> tuple[str, str] | None:
    return (branch.outcome.value, branch.condition) if branch else None


def _normalize_condition(value: str) -> str:
    normalized = _normalize_text(value)
    normalized = re.sub(
        r"\b(?:as )?(?:reported|published|provided) by (?:the )?"
        r"(?:national weather service's? climatological report(?:\s*\(daily\)|\s+daily)?"
        r"|nws climatological report daily|the weather company)"
        r"(?=\s+is\b|[,.;]|$)",
        "as reported by authoritative_source",
        normalized,
    )
    normalized = re.sub(r"\bdegrees? fahrenheit\b|°\s*f\b", "f", normalized)
    normalized = re.sub(r"\b(?:the )?(?:above )?(?:condition|criteria) (?:is|are) not met\b", "otherwise", normalized)
    return normalized.strip(" ,:-")


def _normalize_source_name(value: str) -> str:
    normalized = _normalize_text(value)
    normalized = re.sub(r"^(?:the|official)\s+", "", normalized)
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return normalized


def _normalize_text(value: str) -> str:
    value = value.casefold().replace("’", "'").replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _evidence_snippet(text: str, start: int, end: int, radius: int = 220) -> str:
    left_boundary = max(
        text.rfind(".", max(0, start - radius), start),
        text.rfind(";", max(0, start - radius), start),
        text.rfind("\n", max(0, start - radius), start),
    )
    left = left_boundary + 1 if left_boundary >= 0 else max(0, start - radius)
    right_candidates = [
        index
        for delimiter in (".", ";", "\n")
        if (index := text.find(delimiter, end, min(len(text), end + radius))) >= 0
    ]
    right = min(right_candidates) + 1 if right_candidates else min(len(text), end + radius)
    return text[left:right].strip()
