"""Settlement Clarity Score: the grade, its honesty, and its one-way dependency.

The score is a sellable claim about someone else's fine print, so what these
tests pin is everything that could turn it into an overclaim: a grade that moves
between runs, a discretionary clause that scores well, a finding with no plain
English behind it, a blocker code the frozen assessors emit that this grader
would silently ignore, and — most important — any path by which a grade could
leak back into the verification pipeline it reads from.
"""

import ast
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from atlas.clarity import (
    _CANONICAL_CODES,
    _DEDUCTIONS,
    _FINDING_FIX,
    _FINDING_PROSE,
    DISCRETIONARY_FAIR_PRICE_SETTLEMENT,
    GRADES,
    NO_RULES_TEXT,
    _band,
    clarity_scan_report,
    clarity_score,
)
from atlas.models import VenueName
from atlas.venues.fixtures import fixture_markets

YES = "This market resolves Yes if the Acme Prize is awarded on or before December 31, 2026."
NO = "Otherwise, the market resolves No."
CANCEL = "If the award ceremony is canceled, the market resolves No."
REVISION = "Subsequent revisions to the announcement will not be considered."
SOURCE = "The resolution source is the Acme Foundation."
FAIR_PRICE = "The exchange may settle at the last fair market price if the contract is delisted."
EARLY = "This market may be determined early based on a consensus of media calls."

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def _market(*sentences: str, source: str = "Acme Foundation", venue: str = "kalshi"):
    """A market outside every specialized contract family.

    The families (CPI, FOMC, weather, ...) have their own complete-policy paths
    in the frozen assessor; a neutral subject keeps these tests about the
    grader's own arithmetic rather than about family classification.
    """
    key = VenueName.KALSHI if venue == "kalshi" else VenueName.POLYMARKET_US
    market = fixture_markets()[key][0]
    market.title = "Will the Acme Prize be awarded by December 31, 2026?"
    market.subtitle = None
    market.description = None
    market.resolution_text = ""
    market.raw_rules_text = " ".join(sentences)
    market.resolution_source = source
    market.event_subject = "acme prize"
    market.event_action = "is awarded"
    market.market_type = None
    market.threshold = None
    market.threshold_operator = None
    market.threshold_unit = None
    market.revision_policy = None
    market.category = "awards"
    return market


def _codes(grade: dict) -> list[str]:
    return [finding["code"] for finding in grade["findings"]]


def test_same_market_grades_identically_every_time():
    """A published grade a customer can re-derive is the whole product claim."""
    market = _market(YES, NO, CANCEL, SOURCE)
    first = clarity_score(market, graded_at=NOW)
    second = clarity_score(_market(YES, NO, CANCEL, SOURCE), graded_at=NOW)
    assert first == second
    # Without a supplied stamp only the timestamp may differ; nothing else can.
    unstamped = clarity_score(market)
    assert {k: v for k, v in unstamped.items() if k != "graded_at"} == {
        k: v for k, v in first.items() if k != "graded_at"
    }


def test_market_with_no_published_text_is_ungradeable_not_perfect():
    """The dangerous failure mode: no text parsed as nothing wrong."""
    market = _market("", source="unknown")
    market.raw_rules_text = ""
    grade = clarity_score(market, graded_at=NOW)
    assert grade["grade"] == "F"
    assert grade["score"] == 0
    # The single finding names the absence; the blockers it causes are not
    # separate defects and would only pad the report.
    assert _codes(grade) == [NO_RULES_TEXT]


def test_discretionary_fair_price_clause_caps_the_grade_at_d():
    """Discretion is the opposite of clarity: complete prose cannot buy it off."""
    complete = clarity_score(_market(YES, NO, CANCEL, REVISION, SOURCE), graded_at=NOW)
    assert complete == {**complete, "grade": "A", "score": 100, "findings": []}

    discretionary = clarity_score(
        _market(YES, NO, CANCEL, REVISION, SOURCE, FAIR_PRICE), graded_at=NOW
    )
    assert discretionary["score"] == 100
    assert discretionary["grade"] == "D"
    assert discretionary["guarantee_status"] == "NON_GUARANTEED"
    # The cap is a ceiling, not a deduction, so the finding carries zero points
    # while the score keeps reporting how complete the rest of the text is.
    assert _codes(discretionary) == [DISCRETIONARY_FAIR_PRICE_SETTLEMENT]
    assert discretionary["findings"][0]["points"] == 0


