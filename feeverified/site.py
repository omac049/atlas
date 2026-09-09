"""Static site generator for Fee Verified.

Every calculator page is generated from docs/fees/<platform>.json and carries:
the schedule's source link and the date its numbers were read, the nightly
verification status (verified / under review / could not check), the
platform's own published example reproduced by the engine, and the
disclosure. Nothing on a page is an opinion about which platform to use.
"""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from pathlib import Path

from feeverified import engine

ROOT = Path(__file__).resolve().parent.parent
FEES_DIR = ROOT / "docs" / "fees"
STATIC_DIR = ROOT / "feeverified" / "static"
SITE_NAME = "Fee Verified"
# Public by design: served at /{key}.txt so search engines can verify IndexNow submissions.
INDEXNOW_KEY = "4f6a2c9e1b7d4a3e9c0f5b8d2a6e7c1f"

DISCLOSURE = (
    "Disclosure: this site may earn a referral fee when you sign up for a seller tool "
    "through a link here. The platforms whose fees are explained on these pages do not pay "
    "us, and no referral relationship changes a number: every fee is computed from the "
    "platform's own published schedule, linked on the page."
)
NOT_ADVICE = (
    "This is a calculator, not advice. It computes what a platform publishes; it does not "
    "tell you which platform to use, and it cannot see promotions, negotiated rates, or fees "
    "the platform has not published."
)
# Sponsored links. Two rules, enforced by verify_pages() and its test:
#   1. A partner link appears only on that platform's OWN pages. Never on a
#      comparison page or the index, where one paid link beside an unpaid
#      competitor would tilt a page whose entire job is arithmetic.
#   2. No price or offer claim lives here. Prices belong in the calculator,
#      which is computed from the published schedule and checked nightly.
PARTNERS = {
    "shopify": {
        "url": "https://shopify.pxf.io/c/7753483/1061744/13624",
        "label": "Start a Shopify store",
        "network": "Impact",
        "since": "2026-09-09",
    },
}


def partner_box(schedule: dict) -> str:
    """The sponsored link for one platform, or nothing. Labeled, never styled
    as editorial text, and always below the numbers it must not influence."""
    partner = PARTNERS.get(schedule["platform"])
    if not partner:
        return ""
    short = _esc(schedule["name"].split(" (")[0])
    return (
        '<div class="partner"><p class="tag">Sponsored link</p>'
        f"<p>{short} pays this site a referral fee if you sign up through the link below. It changes "
        f"nothing above: every rate on this page is computed from {short}'s own published fee "
        "schedule and re-checked nightly. The link carries no discount and no special terms — you "
        f"get {short}'s standard published pricing, the same as typing the address yourself.</p>"
        f'<p><a class="ext" href="{_esc(partner["url"])}" rel="sponsored nofollow noopener" '
        f'target="_blank">{_esc(partner["label"])}</a></p></div>'
    )


# Site-ownership tags that affiliate networks ask for; public by design.
HEAD_VERIFICATION_TAGS = (
    '<meta name="impact-site-verification" value="096c8924-cef7-413d-a04d-6410c9c8155f">',
)
INDEPENDENCE = (
    "Independent site: Fee Verified is not affiliated with, endorsed by, or operated by any "
    "platform named here. There are no accounts or logins; the only thing you enter is a sale amount."
)

STATUS_TEXT = {
    "verified": ("verified", "Verified against {name}'s fee page"),
    "changed": ("review", "{name}'s fee page changed — these numbers are under review"),
    "unreachable": ("warn", "{name}'s fee page could not be checked tonight; last verified {date}"),
    "unreviewed": ("warn", "Not yet verified against the live page"),
}


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _money(value: object) -> str:
    return f"${float(value):,.2f}"


