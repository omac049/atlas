"""Test A harness: the charter's mechanics, pinned to the signed thresholds.

These tests protect the PROOF, not the theory: control selection must be
deterministic, the pass criteria must match the charter verbatim, and a thin
corpus must read INCONCLUSIVE rather than leaning either way.
"""

from datetime import UTC, datetime, timedelta

from atlas.clarity import clarity_score
from atlas.models import MarketStatus
from atlas.proof import (
    CONTROLS_PER_DISPUTE,
    MIN_CORPUS_SIZE,
    TROUBLE_FINDINGS,
    evaluate_test_a,
    select_controls,
)
from atlas.venues.fixtures import fixture_markets

GRADED_AT = datetime(2026, 9, 1, tzinfo=UTC)


def _market(mid: str, rules: str, close_offset_hours: int = 0, source: str = "unknown"):
    market = fixture_markets()["polymarket_us"][0].model_copy(deep=True)
    market.market_id = mid
    market.venue_market_id = mid
    market.title = mid
    market.raw_rules_text = rules
    market.resolution_text = rules
    market.description = None
    market.resolution_source = source
    market.status = MarketStatus.SETTLED
    market.close_time = datetime(2026, 6, 1, tzinfo=UTC) + timedelta(hours=close_offset_hours)
    market.raw_market_json = {}
    return market


CLEAN_RULES = (
    "If X happens, the market resolves to Yes. Otherwise, the market resolves to No. "
    "If the event is canceled or abandoned, the market resolves to No. "
    "Settlement uses the first official release; later revisions do not change the outcome. "
    "If the deciding number is never released within 3 months, the market resolves to No."
)
VAGUE_RULES = "Something interesting may happen at some point."


def test_control_selection_is_deterministic_and_never_includes_a_disputed_market():
    """Nearest close-times with a market_id tiebreak: fetch order cannot
    reorder equal candidates, and a disputed market can never sit in its own
    or any other dispute's control arm."""
    disputed = _market("d1", VAGUE_RULES, close_offset_hours=0)
    pool = [
        _market("far", CLEAN_RULES, close_offset_hours=100),
        _market("near-b", CLEAN_RULES, close_offset_hours=1),
        _market("near-a", CLEAN_RULES, close_offset_hours=1),
        _market("other-dispute", CLEAN_RULES, close_offset_hours=0),
        _market("mid", CLEAN_RULES, close_offset_hours=10),
    ]
    picked = select_controls(disputed, pool, excluded_ids={"d1", "other-dispute"})
    assert [market.market_id for market in picked] == ["near-a", "near-b", "mid"]
    assert len(picked) == CONTROLS_PER_DISPUTE
    # Same inputs shuffled -> same answer.
    picked_again = select_controls(disputed, list(reversed(pool)), excluded_ids={"d1", "other-dispute"})
    assert [m.market_id for m in picked_again] == [m.market_id for m in picked]


def test_pass_requires_both_criteria_and_an_adequate_corpus():
    """The charter's conjunction: median gap >=10 AND (60% disputed trouble at
    >=2x control). A corpus below 15 gradeable disputes is INCONCLUSIVE even
    with perfect separation — inconclusive is not permission."""
    disputed = [_market(f"d{i}", VAGUE_RULES) for i in range(MIN_CORPUS_SIZE)]
    controls = [_market(f"c{i}", CLEAN_RULES) for i in range(MIN_CORPUS_SIZE * 3)]
    report = evaluate_test_a(disputed, controls, GRADED_AT, corpus_size_documented=MIN_CORPUS_SIZE)
    assert report["adequate_corpus"] is True
    assert report["criteria"]["median_gap_ge_10"] is True
    assert report["outcome"] in {"PASS", "FAIL"}  # decided, not inconclusive

    thin = evaluate_test_a(disputed[:5], controls, GRADED_AT, corpus_size_documented=MIN_CORPUS_SIZE)
    assert thin["outcome"] == "INCONCLUSIVE_CORPUS_TOO_SMALL"


def test_identical_arms_fail_rather_than_pass():
    """No separation, no proof — even though every grade is low."""
    same = [_market(f"d{i}", VAGUE_RULES) for i in range(MIN_CORPUS_SIZE)]
    twins = [_market(f"c{i}", VAGUE_RULES) for i in range(MIN_CORPUS_SIZE)]
    report = evaluate_test_a(same, twins, GRADED_AT, corpus_size_documented=MIN_CORPUS_SIZE)
    assert report["median_score_gap"] == 0
    assert report["outcome"] == "FAIL"


def test_unfetchable_disputes_are_reported_never_silently_dropped():
    disputed = [_market(f"d{i}", VAGUE_RULES) for i in range(MIN_CORPUS_SIZE)]
    controls = [_market(f"c{i}", CLEAN_RULES) for i in range(MIN_CORPUS_SIZE)]
    report = evaluate_test_a(disputed, controls, GRADED_AT, corpus_size_documented=22)
    assert report["corpus_size_documented"] == 22
    assert report["corpus_size_gradeable"] == MIN_CORPUS_SIZE


def test_trouble_class_matches_the_charter_and_is_actually_emittable():
    """Every code in the pre-named trouble class must be one the grader can
    emit — a typo here would silently zero the trouble rate."""
    vague = clarity_score(_market("probe", VAGUE_RULES), graded_at=GRADED_AT)
    emitted = {finding["code"] for finding in vague["findings"]}
    assert emitted & TROUBLE_FINDINGS, emitted
    assert TROUBLE_FINDINGS == {
        "DISCRETIONARY_FAIR_PRICE_SETTLEMENT",
        "NO_EXPLICIT_EXCEPTION_FALLBACK",
        "MISSING_AUTHORITATIVE_SOURCE",
        "CONFLICTING_AUTHORITATIVE_SOURCE",
        "UNPARSED_SETTLEMENT_POLICY",
    }


def test_the_verification_pipeline_never_imports_the_proof_harness():
    import sys

    for name in list(sys.modules):
        if name.startswith("atlas.proof"):
            del sys.modules[name]
    import atlas.normalization
    import atlas.settlement
    import atlas.verification  # noqa: F401

    assert not any(name.startswith("atlas.proof") for name in sys.modules)