def test_each_deduction_fires_once_and_the_points_sum_to_the_score():
    """One missing branch, one deduction — never double-charged, never free."""
    missing_revision = clarity_score(_market(YES, NO, CANCEL, SOURCE), graded_at=NOW)
    assert _codes(missing_revision) == ["MISSING_REVISION_POLICY"]
    assert missing_revision["score"] == 85
    assert missing_revision["grade"] == "B"

    missing_both = clarity_score(_market(YES, NO, SOURCE), graded_at=NOW)
    assert _codes(missing_both) == ["MISSING_CANCELLATION_POLICY", "MISSING_REVISION_POLICY"]
    assert missing_both["score"] == 65
    assert missing_both["grade"] == "C"

    for grade in (missing_revision, missing_both):
        deducted = sum(finding["points"] for finding in grade["findings"])
        assert grade["score"] == 100 - deducted
        assert len(_codes(grade)) == len(set(_codes(grade)))


def test_the_same_defect_under_two_code_names_is_charged_once():
    """The frozen assessors name an unnamed source twice; that is one defect.

    `assess_settlement_guarantee` emits MISSING_RESOLUTION_SOURCE and the policy
    evidence parser emits MISSING_AUTHORITATIVE_SOURCE for the identical gap. A
    contract must not lose 40 points because two modules noticed it.
    """
    grade = clarity_score(_market("Something happens eventually.", source="unknown"))
    codes = _codes(grade)
    assert codes.count("MISSING_AUTHORITATIVE_SOURCE") == 1
    assert "MISSING_RESOLUTION_SOURCE" not in codes
    # One unparseable clause is ONE deduction: the branch codes that restate it
    # are superseded (shown, not charged), so this text no longer floors to 0.
    superseded = {entry["code"] for entry in grade["superseded"]}
    assert "MISSING_AFFIRMATIVE_BRANCH" in superseded
    assert "MISSING_NEGATIVE_BRANCH" in superseded
    assert "UNPARSED_SETTLEMENT_POLICY" in _codes(grade)
    assert grade["grade"] == "F"


def test_every_emittable_finding_carries_prose_and_a_venue_side_fix():
    """A deduction a reader cannot act on is a number, not intelligence."""
    emittable = set(_DEDUCTIONS) | {NO_RULES_TEXT, DISCRETIONARY_FAIR_PRICE_SETTLEMENT}
    assert emittable == set(_FINDING_PROSE) == set(_FINDING_FIX)
    for code in emittable:
        assert _FINDING_PROSE[code].strip(), code
        # The fix is always something the VENUE publishes; no code change here
        # can make an unpublished branch exist.
        assert _FINDING_FIX[code].startswith(("publish", "name", "state", "replace")), code


def test_grade_bands_sit_exactly_on_their_published_boundaries():
    assert [_band(score) for score in (100, 90, 89, 75, 74, 60, 59, 40, 39, 0)] == [
        "A", "A", "B", "B", "C", "C", "D", "D", "F", "F",
    ]


def test_early_determination_clauses_are_disclosed_but_never_scored():
    """Timing and clarity are different products; mixing them would mislead."""
    plain = clarity_score(_market(YES, NO, CANCEL, REVISION, SOURCE), graded_at=NOW)
    flagged = clarity_score(_market(YES, NO, CANCEL, REVISION, SOURCE, EARLY), graded_at=NOW)
    assert flagged["flags"] == [
        "EARLY_DETERMINATION_CLAUSE",
        "EARLY_MEDIA_CONSENSUS",
        "EARLY_MEDIA_PROJECTION",
    ]
    assert plain["flags"] == []
    assert flagged["score"] == plain["score"]
    assert not [code for code in _codes(flagged) if code.startswith("EARLY_")]