_CSS = """
:root{--ink:#17181a;--muted:#5f6368;--line:#e6e6e8;--soft:#f6f6f7;--ok:#146c3a;--ok-bg:#e6f4ea;
--warn:#8a4b00;--warn-bg:#fdf1e3;--rev:#7a1f1f;--rev-bg:#fbe9e9;--accent:#1d4ed8;--bg:#fff}
*{box-sizing:border-box}body{margin:0;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",
Roboto,Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg)}a{color:var(--accent)}
main{max-width:900px;margin:0 auto;padding:28px 20px 64px}header{border-bottom:1px solid var(--line)}
.top{max-width:900px;margin:0 auto;padding:14px 20px;display:flex;flex-wrap:wrap;gap:8px 18px;
align-items:baseline}.brand{font-weight:800;font-size:1.05rem;color:var(--ink);text-decoration:none;
margin-right:auto}.brand span{color:var(--accent)}.top nav{display:flex;flex-wrap:wrap;gap:4px 14px;
font-size:14px}.top nav a{color:var(--ink);text-decoration:none}
h1{font-size:1.85rem;line-height:1.2;margin:.2em 0 .4em}h2{font-size:1.2rem;margin:1.8em 0 .5em}
p{margin:.55em 0}.lede{font-size:1.08rem;color:#333}header .independent{max-width:900px;margin:0 auto;padding:0 20px .6rem;font-size:.82rem;color:#5b6472}.muted{color:var(--muted);font-size:14px}
.partner{border:1px solid var(--line);border-left:3px solid var(--muted);background:var(--soft);border-radius:10px;padding:10px 16px;margin:2em 0 1em}
.partner p{margin:.5em 0}.partner .tag{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;margin:.2em 0}
.small{font-size:13px}.badge{display:inline-block;padding:3px 10px;border-radius:999px;font-size:13px;
font-weight:600}.badge.verified{background:var(--ok-bg);color:var(--ok)}.badge.review{background:var(--rev-bg);
color:var(--rev)}.badge.warn{background:var(--warn-bg);color:var(--warn)}
.status{padding:12px 16px;border-radius:10px;margin:1em 0;font-weight:600}.status.verified{background:var(--ok-bg);
color:var(--ok)}.status.review{background:var(--rev-bg);color:var(--rev)}.status.warn{background:var(--warn-bg);
color:var(--warn)}
.calc{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin:1.2em 0}
@media(max-width:700px){.calc{grid-template-columns:1fr}h1{font-size:1.5rem}}
.field{margin:0 0 10px}.field label{display:flex;flex-direction:column;gap:4px;font-size:14px;font-weight:600}
.field.check label{flex-direction:row;align-items:center;font-weight:500}
.field input[type=number],.field select{font-size:16px;padding:7px 9px;border:1px solid #bbb;border-radius:6px;
width:100%;max-width:320px}
table{border-collapse:collapse;width:100%;margin:.6em 0;font-size:15px}th,td{text-align:left;padding:8px 10px;
border-bottom:1px solid var(--line);vertical-align:top}th{font-weight:600;background:var(--soft)}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}tr.total td{font-weight:700;
border-top:2px solid var(--ink)}tr.net td{font-weight:700;color:var(--ok)}tr.detail td{padding-top:0;border:0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin:1em 0}
.card{border:1px solid var(--line);border-radius:10px;padding:14px 16px}.card a{font-weight:600;
text-decoration:none}.card p{margin:.3em 0 0;font-size:14px;color:var(--muted)}
blockquote{margin:.6em 0;padding:.7em 1em;border-left:3px solid var(--line);color:#333;font-size:14px;
background:#fcfcfc}footer{max-width:900px;margin:0 auto;padding:22px 20px;border-top:1px solid var(--line);
font-size:13px;color:var(--muted)}code{background:var(--soft);padding:1px 5px;border-radius:4px}
"""

_NAV = (("index.html", "Calculators"), ("compare.html", "Compare"), ("methodology.html", "How it's verified"), ("about.html", "About"))

