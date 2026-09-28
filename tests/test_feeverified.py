"""Fee Verified: every calculator is pinned to the platform's own published
examples, and every number on a page traces to a schedule file that cites the
page it was read from.

The engine is JavaScript (it runs in the browser); the tests execute it with
Node so there is exactly one implementation to be right.
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEDULES = sorted(
    p for p in (ROOT / "docs" / "fees").glob("*.json") if not p.name.startswith(("_", "verification"))
)
RUN_JS = ROOT / "feeverified" / "static" / "run.js"


def compute(schedule_path: Path, inputs: dict) -> dict:
    out = subprocess.run(
        ["node", str(RUN_JS), str(schedule_path), json.dumps(inputs)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(out.stdout)


def _examples():
    for path in SCHEDULES:
        data = json.loads(path.read_text())
        for n, example in enumerate(data.get("examples", [])):
            yield pytest.param(path, example, id=f"{data['platform']}-{n}")


@pytest.mark.parametrize("path,example", list(_examples()))
def test_calculator_reproduces_the_platforms_published_example(path, example):
    result = compute(path, example["inputs"])
    lines = {line["id"]: line["amount"] for line in result["lines"]}
    for key, expected in example["expected"].items():
        if expected is None:
            continue
        actual = result.get(key, lines.get(key))
        assert actual is not None, f"{key} missing from result"
        assert abs(actual - expected) < 0.005, f"{path.stem} {key}: got {actual}, page says {expected}"


@pytest.mark.parametrize("path", SCHEDULES, ids=[p.stem for p in SCHEDULES])
def test_every_schedule_cites_a_loaded_source_and_quotes_its_numbers(path):
    data = json.loads(path.read_text())
    assert data["platform"] == path.stem
    assert data["sources"], "no source"
    for source in data["sources"]:
        assert source["url"].startswith("https://") and source.get("loaded")
    assert data["rates"], "no rates"
    assert data["quotes"], "a schedule with no quoted sentences cannot be checked against its page"
    for example in data.get("examples", []):
        assert example.get("quote") and example.get("source_url"), "example without a quote"
        if example.get("derived"):
            assert "derived" in example["description"].lower() or "arithmetic" in example["description"].lower()


def test_engine_covers_every_calculator_schedule():
    engine = (ROOT / "feeverified" / "static" / "fees.js").read_text()
    for path in SCHEDULES:
        data = json.loads(path.read_text())
        if data.get("no_calculator"):
            continue
        assert f"function {path.stem}(" in engine, path.stem


def test_rounding_is_half_up_to_the_cent():
    """1054.545 must become 1054.55 — eBay's own example depends on it."""
    out = subprocess.run(
        ["node", "-e", "const e=require(process.argv[1]);const FV=e.FeeVerified||e;console.log(FV.cents(1054.545), FV.cents(58.064), FV.cents(0.005))",
         str(ROOT / "feeverified" / "static" / "fees.js")],
        capture_output=True, text=True, check=True, timeout=30,
    )
    assert out.stdout.strip() == "1054.55 58.06 0.01"


def test_fingerprint_ignores_page_noise_but_catches_a_rate_change():
    """Session ids and widgets churn between fetches; the fee sentences are what
    a seller's money depends on, so they are the only thing that decides
    'changed'."""
    from feeverified.verify import fee_fingerprint, fee_lines, visible_text

    page = ("<html><script>var nonce='abc123'</script><body><div>928871771278</div>"
            "<p>Suggested queries</p><p>We charge 13.6% on the total amount of the sale up to $7,500.</p>"
            "<p>For orders $10.00 or less the per order fee is $0.30.</p><p>Was this article helpful?</p></body></html>")
    noisy = page.replace("928871771278", "111111111111").replace("Suggested queries", "Try different search terms")
    changed = page.replace("13.6%", "13.9%")
    assert fee_fingerprint(page) == fee_fingerprint(noisy)
    assert fee_fingerprint(page) != fee_fingerprint(changed)
    assert fee_lines(visible_text(page)) == [
        "For orders $10.00 or less the per order fee is $0.30.",
        "We charge 13.6% on the total amount of the sale up to $7,500.",
    ]