def test_scan_report_aggregates_per_venue_and_never_hides_a_missing_venue():
    markets = [
        _market(YES, NO, CANCEL, REVISION, SOURCE),
        _market(YES, NO, SOURCE, venue="polymarket_us"),
    ]
    report = clarity_scan_report(
        markets,
        generated_at=NOW,
        degraded_venues=["polymarket_global"],
        scope={"truncated_venues": ["kalshi"], "max_markets_per_venue": 1},
    )
    aggregates = report["aggregates"]
    assert report["paper_only"] is True
    assert aggregates["markets_graded"] == 2
    assert aggregates["per_venue"]["kalshi"]["grade_distribution"]["A"] == 1
    # Every band is present even at zero: absent must read as "none graded
    # there", never as "not measured".
    assert set(aggregates["per_venue"]["kalshi"]["grade_distribution"]) == set(GRADES)
    assert aggregates["per_venue"]["polymarket_us"]["mean_score"] == "65.0"
    assert aggregates["mean_score_per_category"]["kalshi"]["awards"] == "100.0"
    # Worst-first, and the compact row carries only finding CODES.
    assert aggregates["worst"][0]["venue"] == "polymarket_us"
    assert aggregates["worst"][0]["findings"] == [
        "MISSING_CANCELLATION_POLICY",
        "MISSING_REVISION_POLICY",
    ]
    assert report["degraded_venues"] == ["polymarket_global"]
    assert report["scope"]["truncated_venues"] == ["kalshi"]
    # The scan reads Kalshi's series-level sources as evidence; the limit the
    # artifact must now carry is the failure direction — a fetch outage leaves
    # findings standing, so grades can only get stricter, never kinder.
    assert any("stricter, never kinder" in limit for limit in report["limits"])


def test_worst_list_shows_distinct_wordings_not_one_ladder_ten_times():
    """A venue's strike ladder repeats one rules text; ten copies is padding."""
    ladder = []
    for index in range(4):
        market = _market(YES, SOURCE)
        market.market_id = f"kalshi:LADDER-{index}"
        ladder.append(market)
    distinct = _market(YES, NO, SOURCE)
    distinct.market_id = "kalshi:OTHER-1"
    distinct.title = "Will a different thing happen?"
    report = clarity_scan_report([*ladder, distinct], generated_at=NOW)
    worst = report["aggregates"]["worst"]
    assert [row["title"] for row in worst] == [
        "Will the Acme Prize be awarded by December 31, 2026?",
        "Will a different thing happen?",
    ]
    # The scale of the repetition survives the dedup rather than disappearing.
    assert worst[0]["contracts_with_this_title"] == 4
    assert worst[1]["contracts_with_this_title"] == 1
    # Every graded contract still appears in the per-market rows.
    assert len(report["markets"]) == 5


async def test_scan_cli_writes_the_dated_artifact_in_fixture_mode(tmp_path, monkeypatch):
    """The artifact is what the divergence report and the future lookup read."""
    from atlas.cli import clarity_scan

    monkeypatch.chdir(tmp_path)
    await clarity_scan(live=False)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    target = tmp_path / "data" / "clarity" / f"clarity-scan-{stamp}.json"
    assert target.exists()
    report = json.loads(target.read_text())
    assert report["scan_kind"] == "SETTLEMENT_CLARITY_SCAN"
    assert report["scope"]["live"] is False
    assert set(report["aggregates"]["per_venue"]) == {"kalshi", "polymarket_us"}
    assert report["markets"]


async def test_scan_survives_a_venue_whose_catalog_fetch_fails(tmp_path, monkeypatch, capsys):
    """A venue outage must degrade the scan, never crash it or vanish silently."""
    from atlas.cli import clarity_scan
    from atlas.venues.kalshi import KalshiVenue

    async def refuse(self, *args, **kwargs):
        raise TimeoutError("catalog unreachable")

    monkeypatch.setattr(KalshiVenue, "list_markets", refuse)
    monkeypatch.chdir(tmp_path)
    await clarity_scan(live=False)
    printed = capsys.readouterr().out
    assert "clarity_scan_fetch_failed=kalshi" in printed
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    report = json.loads(
        (tmp_path / "data" / "clarity" / f"clarity-scan-{stamp}.json").read_text()
    )
    assert report["degraded_venues"] == ["kalshi"]
    assert set(report["aggregates"]["per_venue"]) == {"polymarket_us"}