# Comparison pages people actually search for; rendered only for the platforms
# whose schedules exist, with each platform's declared defaults.
COMPARISONS = (
    ("resale-marketplaces", "eBay vs Mercari vs Poshmark vs Depop fees", ("ebay", "mercari", "poshmark", "depop")),
    ("payment-processors", "PayPal vs Stripe vs Square fees", ("paypal", "stripe", "square")),
    ("etsy-vs-shopify", "Etsy vs Shopify fees", ("etsy", "shopify")),
    ("payment-apps", "Venmo vs Cash App vs PayPal fees for goods and services", ("venmo", "cashapp", "paypal")),
)


def _href(path: str) -> str:
    return "" if path == "index.html" else path.removesuffix(".html")


def load_schedules() -> list[dict]:
    out = []
    for path in sorted(FEES_DIR.glob("*.json")):
        if path.name.startswith(("_", "verification")):
            continue
        out.append(json.loads(path.read_text()))
    return out


def load_verification() -> dict:
    from feeverified import verify

    return verify.merged_status()


def status_for(schedule: dict, verification: dict) -> tuple[str, str, str]:
    entry = verification.get(schedule["platform"], {})
    status = entry.get("status", "unreviewed")
    kind, template = STATUS_TEXT.get(status, STATUS_TEXT["unreviewed"])
    reviewed = str(entry.get("reviewed_at") or schedule.get("as_of") or "")[:10]
    checked = str(entry.get("last_checked_at") or "")[:10]
    text = template.format(name=schedule["name"].split(" (")[0], date=reviewed)
    if status == "verified" and checked:
        text += f" · numbers reviewed {reviewed} · page checked {checked}"
    return status, kind, text


def coverage_note(schedule: dict, verification: dict) -> str:
    """How much of the schedule the nightly check can actually see."""
    entry = verification.get(schedule["platform"], {})
    total = len(schedule.get("quotes", []))
    if not total or "quotes_missing_at_review" not in entry:
        return ""
    found = total - len(entry["quotes_missing_at_review"])
    unverified_sources = sum(1 for s in schedule["sources"] if s.get("verify") is False)
    note = f"The nightly check re-reads {found} of the {total} sentences quoted on this page"
    if found < total:
        note += (
            "; the rest are in tables or on pages that block automated reading and were "
            f"verified by a person on {str(entry.get('reviewed_at') or schedule.get('as_of'))[:10]}"
        )
    if unverified_sources:
        note += f" ({unverified_sources} source page{'s' if unverified_sources > 1 else ''} read by a person only)"
    return note + "."


def _identity_jsonld(site: dict) -> str:
    """Who publishes this site, in machine-readable form, on every page."""
    base = site["base_url"].rstrip("/")
    data = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "Fee Verified",
        "url": f"{base}/",
        "description": (
            "Independent seller-fee calculators computed from each platform's published fee "
            "schedule and verified against it nightly. Not affiliated with any platform."
        ),
        "publisher": {"@type": "Organization", "name": "Fee Verified", "url": f"{base}/about"},
    }
    return json.dumps(data).replace("</", "<\\/")


def _page(site: dict, *, title: str, path: str, body: str, description: str, head_extra: str = "") -> str:
    root = "../" * path.count("/")
    nav = "".join(f'<a href="{root}{_href(h) or "./"}">{_esc(label)}</a>' for h, label in _NAV)
    canonical = f"{site['base_url'].rstrip('/')}/{_href(path)}"
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_esc(title)}</title><meta name=\"description\" content=\"{_esc(description)}\">"
        f"<link rel=\"canonical\" href=\"{_esc(canonical)}\">"
        f"<meta property=\"og:title\" content=\"{_esc(title)}\"><meta property=\"og:description\" content=\"{_esc(description)}\">"
        "<meta property=\"og:site_name\" content=\"Fee Verified\"><meta name=\"author\" content=\"Fee Verified\">"
        + "".join(HEAD_VERIFICATION_TAGS) +
        f"<script type=\"application/ld+json\">{_identity_jsonld(site)}</script>"
        "<link rel=\"icon\" href=\"data:,\">"
        f"<style>{_CSS}</style>{head_extra}</head><body>"
        f"<header><div class=\"top\"><a class=\"brand\" href=\"{root or './'}\">Fee <span>Verified</span></a><nav>{nav}</nav></div><p class=\"independent\">{_esc(INDEPENDENCE)}</p></header>"
        f"<main>{body}</main>"
        f"<footer><p>{_esc(DISCLOSURE)}</p><p>{_esc(NOT_ADVICE)}</p>"
        f"<p>Generated {_esc(site['stamp'])}. <a href=\"{root}methodology\">How every number is verified</a> · <a href=\"{root}about\">About</a></p></footer>"
        "</body></html>\n"
    )


