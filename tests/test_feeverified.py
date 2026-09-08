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


def test_engine_covers_every_schedule_file():
    engine = (ROOT / "feeverified" / "static" / "fees.js").read_text()
    for path in SCHEDULES:
        assert f"{path.stem}(" in engine or f"{path.stem}:" in engine or f"{path.stem} " in engine or path.stem in engine, path.stem


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