def test_grader_explains_every_blocker_code_the_frozen_assessors_can_emit():
    """A new venue blocker must not slip through as a silent zero-point finding.

    The grader ignores codes it does not recognize on purpose — inventing a
    penalty for an unknown code would be an overclaim — so the safety net is
    here: every code the frozen settlement assessor and the policy-evidence
    parser can actually emit must be either a guarantee-POSITIVE code or a
    finding this module can price and explain.
    """
    settlement_source = Path("atlas/settlement.py").read_text()
    evidence_source = Path("atlas/policy_evidence.py").read_text()
    emitted = set()
    for block in re.findall(r'"reason_codes": \[([^\]]*)\]', settlement_source):
        emitted.update(re.findall(r'"([A-Z_]+)"', block))
    for block in re.findall(r"reasons = \[([^\]]*)\]", settlement_source):
        emitted.update(re.findall(r'"([A-Z_]+)"', block))
    emitted.update(re.findall(r'reasons\.append\("([A-Z_]+)"\)', settlement_source))
    emitted.update(re.findall(r'blockers\.append\("([A-Z_]+)"\)', evidence_source))
    # Sanity: the scan above must actually find the codes, or it proves nothing.
    assert {"FAMILY_POLICY_INCOMPLETE", "MISSING_CANCELLATION_POLICY"} <= emitted

    for code in emitted:
        if code.startswith(("COMPLETE_", "EXPLICIT_")):
            continue  # guarantee-positive reasons, never a clarity defect
        canonical = _CANONICAL_CODES.get(code, code)
        assert canonical in _FINDING_PROSE, code
        assert canonical in _DEDUCTIONS or canonical == DISCRETIONARY_FAIR_PRICE_SETTLEMENT, code


def test_the_approval_pipeline_never_imports_the_grader():
    """One-way dependency, pinned two ways.

    The grade reads the frozen modules; if any of them read it back, a marketing
    score could start influencing an approval label — and the 90-day study's
    frozen measurement would silently change underneath it.
    """
    frozen = ("atlas/verification.py", "atlas/settlement.py", "atlas/normalization.py")
    for path in frozen:
        tree = ast.parse(Path(path).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all("clarity" not in alias.name for alias in node.names), path
            if isinstance(node, ast.ImportFrom):
                assert "clarity" not in (node.module or ""), path
    # Transitive too: importing the frozen modules must not drag the grader in
    # through some intermediate module.
    probe = (
        "import sys, atlas.verification, atlas.settlement, atlas.normalization;"
        "print('atlas.clarity' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"



def test_series_level_sources_supersede_the_missing_source_finding():
    """Kalshi names its settlement sources on /series, not the market record.

    Venue-published evidence one endpoint up is still venue-published evidence.
    Passed in by the caller so the scorer stays pure; and the supersession names
    the sources so a reader can check the claim.
    """
    market = _market("If X happens, the market resolves to Yes.", source="unknown")
    without = clarity_score(market)
    assert "MISSING_AUTHORITATIVE_SOURCE" in _codes(without)
    graded = clarity_score(market, series_settlement_sources=["Federal Reserve"])
    assert "MISSING_AUTHORITATIVE_SOURCE" not in _codes(graded)
    entry = next(
        e for e in graded["superseded"] if e["code"] == "MISSING_AUTHORITATIVE_SOURCE"
    )
    assert "Federal Reserve" in entry["reason"]
    assert graded["score"] == without["score"] + 20


def test_an_explicit_yes_branch_cannot_be_charged_as_missing():
    """Hand-checked live 2026-08-24: a platinum contract stating "...then the
    market resolves to Yes" was charged MISSING_AFFIRMATIVE_BRANCH by the
    family parsers, which only know their own families' wording. Clarity
    re-checks the generic structure textually and overrules — visibly."""
    market = _market(
        "If the close price is above 1884.49 USD, then the market resolves to Yes.",
        source="unknown",
    )
    grade = clarity_score(market)
    assert "MISSING_AFFIRMATIVE_BRANCH" not in _codes(grade)
    superseded = {e["code"]: e["reason"] for e in grade["superseded"]}
    if "MISSING_AFFIRMATIVE_BRANCH" in superseded:
        assert "Yes-branch" in superseded["MISSING_AFFIRMATIVE_BRANCH"]


def test_both_branches_textually_present_supersede_the_unparsed_charge():
    market = _market(
        "If X, the market resolves to Yes. Otherwise, the market resolves to No.",
        source="unknown",
    )
    grade = clarity_score(market)
    codes = _codes(grade)
    assert "UNPARSED_SETTLEMENT_POLICY" not in codes
    assert "MISSING_AFFIRMATIVE_BRANCH" not in codes
    assert "MISSING_NEGATIVE_BRANCH" not in codes


def test_supersession_only_ever_removes_charges_never_adds_them():
    """A failed evidence fetch degrades toward the STRICTER grade: passing no
    series sources, or sources for the wrong market, changes nothing."""
    market = _market("Something happens eventually.", source="unknown")
    baseline = clarity_score(market)
    with_empty = clarity_score(market, series_settlement_sources=[])
    assert with_empty["score"] == baseline["score"]
    assert with_empty["findings"] == baseline["findings"]