def render_platform(site: dict, schedule: dict, verification: dict, engine_js: str, ui_js: str) -> str:
    if schedule.get("no_calculator"):
        return render_fact_page(site, schedule, verification)
    _status, kind, text = status_for(schedule, verification)
    name = schedule["name"]
    short = name.split(" (")[0]
    sources = "".join(
        f"<li><a href=\"{_esc(s['url'])}\" rel=\"noopener\">{_esc(s['title'])}</a> — read {_esc(s.get('loaded', ''))}"
        + (f"; the page states \"Last updated {_esc(s['last_updated_on_page'])}\"" if s.get("last_updated_on_page") else "")
        + "</li>"
        for s in schedule["sources"]
    )
    examples = ""
    for ex in schedule.get("examples", []):
        exp = ", ".join(f"{k.replace('_', ' ')} {_money(v)}" for k, v in ex["expected"].items() if v is not None)
        examples += (
            f"<li>{_esc(ex['description'])} <strong>Page says:</strong> {_esc(exp)}. "
            f"<span class=\"muted\">\"{_esc(ex['quote'][:220])}\"</span></li>"
        )
    excluded = "".join(f"<li>{_esc(x)}</li>" for x in schedule.get("excluded", []))
    notes = "".join(f"<li>{_esc(x)}</li>" for x in schedule.get("notes", []))
    schedule_json = json.dumps(schedule).replace("</", "<\\/")
    glance = "".join(
        f"<tr><td>{_money(r['sale'])}</td><td class=\"num\">{_money(r['fees'])}</td><td class=\"num\">{_money(r['net'])}</td><td class=\"num\">{r['rate']:.2f}%</td></tr>"
        for r in engine.standard_table(schedule)
    )
    body = (
        f"<h1>{_esc(short)} fee calculator</h1>"
        f"<p class=\"muted small\">An independent calculator, not {_esc(short)}: nothing to sign in to and nothing to pay here. Enter a sale amount and read the fees.</p>"
        f"<p class=\"lede\">{_esc(schedule.get('summary', ''))}</p>"
        f"<div class=\"status {kind}\">{_esc(text)}</div>"
        + (f"<p class=\"muted small\">{_esc(coverage_note(schedule, verification))}</p>" if coverage_note(schedule, verification) else "")
        + "<div class=\"calc\"><form id=\"calc-form\" autocomplete=\"off\"></form><div id=\"calc-out\"></div></div>"
        "<h2>At a glance (default settings)</h2>"
        "<table><thead><tr><th>Sale</th><th class=\"num\">Fees</th><th class=\"num\">You keep</th><th class=\"num\">Effective rate</th></tr></thead>"
        f"<tbody>{glance}</tbody></table>"
        f"<p class=\"muted\">Computed from the schedule with the calculator's default settings; change them above for your case. "
        f"<a href=\"how-much-does-{_esc(schedule['platform'])}-take\">How much does {_esc(short)} take?</a> explains each fee in words.</p>"
        "<h2>Where these numbers come from</h2>"
        f"<ul>{sources}</ul>"
        f"<p>{_esc(schedule.get('structure', ''))}</p>"
        + (f"<h2>Checked against {_esc(short)}'s own examples</h2><p class=\"muted\">The calculator reproduces every worked example the platform publishes; these are run as tests before any page is built.</p><ul>{examples}</ul>" if examples else "")
        + (f"<h2>Not included</h2><ul>{excluded}</ul>" if excluded else "")
        + (f"<h2>Notes</h2><ul>{notes}</ul>" if notes else "")
        + partner_box(schedule)
        + "<p class=\"muted\">See a mistake? The schedule file behind this page is public; corrections that cite the platform's page are applied.</p>"
        f"<script>{engine_js}</script><script>{ui_js}</script>"
        f"<script>window.addEventListener('DOMContentLoaded',function(){{window.FeeVerifiedUI.mount({schedule_json},'calc-form','calc-out');}});</script>"
    )
    title = f"{short} fee calculator ({site['year']}): exact seller fees from {short}'s published schedule"
    return _page(site, title=title, path=f"{schedule['platform']}.html", body=body,
                 description=f"{short} seller fees computed from the published fee schedule, verified against the page, with the platform's own examples reproduced. Independent site, not affiliated with {short}.")


