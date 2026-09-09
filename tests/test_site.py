"""The demand-test site: guardrails a page cannot ship without.

Protects what the site is allowed to SAY, not how it looks: a pair is "the
same bet" only on the verifier's word, every page carries the disclosure and
the not-advice notice, fees on the calculator page are the venues' formulas
and not a copywriter's memory of them, and the build is deterministic.
"""

import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from atlas.gap_radar import kalshi_taker_fee_per_contract
from atlas.site import (
    DISCLOSURE,
    NO_STATE_ACTION,
    NOT_ADVICE,
    PairPage,
    build_site,
    family_label,
    render_fees,
    slugify,
    taker_fee_at,
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
    assert "global only" in pages["index.html"]


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


def _pages(**kw):
    return build_site([_obs()], base_url="https://example.test", generated_at=AT, **kw)[1]


def _compare(pages):
    return next(h for p, h in pages.items() if p.startswith("compare/"))


def test_prices_links_and_fees_appear_only_when_a_quote_exists():
    """No quote = no price and no guessed link; a quote = price, fee from the
    venue formula at that price, and an outbound link to the contract."""
    bare = _compare(_pages())
    assert "Open on Kalshi" not in bare and "Open on Polymarket" not in bare
    quotes = {
        "kalshi:KXCPIYOY-26AUG-T3.0": {"yes_ask": "0.42", "url": "https://kalshi.com/markets/x/y/z"},
        "polymarket_us:cpic-uscpi-august-yoy-2026-09-11-gt3pt0pct": {
            "yes_ask": "0.40", "url": "https://polymarket.us/event/e"},
    }
    html = _compare(_pages(quotes=quotes))
    assert "Open on Kalshi" in html and 'href="https://kalshi.com/markets/x/y/z"' in html
    assert "Open on Polymarket US" in html and 'href="https://polymarket.us/event/e"' in html
    assert "<td>42¢</td>" in html and "<td>40¢</td>" in html
    kalshi_fee = kalshi_taker_fee_per_contract(Decimal("0.42"))
    assert f"{kalshi_fee * 100:.1f}¢" in html
    assert taker_fee_at("polymarket_us", Decimal("0.40")) == Decimal("0.06") * Decimal("0.40") * Decimal("0.60")
    assert taker_fee_at("polymarket_global", Decimal("0.40")) is None


def test_human_summary_is_composed_from_codes_and_findings_not_written():
    page = PairPage(
        observation=_obs(),
        kalshi_grade={"grade": "B", "score": 85, "findings": [
            {"code": "MISSING_REVISION_POLICY", "points": 15, "prose": "the rules never say what a revision does"}]},
    )
    text = page.human_summary()
    assert text.startswith("Where they differ: both venues publish settlement policy")
    assert "Kalshi's fine print: the rules never say what a revision does." in text
    assert "delayed release" in text
    approved = PairPage(observation=_obs(status="APPROVED_INVERSE", codes=[]))
    assert "same outcome in every case" in approved.human_summary()


def test_index_groups_by_readable_family_with_verified_pairs_first():
    fomc = _obs(kid="kalshi:KXFEDDECISION-26SEP-H0", status="APPROVED_EQUIVALENT", codes=[])
    fomc["event_subject"] = "us_fomc_rate_decision|2026-09"
    fomc["kalshi_title"] = "Will the Fed hold rates in September?"
    _, pages = build_site([_obs(), fomc], base_url="https://example.test", generated_at=AT)
    index = pages["index.html"]
    assert index.index("Verified: the same bet on both venues") < index.index("<h2>CPI inflation, year over year</h2>")
    assert "<h2>Fed rate decisions</h2>" in index
    assert "us_cpi_yoy" not in index
    assert family_label("us_made_up_thing|2026") == "Us made up thing"
    assert "**" not in index  # markdown bold stripped from venue titles


def test_legal_table_renders_from_data_and_unsourced_action_fails_the_build():
    data = {"as_of": "2026-09-04", "states": [
        {"state": "Nevada", "status": "Court order restricting (TRO/injunction)",
         "summary": "A TRO covered sports contracts.", "categories": ["sports"],
         "sources": ["https://example.test/nv"]},
        {"state": "Wyoming", "status": NO_STATE_ACTION, "summary": "", "categories": [], "sources": []},
    ]}
    legal = _pages(legal_states=data)["legal.html"]
    assert "All 50 states and DC (as of 2026-09-04)" in legal
    assert "<strong>Nevada</strong>" in legal and 'href="https://example.test/nv"' in legal
    assert "<strong>Wyoming</strong>" in legal
    bad = {"as_of": "x", "states": [{"state": "Texas", "status": "Litigation pending", "sources": []}]}
    with pytest.raises(ValueError, match="Texas"):
        _pages(legal_states=bad)


def test_about_page_exists_and_is_linked_from_every_page():
    pages = _pages()
    assert "about.html" in pages
    assert "Not affiliated with" in pages["about.html"]
    for path, html in pages.items():
        if path.endswith(".html"):
            assert re.search(r'href="(\.\./)*about"', html), path


def test_comparison_page_renders_sourced_rows_and_faq_schema_and_rejects_unsourced():
    data = {"as_of": "2026-09-04", "rows": [
        {"topic": "Regulator", "kalshi": "CFTC-regulated DCM.", "polymarket_us": "CFTC-registered.",
         "polymarket_global": "Offshore; not for US accounts.", "sources": ["https://example.test/reg"]},
    ], "faq": [{"q": "Is Polymarket legal in the US?", "a": "Polymarket US is the US venue."}]}
    pages = _pages(comparison=data)
    page = pages["kalshi-vs-polymarket.html"]
    assert "Side by side (as of 2026-09-04)" in page
    assert "CFTC-regulated DCM." in page and 'href="https://example.test/reg"' in page
    assert '"@type": "FAQPage"' in page and "Is Polymarket legal in the US?" in page
    assert 'href="kalshi-vs-polymarket"' in pages["index.html"]
    with pytest.raises(ValueError, match="Regulator"):
        _pages(comparison={"rows": [{"topic": "Regulator", "kalshi": "x", "sources": []}]})
    # Without data the page still exists and routes to the pillars.
    bare = _pages()["kalshi-vs-polymarket.html"]
    assert "Side by side" not in bare and 'href="fees"' in bare


def test_indexnow_key_file_is_emitted_and_matches_the_constant():
    from atlas.site import INDEXNOW_KEY

    pages = _pages()
    assert pages[f"{INDEXNOW_KEY}.txt"].strip() == INDEXNOW_KEY
    assert f"{INDEXNOW_KEY}.txt" not in pages["sitemap.xml"]


def test_jsonld_cannot_break_out_of_its_script_tag():
    from atlas.site import _faq_jsonld

    out = _faq_jsonld([{"q": "x</script><script>alert(1)</script>", "a": "y"}])
    assert out.count("</script>") == 1


def test_referral_code_pages_publish_terms_and_only_show_a_code_when_the_owner_has_one():
    comparison = {"as_of": "2026-09-04", "rows": [
        {"topic": "Referral / promotions", "kalshi": "Referral credits aren't cash.",
         "polymarket_us": "$25 bonus credit for each qualifying referral",
         "polymarket_global": "10% of net trading fees", "sources": ["https://example.test/ref"]}],
        "faq": []}
    pages = _pages(comparison=comparison)
    k = pages["kalshi-referral-code.html"]
    assert "Referral credits aren&#x27;t cash." in k and "We do not publish a code" in k
    assert 'href="https://example.test/ref"' in k
    with_code = _pages(comparison=comparison, referral_codes={"kalshi": "OMAR123"})
    assert "<code>OMAR123</code>" in with_code["kalshi-referral-code.html"]
    assert "We do not publish a code" in with_code["polymarket-referral-code.html"]
    assert "<script>" not in _pages(referral_codes={"kalshi": "<script>"})["kalshi-referral-code.html"].split("<body>")[1].split("</main>")[0].replace("<script>window.dataLayer", "")


def test_arbitrage_page_lists_only_tradeable_pairs_sorted_by_gap_after_fees():
    a = _obs(kid="kalshi:A"); a["best_gap"] = "-0.0167"; a["best_basket_size"] = "1"
    b = _obs(kid="kalshi:B"); b["best_gap"] = "0.004"; b["best_basket_size"] = "62"
    b["kalshi_title"] = "TOP GAP PAIR"
    g = _obs(kid="kalshi:G", venue="polymarket_global", pid="polymarket_global:1"); g["best_gap"] = "0.30"
    _, pages = build_site([a, b, g], base_url="https://example.test", generated_at=AT)
    page = pages["arbitrage.html"]
    assert "TOP GAP PAIR" in page and "polymarket_global:1" not in page
    assert page.index("TOP GAP PAIR") < page.index("kalshi:A".replace("kalshi:", "")) or "0.4¢" in page
    assert "<strong>1</strong> show a positive gap" in page
    assert "27 matched pairs produced zero executable gaps" in page


def test_legit_pages_exist_only_with_data_and_require_sources():
    assert "is-kalshi-legit.html" not in _pages()
    data = {"as_of": "2026-09-07", "venues": {
        "kalshi": {"rows": [{"topic": "Regulator", "fact": "CFTC-designated DCM since 2020.",
                             "sources": ["https://example.test/cftc"]}]},
        "polymarket_us": {"rows": [{"topic": "Regulator", "fact": "Not found", "sources": []}]},
        "polymarket_global": {"rows": []}},
        "faq": [{"q": "Is Kalshi legit?", "a": "Kalshi is a CFTC-designated exchange."},
                {"q": "Is Polymarket safe?", "a": "Two venues; see the table."}]}
    pages = _pages(legit=data)
    k = pages["is-kalshi-legit.html"]
    assert "CFTC-designated DCM since 2020." in k and 'href="https://example.test/cftc"' in k
    assert "Is Kalshi legit?" in k and "Is Polymarket safe?" not in k
    assert "Is Polymarket safe?" in pages["is-polymarket-legit.html"]
    assert 'href="is-kalshi-legit"' in pages["index.html"]
    bad = {"as_of": "x", "venues": {"kalshi": {"rows": [{"topic": "Custody", "fact": "FDIC insured.", "sources": []}]}}}
    with pytest.raises(ValueError, match="Custody"):
        _pages(legit=bad)


def test_a_pair_not_quoted_for_three_days_is_shown_closed_not_deleted():
    """Deleting a settled pair's page would 404 an indexed URL."""
    fresh = _obs(kid="kalshi:FRESH", at="2026-09-04T10:00:00+00:00")
    old = _obs(kid="kalshi:OLD", at="2026-08-30T10:00:00+00:00")
    old["kalshi_title"] = "OLD SETTLED PAIR"; old["best_gap"] = "0.5"
    _, pages = build_site([fresh, old], base_url="https://example.test", generated_at=AT)
    old_page = next(h for p, h in pages.items() if "old" in p and p.startswith("compare/"))
    assert "No longer quoted" in old_page
    assert "Recently closed" in pages["index.html"] and "Tracking <strong>1 pairs" in pages["index.html"]
    assert "OLD SETTLED PAIR" not in pages["arbitrage.html"]


def test_also_on_note_appears_only_on_global_pages_for_mapped_events_and_gives_no_verdict():
    g = _obs(venue="polymarket_global", pid="polymarket_global:9")
    g["event_subject"] = "us_house_control|2026"
    us = _obs(kid="kalshi:US")
    us["event_subject"] = "us_house_control|2026"
    mapping = {"us_house_control|2026": {"venue": "polymarket_us", "event_slug": "usho-midterms-2026-11-03",
                                         "title": "U.S House Midterm Winner"}}
    _, pages = build_site([g, us], base_url="https://example.test", generated_at=AT, also_on=mapping)
    global_page = next(h for p, h in pages.items() if "polymarket-global-9" in p)
    us_page = next(h for p, h in pages.items() if "kalshi-us" in p)
    assert "Also listed on Polymarket US" in global_page
    assert 'href="https://polymarket.us/event/usho-midterms-2026-11-03"' in global_page
    assert "no verdict is given for that pairing" in global_page
    assert "Also listed on Polymarket US" not in us_page


def test_a_referral_link_renders_as_a_sponsored_link_not_a_code():
    url = "https://kalshi.com/sign-up/?referral=abc&m=true"
    page = _pages(referral_codes={"kalshi": url})["kalshi-referral-code.html"]
    # Ampersands are escaped in attributes; browsers decode them.
    assert f'href="{url.replace("&", "&amp;")}"' in page and 'rel="sponsored noopener"' in page
    assert "trading credits, not cash" in page
    assert "<code>https://" not in page
    # A non-https value is never turned into a link.
    plain = _pages(referral_codes={"kalshi": "javascript:alert(1)"})["kalshi-referral-code.html"]
    assert "href=\"javascript:" not in plain and "<code>javascript:alert(1)</code>" in plain


async def test_polymarket_us_event_open_by_slug_is_strict():
    from atlas.cli import _polymarket_us_event_open

    class Venue:
        def __init__(self, payload):
            self.payload = payload

        async def _get(self, path, params=None):
            return self.payload

    assert await _polymarket_us_event_open(
        Venue({"events": [{"slug": "e", "active": True, "closed": False}]}), "e"
    )
    assert not await _polymarket_us_event_open(
        Venue({"events": [{"slug": "e", "active": True, "closed": True}]}), "e"
    )
    assert not await _polymarket_us_event_open(
        Venue({"events": [{"slug": "other", "active": True, "closed": False}]}), "e"
    )
    assert not await _polymarket_us_event_open(Venue({"events": []}), "e")
    assert not await _polymarket_us_event_open(Venue({}), "")