def test_build_guardrails_every_page_carries_disclosure_and_status():
    from datetime import UTC, datetime

    from feeverified import site

    pages = site.build("https://example.test", generated_at=datetime(2026, 9, 8, tzinfo=UTC))
    assert site.verify_pages(pages) == []
    assert "ebay.html" in pages and "paypal.html" in pages
    assert 'class="status ' in pages["ebay.html"]
    assert "https://www.ebay.com/help/selling/fees-credits-invoices/selling-fees" in pages["ebay.html"]
    assert "<loc>https://example.test/ebay</loc>" in pages["sitemap.xml"]
    assert "not affiliated with" in pages["ebay.html"] and "not affiliated with" in pages["index.html"]
    assert "not affiliated with eBay" in pages["ebay.html"]
    assert '"@type": "WebSite"' in pages["ebay.html"] and 'og:site_name' in pages["index.html"]
    assert "impact-site-verification" in pages["index.html"] and "impact-site-verification" in pages["ebay.html"]
    shopify_link = site.PARTNERS["shopify"]["url"]
    assert shopify_link in pages["shopify.html"]
    assert shopify_link in pages["how-much-does-shopify-take.html"]
    assert "Sponsored link" in pages["shopify.html"]
    # Each partner states its own terms: Shopify has no discount, Intuit's link carries one.
    assert "carries no discount" in pages["shopify.html"]
    quickbooks_link = site.PARTNERS["quickbooks"]["url"]
    assert quickbooks_link in pages["quickbooks.html"]
    assert quickbooks_link in pages["how-much-does-quickbooks-take.html"]
    assert "own new-customer discount" in pages["quickbooks.html"]
    assert "carries no discount" not in pages["quickbooks.html"]
    assert quickbooks_link not in pages["compare/payment-processors.html"]
    assert quickbooks_link not in pages["index.html"]
    # The money never sits on a page that compares a paid platform with an unpaid one.
    assert shopify_link not in pages["compare/etsy-vs-shopify.html"]
    assert shopify_link not in pages["index.html"]
    assert shopify_link not in pages["etsy.html"]
    tilted = {"compare/x.html": f"{site.DISCLOSURE}{site.NOT_ADVICE}{site.INDEPENDENCE}{shopify_link}"}
    assert site.verify_pages(tilted) == ["compare/x.html: shopify sponsored link on a comparison/index page"]
    bare = {"x.html": f"<p>{site.DISCLOSURE}</p><p>{site.NOT_ADVICE}</p>"}
    assert site.verify_pages(bare) == ["x.html: missing independence line"]


def test_quote_check_ignores_typography_but_catches_a_reworded_fee():
    from feeverified.verify import missing_quotes

    page = "We charge 13.6% on the total amount of the sale up to $7,500.  For orders $10.00 or less the per order fee is $0.30."
    assert missing_quotes(page, ["We charge 13.6% on the total amount of the sale up to $7,500."]) == []
    assert missing_quotes(page.replace("$0.30", "$0.35"), ["For orders $10.00 or less the per order fee is $0.30."]) == [
        "For orders $10.00 or less the per order fee is $0.30."
    ]
    # Curly quotes, dashes and spacing are not changes; '...' splits a quote into fragments.
    assert missing_quotes("It\u2019s 13.6% \u2013 up to $7,500", ["It's 13.6% - up to $7,500"]) == []
    assert missing_quotes(page, ["13.6% on the total amount … per order fee is $0.30."]) == []


