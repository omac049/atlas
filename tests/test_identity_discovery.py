from decimal import Decimal

from atlas.discovery import (
    _identity_discovery,
    _structured_identity_key,
    structured_identity_candidates,
)
from atlas.models import (
    ContractFingerprint,
    ContractPair,
    EquivalenceDecision,
    Market,
    MatchStatus,
    VenueName,
)


def _fingerprint(subject: str, geography: str = "us") -> ContractFingerprint:
    return ContractFingerprint(
        event_subject=subject,
        event_action="published_value",
        event_date="2026-08",
        market_type="economic",
        contract_scope="unemployment_rate",
        affirmative_outcome="predicate_true",
        threshold=Decimal("4.5"),
        threshold_operator=">",
        threshold_unit="percent",
        measurement_period="2026-08",
        geography=geography,
        resolution_source="us_bls",
    )


def _market(market_id: str, venue: VenueName, title: str) -> Market:
    return Market(
        market_id=market_id,
        venue=venue,
        venue_market_id=market_id,
        title=title,
        resolution_source="US BLS",
        resolution_text="Explicit Yes and No resolution.",
        event_subject=title,
        event_action="published_value",
    )


def test_structured_identity_requires_complete_anchor_and_separates_geography():
    assert _structured_identity_key(_fingerprint("alias-a")) is not None
    assert _structured_identity_key(_fingerprint("alias-a", "unknown")) is None
    assert _structured_identity_key(_fingerprint("alias-a", "us")) != (
        _structured_identity_key(_fingerprint("alias-a", "ca"))
    )


def test_identity_report_counts_novel_aliases_without_approving_them():
    left_market = _market("k-1", VenueName.KALSHI, "US jobless rate")
    right_market = _market("p-1", VenueName.POLYMARKET_US, "American unemployment")
    report = _identity_discovery(
        [(left_market, _fingerprint("us_jobless_rate|2026-08"))],
        [(right_market, _fingerprint("us_unemployment_rate|2026-08"))],
    )
    assert report["structured_shared_keys"] == 1
    assert report["novel_alias_candidates"] == 1
    assert report["status"] == "NOVEL_ALIASES_REQUIRE_RULE_REVIEW"


def test_structured_alias_candidate_retains_verifier_mismatches(monkeypatch):
    left_market = _market("k-1", VenueName.KALSHI, "US jobless rate")
    right_market = _market("p-1", VenueName.POLYMARKET_US, "American unemployment")
    left_fingerprint = _fingerprint("us_jobless_rate|2026-08")
    right_fingerprint = _fingerprint("us_unemployment_rate|2026-08")
    right_fingerprint.resolution_source = "named_sources:commerce_department"

    fingerprints = {
        left_market.market_id: left_fingerprint,
        right_market.market_id: right_fingerprint,
    }
    monkeypatch.setattr(
        "atlas.discovery.build_fingerprint",
        lambda market: fingerprints[market.market_id],
    )

    def fake_verify(left, right, pair_id):
        decision = EquivalenceDecision(
            status=MatchStatus.REVIEW_REQUIRED,
            mismatch_codes=["EVENT_SUBJECT_MISMATCH", "RESOLUTION_SOURCE_MISMATCH"],
            fingerprint_a=left_fingerprint,
            fingerprint_b=right_fingerprint,
        )
        return ContractPair(
            pair_id=pair_id,
            market_a=left,
            market_b=right,
            status=MatchStatus.REVIEW_REQUIRED,
            match_confidence=Decimal(0),
            differences=decision.mismatch_codes,
            decision=decision,
        )

    monkeypatch.setattr("atlas.discovery.verify_equivalence", fake_verify)
    candidates = structured_identity_candidates([left_market], [right_market])
    assert len(candidates) == 1
    assert candidates[0]["status"] == "REVIEW_REQUIRED"
    assert candidates[0]["review_kind"] == "STRUCTURED_IDENTITY_REVIEW"
    assert candidates[0]["mismatch_codes"] == [
        "EVENT_SUBJECT_MISMATCH",
        "RESOLUTION_SOURCE_MISMATCH",
    ]
