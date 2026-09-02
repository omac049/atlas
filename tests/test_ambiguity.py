"""Predicate Ambiguity Score: the instrument the third charter freezes.

These tests protect the INSTRUMENT, not the theory. They pin the direction of
the scale, the charter-fixed threshold, the read-only boundary, and the one
distinction the whole hypothesis rests on: a judgment-laden term is a signal
only when the rules never define it.
"""

from datetime import UTC, datetime

from atlas.ambiguity import (
    AMBIGUITY_FLAG_THRESHOLD,
    SCORING_VERSION,
    ambiguity_score,
)
from atlas.venues.fixtures import fixture_markets

SCORED_AT = datetime(2026, 9, 2, tzinfo=UTC)

# Quoted from the development-set disputes' own published text.
ZELENSKY_SUIT = (
    'This market will resolve to "Yes" if Volodymyr Zelenskyy is photographed '
    "or videotaped wearing a suit between May 22 and June 30, 2025 ET. "
    'Otherwise, this market will resolve to "No". For this market, a '
    "consensus of credible reporting will suffice."
)
CPI_ANCHORED = (
    "This market will settle to Yes if U.S. inflation, before seasonal "
    "adjustment, over the 12-month period ending in August 2026 (CPI YoY) is "
    "above 3.0%. Outcome sourced from the Bureau of Labor Statistics."
)


def _market(rules: str, venue_key: str = "polymarket_us"):
    market = fixture_markets()[venue_key][0].model_copy(deep=True)
    market.raw_rules_text = rules
    market.resolution_text = rules
    market.description = None
    market.title = "irrelevant to the score"
    market.raw_market_json = {}
    return market


def test_the_scale_runs_opposite_to_clarity_so_the_two_cannot_be_confused():
    """Clarity: higher is better. Ambiguity: higher is WORSE. A report that
    mixed them up would invert its own conclusion."""
    vague = ambiguity_score(_market(ZELENSKY_SUIT), scored_at=SCORED_AT)
    anchored = ambiguity_score(_market(CPI_ANCHORED), scored_at=SCORED_AT)
    assert vague["score"] > anchored["score"]
    assert vague["flagged"] is True
    assert anchored["flagged"] is False


def test_a_judgment_laden_term_fires_only_when_the_rules_leave_it_undefined():
    """The distinction the entire hypothesis rests on. 'Suit' is not itself a
    defect; 'suit' with nobody saying what counts as one is."""
    undefined = ambiguity_score(_market(ZELENSKY_SUIT), scored_at=SCORED_AT)
    codes = {feature["code"] for feature in undefined["features"]}
    assert "OPERATIVE_TERM_UNDEFINED" in codes

    defined = ambiguity_score(
        _market(
            ZELENSKY_SUIT
            + ' For the purposes of this market, "suit" means a matching '
            "jacket and trousers with a collared shirt and necktie."
        ),
        scored_at=SCORED_AT,
    )
    defined_codes = {feature["code"] for feature in defined["features"]}
    assert "OPERATIVE_TERM_UNDEFINED" not in defined_codes
    assert "DEFINITION_CLAUSE_PRESENT" in defined_codes
    assert defined["score"] < undefined["score"]


def test_every_fired_feature_carries_the_text_that_triggered_it():
    """A reader must be able to check the machine against the contract without
    rerunning anything."""
    scored = ambiguity_score(_market(ZELENSKY_SUIT), scored_at=SCORED_AT)
    assert scored["features"]
    for feature in scored["features"]:
        assert feature["prose"]
        assert feature["evidence"], feature["code"]


def test_a_market_with_no_resolution_text_is_unmeasurable_not_unambiguous():
    """Scoring silence as zero would read as 'perfectly clear'."""
    scored = ambiguity_score(_market(""), scored_at=SCORED_AT)
    assert scored["score"] is None
    assert scored["flagged"] is False
    assert scored["unmeasurable"] == "NO_RESOLUTION_TEXT"


def test_the_title_never_moves_the_score():
    """Titles are marketing; rules settle the contract. A title-driven score
    would drift toward measuring headline style."""
    base = _market(CPI_ANCHORED)
    loud = _market(CPI_ANCHORED)
    loud.title = "Will inflation DEFINITIVELY and substantially explode?!"
    assert (
        ambiguity_score(base, scored_at=SCORED_AT)["score"]
        == ambiguity_score(loud, scored_at=SCORED_AT)["score"]
    )


def test_scoring_is_deterministic_and_versioned():
    market = _market(ZELENSKY_SUIT)
    first = ambiguity_score(market, scored_at=SCORED_AT)
    second = ambiguity_score(market, scored_at=SCORED_AT)
    assert first == second
    assert first["scoring_version"] == SCORING_VERSION
    # Charter-fixed (§3): changing this is changing the instrument.
    assert AMBIGUITY_FLAG_THRESHOLD == 50


def test_no_single_feature_can_flag_a_market_on_its_own():
    """Bounded weights: the flag needs corroboration, so one regex false
    positive cannot manufacture a finding."""
    from atlas.ambiguity import _AMBIGUITY_WEIGHTS

    assert max(_AMBIGUITY_WEIGHTS.values()) < AMBIGUITY_FLAG_THRESHOLD


def test_the_approval_pipeline_never_imports_the_ambiguity_score():
    import importlib
    import sys

    for name in list(sys.modules):
        if name.startswith("atlas.ambiguity"):
            del sys.modules[name]
    for module in ("atlas.normalization", "atlas.settlement", "atlas.verification"):
        importlib.import_module(module)

    assert not any(name.startswith("atlas.ambiguity") for name in sys.modules)