def render_index(site: dict, schedules: list[dict], verification: dict) -> str:
    cards = ""
    for s in schedules:
        status, kind, _ = status_for(s, verification)
        label = {"verified": "verified", "changed": "under review", "unreachable": "check pending", "unreviewed": "not yet verified"}[status]
        cards += (
            f"<div class=\"card\"><a href=\"{_esc(s['platform'])}\">{_esc(s['name'].split(' (')[0])} fees</a> "
            f"<span class=\"badge {kind}\">{label}</span><p>{_esc(s.get('summary', '')[:140])}…</p></div>"
        )
    body = (
        "<h1>Seller fee calculators, computed from the platforms' own schedules</h1>"
        "<p class=\"lede\">Every calculator here takes its numbers from the platform's published fee "
        "page, links that page, says when the numbers were last read, and is checked against the "
        "page every night. When a platform changes its fees, the calculator says so before a human "
        "has re-read the schedule — it never quietly shows old numbers as current.</p>"
        f"<div class=\"cards\">{cards}</div>"
        "<h2>Why this exists</h2><p>Most fee calculators online are a formula someone typed in "
        "once. Fees change several times a year, and a wrong number costs sellers real money. "
        "This site treats each platform's fee page as the source of truth and proves it: the "
        "worked examples the platforms publish are reproduced to the cent as automated tests "
        "before any page is generated.</p>"
    )
    return _page(site, title="Fee Verified — seller fee calculators from the platforms' own published schedules",
                 path="index.html", body=body, description="eBay, PayPal, Etsy and other seller fee calculators computed from each platform's published schedule and verified against it nightly.")


def render_methodology(site: dict) -> str:
    body = (
        "<h1>How every number is verified</h1>"
        "<p>Each platform has a schedule file: the fee components, the exact sentences on the "
        "platform's page that state them, the page's URL, and the date the numbers were read. "
        "The calculator on the page computes from that file and nothing else.</p>"
        "<h2>The nightly check</h2><p>Every night each platform's fee page is fetched and the exact "
        "sentences this site relies on — the ones quoted on each calculator page — are looked for in "
        "the page's text. If every sentence is still there, the page shows "
        "<span class=\"badge verified\">verified</span> with the date the numbers were last reviewed and "
        "the date the page was last checked. If a sentence has disappeared or been reworded, the page "
        "shows <span class=\"badge review\">under review</span> until a person re-reads the schedule and "
        "updates the file — the calculator stays usable but is honest about its state. If the page "
        "could not be fetched, the page says so and keeps the last verified date. A fingerprint of "
        "all fee-bearing sentences is recorded as supporting evidence. Numbers are never changed by "
        "software.</p>"
        "<h2>Pinned to the platforms' own arithmetic</h2><p>Where a platform publishes a worked "
        "example (eBay publishes two on its fees page), the calculator must reproduce it to the "
        "cent in an automated test, or the site does not build. Where a platform publishes only "
        "rates, the tests check derived arithmetic and say so on the page.</p>"
        "<h2>What is deliberately left out</h2><p>Promotions, negotiated rates, store subscriptions "
        "unless stated, chargebacks and disputes, shipping label costs, and anything the platform "
        "has not published. Each page lists its exclusions.</p>"
        "<h2>How this site makes money</h2><p>Some pages carry a sponsored link to the platform "
        "that page is about. If you sign up through one, this site receives a referral fee and you "
        "pay that platform's standard published price — there is no discount, and no better link. "
        "Three rules keep the money away from the numbers. A sponsored link never changes a computed "
        "fee. It appears only on that platform's own pages, never on a page comparing two platforms, "
        "so no comparison here has a paid side and an unpaid side. And no platform pays to be "
        "included, ranked, or described in any particular way; the platforms whose fees are "
        "calculated here do not pay for the calculators.</p>"
        "<p>The code and the schedule files are public. Corrections that cite the platform's own "
        "page are applied.</p>"
    )
    return _page(site, title="How Fee Verified checks its numbers", path="methodology.html", body=body,
                 description="Each calculator computes from the platform's published schedule, is pinned to the platform's own examples, and is checked nightly against the page.")


