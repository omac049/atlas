"""The demand-test site: guardrails a page cannot ship without.

Protects what the site is allowed to SAY, not how it looks: a pair is "the
same bet" only on the verifier's word, every page carries the disclosure and
the not-advice notice, fees on the calculator page are the venues' formulas
and not a copywriter's memory of them, and the build is deterministic.
"""

from datetime import UTC, datetime
from decimal import Decimal

from atlas.gap_radar import kalshi_taker_fee_per_contract
from atlas.site import (
    DISCLOSURE,
    NOT_ADVICE,
    PairPage,
    build_site,
    render_fees,
    slugify,
    verify_pages,
)

AT = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _obs(status="REVIEW_REQUIRED", codes=None, venue="polymarket_us", kid="kalshi:KXCPIYOY-26AUG-T3.0",
         pid="polymarket_us:cpic-uscpi-august-yoy-2026-09-11-gt3pt0pct", at="2026-09-04T10:00:00+00:00"):
    return {
        "observed_at": at,
        "event_subject": "us_cpi_yoy|2026-08",
        "kalshi_market_id": kid, "kalshi_title": "CPI above 3.0%?",
        "polymarket_market_id": pid, "polymarket_title": "Above 3.0%",
        "verification_status": status,
        "mismatch_codes": codes if codes is not None else ["SETTLEMENT_POLICY_MISMATCH"],
        "polymarket_venue": venue,
        "tradeable_venue_pair": venue == "polymarket_us",
        "best_basket": "kalshi_yes+polymarket_no",
        "baskets": [{"legs": "kalshi_yes+polymarket_no", "cost": "0.99", "kalshi_fee": "0.01",
                     "polymarket_fee": "0.0126", "gap": "-0.0126"}],
        "settlement_timing": {"asymmetric": False, "days_to_settlement": "7.0"},
    }


def test_a_pair_is_called_the_same_bet_only_on_the_verifiers_word():
    """The site can never be more confident than the deterministic verifier."""
    review = PairPage(observation=_obs())
    assert review.same_bet is False
    assert ("SETTLEMENT_POLICY_MISMATCH", "both venues publish settlement policy, but the published texts diverge") in review.reasons
    approved = PairPage(observation=_obs(status="APPROVED_EQUIVALENT", codes=[]))
    assert approved.same_bet is True
    _, pages = build_site([_obs(), _obs(status="APPROVED_EQUIVALENT", codes=[], kid="kalshi:X")],
                          base_url="https://example.test", generated_at=AT)
    review_html = next(html for path, html in pages.items() if "kxcpiyoy" in path)
    assert "Not verified as the same bet" in review_html
    assert "Verified as the same bet" not in review_html


def test_every_html_page_carries_disclosure_notice_and_methodology_link():
    _, pages = build_site([_obs()], base_url="https://example.test", generated_at=AT)
    assert verify_pages(pages) == []
    for path, html in pages.items():
        if path.endswith(".html"):
            assert DISCLOSURE[:40] in html and NOT_ADVICE[:40] in html, path


def test_a_page_missing_the_disclosure_fails_the_build_not_the_style_review():
    _, pages = build_site([_obs()], base_url="https://example.test", generated_at=AT)
    pages["index.html"] = pages["index.html"].replace(DISCLOSURE[:40], "")
    assert verify_pages(pages) == ["index.html: missing affiliate disclosure"]


def test_calculator_page_uses_the_venues_formulas_not_a_remembered_rate():
    from atlas.site import Site

    html = render_fees(Site(base_url="https://example.test", generated_at=AT))
    # The at-a-glance table is computed from atlas.gap_radar's Kalshi formula.
    fifty = kalshi_taker_fee_per_contract(Decimal("0.50"))
    assert f"{fifty * 100:.1f}¢" in html  # 2.0¢ (ceil of 1.75¢ per contract)
    assert "0.06 × price × (1 − price)" in html
    assert "help.kalshi.com" in html and "docs.polymarket.us/fees" in html


