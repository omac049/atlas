"""Contract Divergence Report: assembly, honesty framing, and rendering.

The report is the contract-intelligence deliverable, so what these tests pin is
its honesty surface: candidates never presented as proven, untradeable venues
never presented as opportunity, absent measurements never presented as findings.
"""

from datetime import UTC, datetime

from atlas.intel import divergence_report, render_divergence_markdown
from atlas.storage import AtlasStore

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _observation(
    pair: str,
    gap: str,
    *,
    venue: str = "polymarket_global",
    tradeable: bool = False,
    asymmetric: bool = False,
    observed_at: str = "2026-08-20T10:00:00+00:00",
    meets_floors: bool = False,
) -> dict:
    observation = {
        "observation_id": f"{pair}-{observed_at}",
        "observed_at": observed_at,
        "event_subject": f"us_test_family|{pair}",
        "kalshi_market_id": f"kalshi:{pair}",
        "polymarket_market_id": f"{venue}:{pair}",
        "kalshi_title": f"Kalshi {pair}",
        "polymarket_title": f"Polymarket {pair}",
        "verification_status": "REVIEW_REQUIRED",
        "mismatch_codes": ["SETTLEMENT_POLICY_MISMATCH"],
        "baskets": [],
        "best_gap": gap,
        "best_basket": "kalshi_yes+polymarket_no",
        "executable_gap": float(gap) > 0,
        "meets_tick_floor": meets_floors,
        "meets_size_floor": meets_floors,
        "polymarket_venue": venue,
        "tradeable_venue_pair": tradeable,
        "polymarket_fill_assumed_at_quote": True,
    }
    if asymmetric:
        observation["settlement_timing"] = {
            "asymmetric": True,
            "codes": ["SETTLEMENT_TIMING_ASYMMETRIC"],
            "early_venue": "kalshi",
            "early_codes": ["EARLY_MEDIA_CONSENSUS"],
            "days_to_settlement": "164.8",
            "horizon_basis": "kalshi_resolution_time",
        }
    return observation


async def _store_with(tmp_path, observations) -> AtlasStore:
    store = AtlasStore(str(tmp_path / "atlas.sqlite3"))
    for observation in observations:
        await store.save_gap_observation(observation)
    return store


async def test_report_assembles_all_sections_and_stays_paper_only(tmp_path):
    store = await _store_with(
        tmp_path,
        [
            _observation("p1", "0.02", asymmetric=True),
            _observation("p2", "-0.03", venue="polymarket_us", tradeable=True),
        ],
    )
    report = await divergence_report(store, now=NOW)
    assert report["paper_only"] is True
    assert report["report_kind"] == "CONTRACT_DIVERGENCE_REPORT"
    headline = report["headline"]
    assert headline["settlement_timing_asymmetric_pairs"] == 1
    assert headline["price_disagreement_pairs"] == 2
    # Caveats are part of the payload, not just the rendering: a JSON consumer
    # gets the same honesty framing a markdown reader does.
    assert any("CANDIDATE" in caveat for caveat in report["method"]["caveats"])


async def test_disagreements_keep_only_the_latest_reading_per_pair(tmp_path):
    store = await _store_with(
        tmp_path,
        [
            _observation("p1", "0.09", observed_at="2026-08-19T10:00:00+00:00"),
            _observation("p1", "0.01", observed_at="2026-08-20T10:00:00+00:00"),
        ],
    )
    report = await divergence_report(store, now=NOW)
    rows = report["price_disagreements"]
    assert len(rows) == 1
    # The stale 9-cent reading must not survive as the pair's current state.
    assert rows[0]["best_gap"] == "0.01"


async def test_asymmetry_section_is_empty_when_nothing_was_measured(tmp_path):
    """No annotation means not-measured — the section must not invent rows."""
    store = await _store_with(tmp_path, [_observation("p1", "0.02")])
    report = await divergence_report(store, now=NOW)
    assert report["settlement_timing_asymmetries"] == []
    assert report["headline"]["settlement_timing_asymmetric_pairs"] == 0


async def test_markdown_marks_untradeable_venues_and_floor_failures(tmp_path):
    store = await _store_with(
        tmp_path,
        [
            _observation("global-pair", "0.05"),
            _observation(
                "us-pair", "0.02", venue="polymarket_us", tradeable=True, meets_floors=True
            ),
        ],
    )
    report = await divergence_report(store, now=NOW)
    markdown = render_divergence_markdown(report)
    assert "# Contract Divergence Report" in markdown
    assert "paper-only" in markdown
    # The disagreement table must carry the tradeable and floor columns so an
    # offshore 5-cent gap can never read as a takeable one.
    disagreement_rows = [
        line for line in markdown.splitlines() if line.startswith("| Kalshi ")
    ]
    assert any("| no | no |" in row for row in disagreement_rows)  # global pair
    assert any("| yes | yes |" in row for row in disagreement_rows)  # US pair
    # Negative-gap pairs are agreement, not disagreement — never rendered.
    assert "Research signal, not opportunity" in markdown


async def test_markdown_renders_without_any_observations(tmp_path):
    """A fresh database yields a headline-only report, not a crash."""
    store = await _store_with(tmp_path, [])
    report = await divergence_report(store, now=NOW)
    markdown = render_divergence_markdown(report)
    assert "## At a glance" in markdown
    assert "## Method and limits" in markdown