def test_quote_check_ignores_spacing_that_markup_leaves_behind():
    """A tag becomes a space or a line break where it stood, so one sentence can
    read three ways on three nights. None of them is a change to a fee."""
    from feeverified.verify import missing_quotes, visible_text

    page = visible_text(
        "<p>The minimum amount you can withdraw is $<b>0.10</b>. Withdrawals below this amount will fail.</p>"
        "<p>Available in Poland, Portuga<a href='#'>l</a>, Romania.</p>"
        "<table><tr><td>United States</td><td>3% + 0.25 USD</td></tr></table>"
    )
    assert "$ 0.10" in page and "Portuga l" in page  # what the extraction really produces
    assert missing_quotes(page, [
        "The minimum amount you can withdraw is $0.10. Withdrawals below this amount will fail.",
        "Available in Poland, Portugal, Romania.",
        "United States | 3% + 0.25 USD",
        "United States 3% + 0.25 USD",
    ]) == []
    # Every character that is not spacing still has to match.
    assert missing_quotes(page, ["The minimum amount you can withdraw is $0.15."]) == [
        "The minimum amount you can withdraw is $0.15."
    ]
    assert missing_quotes(page, ["United States | 3.5% + 0.25 USD"]) == ["United States | 3.5% + 0.25 USD"]


WHATNOT = ROOT / "docs" / "fees" / "whatnot.json"


@pytest.mark.parametrize(("inputs", "commission", "processing"), [
    # Standard tier: what the rates were before the tiers, 8% and 4% on coins.
    ({"price": 100, "vertical": "other", "tier": "standard"}, 8.00, 3.20),
    ({"price": 100, "vertical": "coins", "tier": "standard"}, 4.00, 3.20),
    # The table read across: Fashion at Tier 3 is 5.50%, Coins at Tier 6 is 3.50%.
    ({"price": 100, "vertical": "fashion", "tier": "tier3"}, 5.50, 3.20),
    ({"price": 100, "vertical": "coins", "tier": "tier6"}, 3.50, 3.20),
    ({"price": 200, "vertical": "sports", "tier": "tier1"}, 15.50, 6.10),
    # Processing is on the buyer's whole total; the tier never reduces it.
    ({"price": 100, "shipping": 10, "sales_tax": 8, "vertical": "other", "tier": "tier6"}, 4.00, 3.72),
    # Above $1,500 the promotion charges 0%, at the selected tier's rate below it.
    ({"price": 2000, "vertical": "tcg", "tier": "tier2", "high_value": True}, 112.50, 58.30),
    # An unknown group or tier falls back to Other at Standard, never to a lower rate.
    ({"price": 100, "vertical": "pallets", "tier": "tier9"}, 8.00, 3.20),
])
def test_whatnot_reads_its_rate_from_the_tier_table(inputs, commission, processing):
    result = compute(WHATNOT, inputs)
    lines = {line["id"]: line["amount"] for line in result["lines"]}
    assert lines["commission"] == pytest.approx(commission, abs=0.005)
    assert lines["processing_fee"] == pytest.approx(processing, abs=0.005)


def test_whatnot_table_is_the_one_on_the_page():
    data = json.loads(WHATNOT.read_text())
    rates = data["rates"]
    assert rates["tier_order"] == ["standard", "tier1", "tier2", "tier3", "tier4", "tier5", "tier6"]
    for group, row in rates["commission"].items():
        assert len(row) == 7 and row == sorted(row, reverse=True), group  # a higher tier never costs more
    table = next(q for q in data["quotes"] if q.startswith("Standard $0"))
    for group, name in (("sports", "Sports"), ("tcg", "TCG"), ("fashion", "Fashion"),
                        ("other_collectibles", "Other Collectibles"), ("coins", "Coins"), ("other", "Other")):
        cells = table.split(f" {name} ")[-1].replace("LOWEST PUBLIC RATE", "").split()[:7]
        assert [round(float(c.rstrip("%")) / 100, 6) for c in cells] == rates["commission"][group], group
    options = {value for value, _ in next(i for i in data["inputs"] if i["id"] == "tier")["options"]}
    assert options == set(rates["tier_order"])