def render_about(site: dict) -> str:
    body = (
        "<h1>About</h1>"
        "<p class=\"lede\">Fee Verified is a small, generated site: calculators built from the fee "
        "schedules platforms publish, verified against those pages every night.</p>"
        "<p><strong>Who.</strong> Omar (GitHub: <a href=\"https://github.com/omac049\" rel=\"noopener\">omac049</a>), "
        "a marketing-analytics professional. Not affiliated with any platform on this site.</p>"
        "<p><strong>Money.</strong> The site may earn referral fees from seller tools, disclosed on "
        "every page. The platforms whose fees are explained here pay nothing, and no relationship "
        "changes a number.</p>"
        "<p><strong>Corrections.</strong> Open an issue at "
        "<a href=\"https://github.com/omac049/atlas/issues\" rel=\"noopener\">github.com/omac049/atlas/issues</a> "
        "citing the platform's page.</p>"
    )
    return _page(site, title="About Fee Verified", path="about.html", body=body, description="Who makes Fee Verified and how to send a correction.")


def render_fact_page(site: dict, schedule: dict, verification: dict) -> str:
    """A platform that publishes no computable schedule gets a page that says
    exactly that, with the sentences that say it — not a made-up calculator."""
    _status, kind, text = status_for(schedule, verification)
    short = schedule["name"].split(" (")[0]
    quotes = "".join(f"<li>{_esc(q)}</li>" for q in schedule.get("quotes", [])[:10])
    sources = "".join(f"<li><a href=\"{_esc(s['url'])}\" rel=\"noopener\">{_esc(s['title'])}</a></li>" for s in schedule["sources"])
    notes = "".join(f"<li>{_esc(x)}</li>" for x in schedule.get("notes", []))
    body = (
        f"<h1>{_esc(short)} seller fees</h1>"
        f"<p class=\"muted small\">An independent calculator, not {_esc(short)}: nothing to sign in to and nothing to pay here. Enter a sale amount and read the fees.</p>"
        f"<p class=\"lede\">{_esc(schedule.get('summary', ''))}</p>"
        f"<div class=\"status {kind}\">{_esc(text)}</div>"
        f"<h2>What {_esc(short)} publishes</h2><p>{_esc(schedule.get('structure', ''))}</p>"
        f"<h2>In {_esc(short)}'s own words</h2><ul>{quotes}</ul>"
        f"<h2>Sources</h2><ul>{sources}</ul>"
        + (f"<h2>Notes</h2><ul>{notes}</ul>" if notes else "")
        + "<p class=\"muted\">No calculator is offered because the platform publishes no rate to compute from. Third-party sites quoting a percentage are quoting something the platform itself does not state.</p>"
    )
    return _page(site, title=f"{short} seller fees ({site['year']}): what the platform publishes, and what it doesn't",
                 path=f"{schedule['platform']}.html", body=body,
                 description=f"{short} seller fees as published by the platform, with the exact sentences quoted.")