def test_latest_observation_per_pair_wins_and_the_build_is_deterministic():
    older = _obs(at="2026-09-03T10:00:00+00:00")
    newer = _obs(at="2026-09-04T10:00:00+00:00")
    newer["kalshi_title"] = "NEWER TITLE"
    site, pages = build_site([older, newer], base_url="https://example.test", generated_at=AT)
    assert len(site.pairs) == 1
    assert "NEWER TITLE" in next(h for p, h in pages.items() if p.startswith("compare/"))
    _, again = build_site([older, newer], base_url="https://example.test", generated_at=AT)
    assert pages == again


def test_global_venue_pairs_are_labelled_not_tradeable():
    _, pages = build_site([_obs(venue="polymarket_global", pid="polymarket_global:123")],
                          base_url="https://example.test", generated_at=AT)
    html = next(h for p, h in pages.items() if p.startswith("compare/"))
    assert "cannot trade" in html
    assert "Global only" in pages["index.html"]


def test_sitemap_and_links_use_clean_urls_while_files_keep_html():
    """Static hosts serve /legal for legal.html and redirect /legal.html; a
    link with the extension is a redirect on every click."""
    _, pages = build_site([_obs()], base_url="https://example.test/", generated_at=AT)
    assert "<loc>https://example.test/</loc>" in pages["sitemap.xml"]
    assert "<loc>https://example.test/legal</loc>" in pages["sitemap.xml"]
    assert ".html</loc>" not in pages["sitemap.xml"]
    assert 'rel="canonical" href="https://example.test/fees"' in pages["fees.html"]
    compare = next(p for p in pages if p.startswith("compare/"))
    assert compare.endswith(".html")
    assert f'href="{compare.removesuffix(".html")}"' in pages["index.html"]
    assert '.html"' not in pages["index.html"]
    assert "Sitemap: https://example.test/sitemap.xml" in pages["robots.txt"]


def test_slugs_are_filesystem_and_url_safe():
    assert slugify("us_cpi_yoy|2026-08--kalshi:KXCPIYOY-26AUG-T3.0") == "us-cpi-yoy-2026-08-kalshi-kxcpiyoy-26aug-t3-0"
    assert len(slugify("x" * 500)) <= 120


def test_hostile_market_ids_are_escaped_and_slugged_safely():
    """Titles and ids come from venue payloads; they must never become markup."""
    obs = _obs(kid="kalshi:<script>alert(1)</script>")
    obs["kalshi_title"] = "<img src=x onerror=alert(1)>"
    _, pages = build_site([obs], base_url="https://example.test", generated_at=AT)
    html = next(h for p, h in pages.items() if p.startswith("compare/"))
    assert "<script>alert" not in html and "<img src=x" not in html
    assert all("<" not in p and ">" not in p for p in pages)


def test_the_approval_pipeline_never_imports_the_site_generator():
    import importlib
    import sys

    for name in list(sys.modules):
        if name.startswith("atlas.site"):
            del sys.modules[name]
    for module in ("atlas.normalization", "atlas.settlement", "atlas.verification"):
        importlib.import_module(module)
    assert not any(name.startswith("atlas.site") for name in sys.modules)


def test_analytics_tag_is_opt_in_and_validated():
    """No measurement id, no tag at all; a malformed id is dropped, not injected."""
    _, plain = build_site([_obs()], base_url="https://example.test", generated_at=AT)
    assert "googletagmanager" not in plain["index.html"]
    _, tagged = build_site(
        [_obs()], base_url="https://example.test", generated_at=AT, analytics_id="G-ABC1234567"
    )
    assert "gtag/js?id=G-ABC1234567" in tagged["index.html"]
    assert "anonymize_ip" in tagged["index.html"]
    _, bad = build_site(
        [_obs()], base_url="https://example.test", generated_at=AT, analytics_id="<script>"
    )
    assert "googletagmanager" not in bad["index.html"]