def render_take(site: dict, schedule: dict, verification: dict) -> str:
    _status, kind, text = status_for(schedule, verification)
    short = schedule["name"].split(" (")[0]
    rows = engine.standard_table(schedule)
    hundred = next((r for r in rows if r["sale"] == 100), rows[0])
    table = "".join(
        f"<tr><td>{_money(r['sale'])}</td><td class=\"num\">{_money(r['fees'])}</td><td class=\"num\">{_money(r['net'])}</td><td class=\"num\">{r['rate']:.2f}%</td></tr>"
        for r in rows
    )
    quotes = "".join(f"<li>{_esc(q)}</li>" for q in schedule.get("quotes", [])[:8])
    sources_html = " · ".join(
        f'<a href="{_esc(src["url"])}" rel="noopener">{_esc(src["title"])}</a>' for src in schedule["sources"]
    )
    body = (
        f"<h1>How much does {_esc(short)} take?</h1>"
        f"<p class=\"muted small\">An independent calculator, not {_esc(short)}: nothing to sign in to and nothing to pay here. Enter a sale amount and read the fees.</p>"
        f"<p class=\"lede\">On a {_money(hundred['sale'])} sale with the default settings, {_esc(short)} takes "
        f"<strong>{_money(hundred['fees'])}</strong> and you keep <strong>{_money(hundred['net'])}</strong> — an effective "
        f"rate of {hundred['rate']:.2f}%. {_esc(schedule.get('summary', ''))}</p>"
        f"<div class=\"status {kind}\">{_esc(text)}</div>"
        "<table><thead><tr><th>Sale</th><th class=\"num\">Fees</th><th class=\"num\">You keep</th><th class=\"num\">Effective rate</th></tr></thead>"
        f"<tbody>{table}</tbody></table>"
        f"<p><a href=\"{_esc(schedule['platform'])}\">Open the {_esc(short)} calculator →</a> for your own price, shipping, category and options.</p>"
        f"<h2>How the fee is built</h2><p>{_esc(schedule.get('structure', ''))}</p>"
        f"<h2>In {_esc(short)}'s own words</h2><ul>{quotes}</ul>"
        f"<p class=\"muted\">Source: {sources_html}.</p>"
        + partner_box(schedule)
    )
    title = f"How much does {short} take? {short} seller fees explained ({site['year']})"
    return _page(site, title=title, path=f"how-much-does-{schedule['platform']}-take.html", body=body,
                 description=f"What {short} takes from a sale, computed from its published fee schedule, with the fee page quoted.")


def render_comparison(site: dict, slug: str, title: str, schedules: list[dict], verification: dict) -> str:
    sales = engine.STANDARD_SALES
    head = "".join(f"<th class=\"num\">{_esc(s['name'].split(' (')[0])}</th>" for s in schedules)
    rows = ""
    tables = {s["platform"]: {r["sale"]: r for r in engine.standard_table(s, sales)} for s in schedules}
    for sale in sales:
        cells = "".join(
            f"<td class=\"num\">{_money(tables[s['platform']][sale]['fees'])}<br><span class=\"muted\">keep {_money(tables[s['platform']][sale]['net'])}</span></td>"
            for s in schedules
        )
        rows += f"<tr><td>{_money(sale)}</td>{cells}</tr>"
    statuses = "".join(
        f"<li>{_esc(s['name'].split(' (')[0])}: <span class=\"badge {status_for(s, verification)[1]}\">{_esc(status_for(s, verification)[2])}</span> — "
        f"<a href=\"../{_esc(s['platform'])}\">calculator</a></li>"
        for s in schedules
    )
    body = (
        f"<h1>{_esc(title)}</h1>"
        "<p class=\"lede\">The same sale run through each platform's published fee schedule with that "
        "platform's default settings (no store subscription, domestic buyer, no optional upgrades). "
        "Fees are what the platform takes; \"keep\" is what reaches you before shipping costs.</p>"
        f"<table><thead><tr><th>Sale</th>{head}</tr></thead><tbody>{rows}</tbody></table>"
        "<p class=\"muted\">Platforms differ in what the percentage applies to (item only, or item plus shipping "
        "and tax) and in who pays shipping; each calculator states its base. This table is arithmetic, "
        "not a recommendation.</p>"
        f"<h2>Verification status</h2><ul>{statuses}</ul>"
    )
    return _page(site, title=f"{title} ({site['year']}): the same sale on each, from the published schedules",
                 path=f"compare/{slug}.html", body=body,
                 description=f"{title}: identical sales computed from each platform's own published fee schedule.")


def render_compare_index(site: dict, available: list[tuple[str, str]]) -> str:
    items = "".join(f"<li><a href=\"compare/{_esc(slug)}\">{_esc(title)}</a></li>" for slug, title in available)
    body = (
        "<h1>Fee comparisons</h1><p class=\"lede\">The same sale on each platform, computed from the "
        "published schedules. Pages appear as their platforms are verified.</p>"
        f"<ul>{items or '<li>No comparison has all its platforms verified yet.</li>'}</ul>"
    )
    return _page(site, title="Compare seller fees across platforms", path="compare.html", body=body,
                 description="Side-by-side seller fees for the same sale, from each platform's published schedule.")


def verify_pages(pages: dict[str, str]) -> list[str]:
    problems = []
    for path, content in pages.items():
        if path.endswith(".html"):
            if DISCLOSURE[:40] not in content:
                problems.append(f"{path}: missing disclosure")
            if NOT_ADVICE[:30] not in content:
                problems.append(f"{path}: missing not-advice notice")
            if INDEPENDENCE[:24] not in content:
                problems.append(f"{path}: missing independence line")
            if path.startswith("compare/") or path == "index.html":
                for platform, partner in PARTNERS.items():
                    if partner["url"] in content:
                        problems.append(f"{path}: {platform} sponsored link on a comparison/index page")
    return problems


def build(base_url: str, generated_at: datetime | None = None) -> dict[str, str]:
    now = generated_at or datetime.now(UTC)
    site = {"base_url": base_url, "stamp": now.strftime("%Y-%m-%d %H:%M UTC"), "year": now.year}
    schedules = load_schedules()
    verification = load_verification()
    engine_js = (STATIC_DIR / "fees.js").read_text()
    ui_js = (STATIC_DIR / "calc-ui.js").read_text()
    pages = {
        "index.html": render_index(site, schedules, verification),
        "methodology.html": render_methodology(site),
        "about.html": render_about(site),
    }
    by_slug = {s["platform"]: s for s in schedules}
    for s in schedules:
        pages[f"{s['platform']}.html"] = render_platform(site, s, verification, engine_js, ui_js)
        if not s.get("no_calculator"):
            pages[f"how-much-does-{s['platform']}-take.html"] = render_take(site, s, verification)
    available = []
    for slug, title, members in COMPARISONS:
        present = [by_slug[m] for m in members if m in by_slug and not by_slug[m].get("no_calculator")]
        if len(present) >= 2:
            pages[f"compare/{slug}.html"] = render_comparison(site, slug, title, present, verification)
            available.append((slug, title))
    pages["compare.html"] = render_compare_index(site, available)
    base = base_url.rstrip("/")
    pages["sitemap.xml"] = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
        + "".join(f"  <url><loc>{_esc(base)}/{_esc(_href(p))}</loc><lastmod>{now.date().isoformat()}</lastmod></url>\n" for p in pages if p.endswith(".html"))
        + "</urlset>\n"
    )
    pages["robots.txt"] = f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"
    pages[f"{INDEXNOW_KEY}.txt"] = INDEXNOW_KEY + "\n"
    problems = verify_pages(pages)
    if problems:
        raise ValueError("guardrails failed: " + "; ".join(problems))
    return pages


def write(pages: dict[str, str], out_dir: Path) -> int:
    for path, content in pages.items():
        target = out_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return len(pages)
