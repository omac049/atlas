"""Static site generator for the demand test in docs/IDEATION.md.

Turns what Atlas already computes — cross-venue twin pairs, the deterministic
verifier's verdict on each, the venues' own fee formulas, settlement-timing
annotations, archived rules text, and live quotes — into a static HTML site:
one factual comparison page per pair, plus pillar pages whose every claim
carries a source and a date.

Read this before adding a page:

- Nothing on any page is an opinion, a pick, or advice. Every page carries the
  affiliate disclosure and the not-advice notice; a page missing either is a
  build failure, not a style choice.
- A pair is called "the same bet" ONLY when the deterministic verifier says
  APPROVED_EQUIVALENT or APPROVED_INVERSE. Everything else is "not verified as
  the same bet" with the venue-published reasons listed. The site can never be
  more confident than the verifier.
- Pillar content is data in this module — sourced, dated, conservative — so
  the generator, not a copywriter, is the single owner of what the site says.
  The state-by-state legal table is data too (docs/site/legal-states.json) and
  a row claiming state action without a source fails the build.
- The generator is a read-only consumer of the pipeline. Nothing here is
  imported by verification, settlement, or normalization.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from atlas.gap_radar import kalshi_taker_fee_per_contract
from atlas.intel import _BLOCKER_PROSE

SITE_VERSION = "0.2"
SITE_NAME = "Same bet or not?"
TRUSTED_STATUSES = {"APPROVED_EQUIVALENT", "APPROVED_INVERSE"}
REPO_URL = "https://github.com/omac049/atlas"
# IndexNow key: public by design (it is served at /{key}.txt so Bing can verify
# the site owns submissions). Not a credential.
INDEXNOW_KEY = "9c1f2a7e4b8d4e6f8a0b1c2d3e4f5a6b"

# Verified 2026-09-04 against the venues' published schedules; the calculator
# page cites both. Kalshi's per-contract ceiling is the conservative reading
# already used by atlas/gap_radar.py.
KALSHI_TAKER_RATE = Decimal("0.07")
KALSHI_MAKER_SHARE = Decimal("0.25")
POLYMARKET_US_TAKER_RATE = Decimal("0.06")
POLYMARKET_US_MAKER_RATE = Decimal("0.0125")

NO_STATE_ACTION = "No public state action reported"

DISCLOSURE = (
    "Disclosure: this site may earn referral or affiliate revenue when you open an "
    "account through a link here. That never changes what a page says — every "
    "comparison is generated from the venues' own published rules and prices, and "
    "the method is public."
)
NOT_ADVICE = (
    "Nothing here is financial, legal, or tax advice, and nothing here is a "
    "recommendation to trade. These pages describe what two venues publish; they do "
    "not tell you what to do with it."
)

# Plain-English reasons a pair is NOT verified as the same bet. Reuses the
# intel report's glossary and adds the codes the verifier emits that the
# report never needed to explain.
REASON_PROSE: dict[str, str] = {
    **_BLOCKER_PROSE,
    "THRESHOLD_MISMATCH": "the two contracts use different numeric thresholds",
    "THRESHOLD_OPERATOR_MISMATCH": (
        "one contract pays on 'above' and the other on 'at or below' the same number"
    ),
    "THRESHOLD_UNIT_MISMATCH": "the contracts measure the number in different units",
    "CONTRACT_SCOPE_MISMATCH": "the contracts cover different scopes of the same subject",
    "AFFIRMATIVE_OUTCOME_MISMATCH": "the contracts' YES sides describe different outcomes",
    "SIGNED_LINE_MISMATCH": "the contracts sit on different lines",
    "SETTLEMENT_TIMING_ASYMMETRIC": (
        "one venue may settle well before the other, so a paired position is not "
        "truly locked until both have settled"
    ),
    "NON_GUARANTEED_SETTLEMENT": (
        "at least one venue reserves discretionary settlement, so no deterministic "
        "guarantee is possible"
    ),
}

# Event families as Atlas keys them -> the words a reader would use. Unknown
# families fall back to a humanized key, never to the raw one.
FAMILY_LABELS: dict[str, str] = {
    "us_fomc_rate_decision": "Fed rate decisions",
    "us_cpi_yoy": "CPI inflation, year over year",
    "us_cpi_core_mom": "Core CPI, month over month",
    "us_cpi_mom": "CPI, month over month",
    "us_real_gdp_growth": "Real GDP growth",
    "us_ism_manufacturing_pmi": "ISM Manufacturing PMI",
    "us_unemployment_rate": "Unemployment rate",
    "us_nonfarm_payrolls": "Nonfarm payrolls",
    "us_senate_control": "Senate control 2026",
    "us_house_control": "House control 2026",
}

VENUE_LABELS = {
    "kalshi": "Kalshi",
    "polymarket_us": "Polymarket US",
    "polymarket_global": "Polymarket (global)",
}


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:120] or "page"


def family_label(event_subject: str) -> str:
    key = str(event_subject or "").split("|")[0]
    return FAMILY_LABELS.get(key) or key.replace("_", " ").strip().capitalize() or "Other"


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _clean_title(value: object) -> str:
    return re.sub(r"\*\*", "", str(value or "")).strip()


def _dec(value: object) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None and str(value) != "" else None
    except (ArithmeticError, ValueError, TypeError):
        return None


def _cents(value: object) -> str:
    dec = _dec(value)
    return f"{dec * 100:.1f}¢" if dec is not None else "—"


def _price(value: object) -> str:
    dec = _dec(value)
    return f"{dec * 100:.0f}¢" if dec is not None else "—"


def taker_fee_at(venue: str, price: Decimal | None) -> Decimal | None:
    """Per-contract taker fee at a YES price, from the venue's published formula."""
    if price is None or not (0 < price < 1):
        return None
    if venue == "kalshi":
        return kalshi_taker_fee_per_contract(price)
    if venue == "polymarket_us":
        return POLYMARKET_US_TAKER_RATE * price * (1 - price)
    return None


@dataclass
class PairPage:
    """One comparison page, built from a radar observation plus optional
    archived rules text, clarity grades, and live quotes for each leg.

    A quote is {"yes_ask": .., "yes_bid": .., "last": .., "url": ..}; every
    field optional. Missing quote = no price shown, never a guessed one."""

    observation: dict
    kalshi_rules: str | None = None
    polymarket_rules: str | None = None
    kalshi_grade: dict | None = None
    polymarket_grade: dict | None = None
    kalshi_quote: dict | None = None
    polymarket_quote: dict | None = None

    @property
    def slug(self) -> str:
        obs = self.observation
        return slugify(
            f"{obs.get('event_subject', 'pair')}--{obs.get('kalshi_market_id', '')}"
            f"--{obs.get('polymarket_market_id', '')}"
        )

    @property
    def same_bet(self) -> bool:
        return str(self.observation.get("verification_status")) in TRUSTED_STATUSES

    @property
    def tradeable(self) -> bool:
        return bool(self.observation.get("tradeable_venue_pair"))

    @property
    def polymarket_venue(self) -> str:
        return str(self.observation.get("polymarket_venue") or "polymarket_global")

    @property
    def family(self) -> str:
        return family_label(str(self.observation.get("event_subject") or ""))

    @property
    def reasons(self) -> list[tuple[str, str]]:
        codes = list(self.observation.get("mismatch_codes") or [])
        timing = self.observation.get("settlement_timing") or {}
        if timing.get("asymmetric") and "SETTLEMENT_TIMING_ASYMMETRIC" not in codes:
            codes.append("SETTLEMENT_TIMING_ASYMMETRIC")
        return [(code, REASON_PROSE.get(code, code.replace("_", " ").lower())) for code in codes]

    @property
    def headline(self) -> str:
        # Kalshi titles are already plain questions; the subject key is not.
        head = _clean_title(self.observation.get("kalshi_title")).rstrip("?")
        return head or family_label(str(self.observation.get("event_subject") or ""))

    @property
    def title(self) -> str:
        return f"Kalshi vs Polymarket: {self.headline}"

    def human_summary(self) -> str:
        """Deterministic prose composed from the verifier's codes and each leg's
        fine-print findings. Templates, never a model."""
        if self.same_bet:
            return (
                "Both venues' published terms resolve to the same outcome in every case the "
                "checker examines: same subject, same threshold and direction, same settlement "
                "source, same handling of revisions and cancellations. Prices can still differ "
                "between the two, and fees always do."
            )
        parts = [prose for _, prose in self.reasons]
        text = "Where they differ: " + "; ".join(parts) + "." if parts else (
            "The checker could not confirm equivalence from the published text."
        )
        for label, grade in (("Kalshi", self.kalshi_grade), ("Polymarket", self.polymarket_grade)):
            findings = [f["prose"] for f in (grade or {}).get("findings", []) if f.get("points")]
            if findings:
                text += f" {label}'s fine print: " + "; ".join(findings) + "."
        text += (
            " In an ordinary outcome both contracts pay the same. The difference shows up only "
            "in the unusual case those gaps describe — a delayed release, a canceled event, a "
            "revised number — which is exactly when a paired position stops being one bet."
        )
        return text


@dataclass
class Site:
    base_url: str
    generated_at: datetime
    pairs: list[PairPage] = field(default_factory=list)
    # A GA4 measurement id (G-XXXXXXX). None = no analytics tag at all; the
    # demand test counts social clicks, so measurement is opt-in by the owner.
    analytics_id: str | None = None
    # docs/site/legal-states.json, loaded by the CLI; None renders the short
    # federal-picture version of the legal page.
    legal_states: dict | None = None
    # docs/site/kalshi-vs-polymarket.json: the head-term page's rows and FAQ,
    # every row sourced; None renders the short version that links the pillars.
    comparison: dict | None = None

    @property
    def stamp(self) -> str:
        return self.generated_at.strftime("%Y-%m-%d %H:%M UTC")


# --- page chrome -----------------------------------------------------------

_CSS = """
:root{--ink:#17181a;--muted:#5f6368;--line:#e6e6e8;--soft:#f6f6f7;--ok:#146c3a;--ok-bg:#e6f4ea;
--warn:#8a4b00;--warn-bg:#fdf1e3;--accent:#1d4ed8;--bg:#fff}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,
sans-serif;color:var(--ink);background:var(--bg)}
a{color:var(--accent)}main{max-width:880px;margin:0 auto;padding:28px 20px 64px}
header{border-bottom:1px solid var(--line)}
.top{max-width:880px;margin:0 auto;padding:14px 20px;display:flex;flex-wrap:wrap;gap:8px 18px;
align-items:baseline}
.brand{font-weight:800;font-size:1.05rem;letter-spacing:-.01em;color:var(--ink);
text-decoration:none;margin-right:auto}.brand span{color:var(--accent)}
.top nav{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:14px}.top nav a{color:var(--ink);
text-decoration:none;padding:2px 0;border-bottom:2px solid transparent}
.top nav a:hover{border-color:var(--accent)}
h1{font-size:1.85rem;line-height:1.2;letter-spacing:-.015em;margin:.2em 0 .4em}
h2{font-size:1.2rem;margin:1.8em 0 .5em}h3{font-size:1rem;margin:1.4em 0 .4em}
p{margin:.55em 0}.lede{font-size:1.08rem;color:#333}.muted{color:var(--muted);font-size:14px}
table{border-collapse:collapse;width:100%;margin:.7em 0;font-size:15px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;background:var(--soft)}tbody tr:hover{background:#fafafa}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12.5px;font-weight:600;
white-space:nowrap}.badge.ok{background:var(--ok-bg);color:var(--ok)}
.badge.no{background:var(--warn-bg);color:var(--warn)}.badge.dim{background:var(--soft);
color:var(--muted);font-weight:500}
.verdict{padding:16px 18px;border-radius:10px;margin:1em 0;font-weight:600;line-height:1.45}
.verdict.ok{background:var(--ok-bg);color:var(--ok)}.verdict.no{background:var(--warn-bg);
color:var(--warn)}
.summary{background:var(--soft);border-radius:10px;padding:14px 18px;margin:1em 0}
blockquote{margin:.6em 0;padding:.7em 1em;border-left:3px solid var(--line);color:#333;
font-size:14px;white-space:pre-wrap;background:#fcfcfc}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;
margin:1em 0}.card{border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card a{font-weight:600;text-decoration:none}.card p{margin:.3em 0 0;font-size:14px;
color:var(--muted)}
footer{max-width:880px;margin:0 auto;padding:22px 20px;border-top:1px solid var(--line);
font-size:13px;color:var(--muted)}
input[type=number]{font-size:16px;padding:7px 9px;width:120px;border:1px solid #bbb;
border-radius:6px}.calc{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:1em 0}
@media(max-width:600px){.calc{grid-template-columns:1fr}h1{font-size:1.5rem}}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;
background:var(--soft);color:#333;margin-left:6px}.ext::after{content:" ↗";font-size:.85em}
"""

_FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
    "%3Crect width='32' height='32' rx='7' fill='%231d4ed8'/%3E"
    "%3Ccircle cx='12' cy='16' r='6.5' fill='none' stroke='%23fff' stroke-width='2.5'/%3E"
    "%3Ccircle cx='20' cy='16' r='6.5' fill='none' stroke='%23fff' stroke-width='2.5'/%3E"
    "%3C/svg%3E"
)

_GA4_ID = re.compile(r"G-[A-Z0-9]{6,12}")
_METHODOLOGY_LINK = re.compile(r'href="(\.\./)*methodology"')

_NAV = (
    ("index.html", "Compare"),
    ("kalshi-vs-polymarket.html", "Kalshi vs Polymarket"),
    ("fees.html", "Fees"),
    ("same-bet.html", "Same bet?"),
    ("legal.html", "Legal"),
    ("taxes.html", "Taxes"),
    ("referrals.html", "Referrals"),
    ("about.html", "About"),
)


def _href(path: str) -> str:
    """Files are written as .html; links and canonicals are extensionless.

    Static hosts (GitHub Pages, Cloudflare Pages) serve `/legal` for
    legal.html and redirect `/legal.html` to it, so a link carrying the
    extension costs a redirect on every click and a "page with redirect" note
    in Search Console.
    """
    if path == "index.html":
        return ""
    return path.removesuffix(".html")


def _page(site: Site, *, title: str, path: str, body: str, description: str) -> str:
    depth = path.count("/")
    root = "../" * depth
    nav = "".join(
        f'<a href="{root}{_href(href) or "./"}">{_esc(label)}</a>' for href, label in _NAV
    )
    canonical = f"{site.base_url.rstrip('/')}/{_href(path)}"
    analytics = ""
    if site.analytics_id and _GA4_ID.fullmatch(site.analytics_id):
        gid = site.analytics_id
        analytics = (
            f"<script async src=\"https://www.googletagmanager.com/gtag/js?id={gid}\"></script>"
            "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
            f"gtag('js',new Date());gtag('config','{gid}',{{'anonymize_ip':true}});</script>"
        )
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_esc(title)}</title>"
        f"<meta name=\"description\" content=\"{_esc(description)}\">"
        f"<link rel=\"canonical\" href=\"{_esc(canonical)}\">"
        f"<link rel=\"icon\" href=\"{_FAVICON}\">"
        f"<meta property=\"og:title\" content=\"{_esc(title)}\">"
        f"<meta property=\"og:description\" content=\"{_esc(description)}\">"
        f"<meta property=\"og:url\" content=\"{_esc(canonical)}\">"
        f"<meta property=\"og:site_name\" content=\"{_esc(SITE_NAME)}\">"
        f"<style>{_CSS}</style>{analytics}</head><body>"
        f"<header><div class=\"top\"><a class=\"brand\" href=\"{root or './'}\">Same bet "
        f"<span>or not?</span></a><nav>{nav}</nav></div></header><main>{body}</main>"
        f"<footer><p>{_esc(DISCLOSURE)}</p><p>{_esc(NOT_ADVICE)}</p>"
        f"<p>Generated {_esc(site.stamp)} from published venue data. "
        f"<a href=\"{root}methodology\">How these pages are made</a> · "
        f"<a href=\"{root}about\">About</a></p></footer>"
        "</body></html>\n"
    )


# --- pair pages ------------------------------------------------------------

def _leg_row(venue: str, obs_title: object, market_id: object, quote: dict | None) -> str:
    quote = quote or {}
    price = _dec(quote.get("yes_ask")) or _dec(quote.get("last"))
    fee = taker_fee_at(venue, price)
    label = VENUE_LABELS.get(venue, venue)
    link = ""
    if quote.get("url"):
        link = (
            f"<div><a class=\"ext\" href=\"{_esc(quote['url'])}\" rel=\"noopener\" "
            f"target=\"_blank\">Open on {_esc(label)}</a></div>"
        )
    fee_text = _cents(fee) if fee is not None else ("set per market" if venue == "polymarket_global" and price else "—")
    return (
        f"<tr><td>{_esc(label)}</td><td>{_esc(_clean_title(obs_title))}"
        f"<div class=\"muted\">{_esc(market_id)}</div>{link}</td>"
        f"<td>{_price(price)}</td><td>{fee_text}</td></tr>"
    )


def _leg_table(page: PairPage) -> str:
    obs = page.observation
    return (
        "<table><thead><tr><th>Venue</th><th>Contract</th><th>YES price</th>"
        "<th>Taker fee at that price</th></tr></thead><tbody>"
        + _leg_row("kalshi", obs.get("kalshi_title"), obs.get("kalshi_market_id"), page.kalshi_quote)
        + _leg_row(
            page.polymarket_venue,
            obs.get("polymarket_title"),
            obs.get("polymarket_market_id"),
            page.polymarket_quote,
        )
        + "</tbody></table>"
    )


def _grade_line(label: str, grade: dict | None) -> str:
    if not grade:
        return ""
    return (
        f"<p><strong>{_esc(label)} fine-print grade:</strong> {_esc(grade.get('grade'))} "
        f"({_esc(grade.get('score'))}/100)"
        + "".join(
            f"<br><span class=\"muted\">−{_esc(f['points'])} {_esc(f['code'])}: {_esc(f['prose'])}</span>"
            for f in grade.get("findings", [])
            if f.get("points")
        )
        + "</p>"
    )


def render_pair(site: Site, page: PairPage) -> str:
    obs = page.observation
    if page.same_bet:
        verdict = (
            "<div class=\"verdict ok\">Verified as the same bet by deterministic rule "
            "comparison — both venues' published terms resolve to the same economic outcome.</div>"
        )
    else:
        items = "".join(
            f"<li><code>{_esc(code)}</code> — {_esc(prose)}</li>" for code, prose in page.reasons
        )
        verdict = (
            "<div class=\"verdict no\">Not verified as the same bet. The venues' published "
            "terms differ in ways that could settle differently.</div>"
            f"<ul>{items or '<li>the verifier could not confirm equivalence from the published text</li>'}</ul>"
        )
    timing = obs.get("settlement_timing") or {}
    timing_html = ""
    if timing.get("days_to_settlement"):
        timing_html = (
            f"<p><strong>Capital lock-up:</strong> about {_esc(timing['days_to_settlement'])} days "
            "until the later of the two contracts settles."
            + (
                f" <span class=\"pill\">early settlement possible on {_esc(timing.get('early_venue'))}</span>"
                if timing.get("asymmetric")
                else ""
            )
            + "</p>"
        )
    rules = ""
    if page.kalshi_rules:
        rules += f"<h2>What Kalshi publishes</h2><blockquote>{_esc(page.kalshi_rules[:1500])}</blockquote>"
    if page.polymarket_rules:
        rules += (
            f"<h2>What Polymarket publishes</h2><blockquote>{_esc(page.polymarket_rules[:1500])}</blockquote>"
        )
    tradeable_note = (
        ""
        if page.tradeable
        else "<p class=\"muted\">The Polymarket leg here is the global venue, which US accounts "
        "cannot trade; it is shown for the rules comparison only.</p>"
    )
    body = (
        f"<p class=\"muted\">{_esc(page.family)}</p>"
        f"<h1>{_esc(page.title)}</h1>"
        f"<p class=\"muted\">Last checked {_esc(str(obs.get('observed_at', ''))[:16].replace('T', ' '))} UTC. "
        "Prices move constantly; the rules comparison is the durable part.</p>"
        + verdict
        + f"<div class=\"summary\"><strong>In plain words.</strong> {_esc(page.human_summary())}</div>"
        + tradeable_note
        + _leg_table(page)
        + "<p class=\"muted\">YES price is the best ask at the last check. Fees use each venue's "
        "published taker formula at that price — see the <a href=\"../fees\">fee calculator</a> "
        "for any price.</p>"
        + timing_html
        + _grade_line("Kalshi", page.kalshi_grade)
        + _grade_line("Polymarket", page.polymarket_grade)
        + rules
    )
    description = (
        f"{'Verified the same bet' if page.same_bet else 'Not verified as the same bet'}: "
        f"{_clean_title(obs.get('kalshi_title'))} vs {_clean_title(obs.get('polymarket_title'))}."
    )
    return _page(site, title=page.title, path=f"compare/{page.slug}.html", body=body, description=description)


# --- pillar pages ----------------------------------------------------------

def _sources(items: list[tuple[str, str]]) -> str:
    return "<h2>Sources</h2><ul>" + "".join(
        f"<li><a href=\"{_esc(url)}\" rel=\"nofollow noopener\">{_esc(label)}</a></li>"
        for label, url in items
    ) + "</ul>"


def _pair_row(p: PairPage) -> str:
    badge = (
        "<span class=\"badge ok\">Same bet ✓</span>"
        if p.same_bet
        else "<span class=\"badge no\">Not verified</span>"
    )
    where = "US" if p.tradeable else "<span class=\"badge dim\">global only</span>"
    return (
        f"<tr><td><a href=\"compare/{p.slug}\">{_esc(p.headline)}</a></td>"
        f"<td>{badge}</td><td>{where}</td></tr>"
    )


def render_index(site: Site) -> str:
    same = [p for p in site.pairs if p.same_bet]
    families: dict[str, list[PairPage]] = {}
    for p in sorted(site.pairs, key=lambda p: (p.family, not p.tradeable, p.headline)):
        families.setdefault(p.family, []).append(p)
    sections = ""
    if same:
        sections += (
            "<h2>Verified: the same bet on both venues</h2>"
            "<table><thead><tr><th>Pair</th><th>Verdict</th><th>Tradeable</th></tr></thead>"
            f"<tbody>{''.join(_pair_row(p) for p in sorted(same, key=lambda p: p.headline))}</tbody></table>"
        )
    for fam, pairs in families.items():
        sections += (
            f"<h2>{_esc(fam)}</h2>"
            "<table><thead><tr><th>Pair</th><th>Verdict</th><th>Tradeable</th></tr></thead>"
            f"<tbody>{''.join(_pair_row(p) for p in pairs)}</tbody></table>"
        )
    body = (
        "<h1>Is it the same bet on Kalshi and Polymarket?</h1>"
        "<p class=\"lede\">The same event is often listed on both venues. Whether it is the "
        "<em>same bet</em> depends on the fine print — the settlement source, what happens if "
        "the data is late, how the number is rounded. Every page here compares the two venues' "
        "own published terms for one matched pair, using a deterministic rule check, and shows "
        "the live price and fee on each side.</p>"
        f"<p>Tracking <strong>{len(site.pairs)} pairs</strong> right now; <strong>{len(same)}</strong> "
        "verify as the same bet. No picks, no predictions — just what each venue says it will do.</p>"
        "<div class=\"cards\">"
        "<div class=\"card\"><a href=\"kalshi-vs-polymarket\">Kalshi vs Polymarket, side by side</a>"
        "<p>Regulation, funding, fees, settlement, taxes — sourced, no opinions.</p></div>"
        "<div class=\"card\"><a href=\"fees\">Fee calculator</a><p>Both venues' exact published "
        "formulas at any price.</p></div>"
        "<div class=\"card\"><a href=\"same-bet\">Why \"same event\" isn't \"same bet\"</a>"
        "<p>Three real cases where identical-looking contracts paid differently.</p></div>"
        "<div class=\"card\"><a href=\"legal\">Legal status by state</a><p>Court orders, "
        "cease-and-desists, and rulings, sourced.</p></div>"
        "<div class=\"card\"><a href=\"taxes\">Taxes</a><p>What forms each venue sends, and "
        "what practitioners say about reporting.</p></div>"
        "</div>"
        + sections
    )
    return _page(site, title="Kalshi vs Polymarket — same bet or not, contract by contract",
                 path="index.html", body=body,
                 description="Contract-by-contract comparison of Kalshi and Polymarket using "
                 "each venue's published rules, with live prices and exact fees.")


def render_fees(site: Site) -> str:
    rows = "".join(
        f"<tr><td>{p}¢</td><td>{_cents(kalshi_taker_fee_per_contract(Decimal(p) / 100))}</td>"
        f"<td>{_cents(POLYMARKET_US_TAKER_RATE * Decimal(p) / 100 * (1 - Decimal(p) / 100))}</td></tr>"
        for p in (5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95)
    )
    calc_js = """
function fee(){var p=Math.min(99,Math.max(1,Number(document.getElementById('p').value)))/100;
var n=Math.max(1,Number(document.getElementById('n').value));
var k=Math.ceil(0.07*p*(1-p)*100)/100;var pm=0.06*p*(1-p);
document.getElementById('k').textContent='$'+(k*n).toFixed(2)+' ('+(k*100).toFixed(2)+'¢ each)';
document.getElementById('km').textContent='$'+(k*0.25*n).toFixed(2);
document.getElementById('pm').textContent='$'+(pm*n).toFixed(2)+' ('+(pm*100).toFixed(2)+'¢ each)';
document.getElementById('pmm').textContent='$'+(0.0125*p*(1-p)*n).toFixed(2);}
document.addEventListener('DOMContentLoaded',function(){fee();['p','n'].forEach(function(i){
document.getElementById(i).addEventListener('input',fee);});});
"""
    body = (
        "<h1>Kalshi vs Polymarket fees, at your price</h1>"
        "<p class=\"lede\">Both venues charge more when a contract is near 50¢ and almost nothing "
        "near the extremes — the fee is a curve, not a flat rate. The headline numbers (\"7%\" vs "
        "\"6%\") are the coefficients in that curve. Enter a price and a size:</p>"
        "<div class=\"calc\"><label>Price (¢) <input id=\"p\" type=\"number\" min=\"1\" max=\"99\" "
        "value=\"50\"></label><label>Contracts <input id=\"n\" type=\"number\" min=\"1\" "
        "value=\"100\"></label></div>"
        "<table><thead><tr><th></th><th>Kalshi</th><th>Polymarket US</th></tr></thead><tbody>"
        "<tr><th>Taker fee (you take the displayed price)</th><td id=\"k\"></td><td id=\"pm\"></td></tr>"
        "<tr><th>Maker fee (your resting order fills)</th><td id=\"km\"></td><td id=\"pmm\"></td></tr>"
        "</tbody></table>"
        f"<script>{calc_js}</script>"
        "<h2>The formulas, as published</h2>"
        "<ul><li><strong>Kalshi taker:</strong> 0.07 × price × (1 − price) per contract, rounded up "
        "to the cent per contract here (the venue rounds per order, so this is the conservative "
        "reading). Maker fee is 25% of the taker fee. A higher multiplier applies to some premium "
        "categories such as crypto.</li>"
        "<li><strong>Polymarket US taker:</strong> 0.06 × price × (1 − price) per share, per the "
        "schedule effective July 1, 2026. Maker fee coefficient 0.0125.</li>"
        "<li><strong>Polymarket (global):</strong> fees are set per market and published on each "
        "market; economics markets have carried a 0.05 taker rate. Not available to US accounts.</li></ul>"
        "<h2>At a glance</h2><table><thead><tr><th>Price</th><th>Kalshi taker</th>"
        f"<th>Polymarket US taker</th></tr></thead><tbody>{rows}</tbody></table>"
        "<p class=\"muted\">Both peak at 50¢: 1.75¢ (Kalshi, shown rounded up to 2¢ per contract) vs "
        "1.5¢ (Polymarket US). At 90¢ they are 0.63¢ (rounded to 1¢) and 0.54¢. "
        "Deposits, withdrawals, and any crypto on-ramp costs are separate and not modeled here.</p>"
        + _sources([
            ("Kalshi Help Center — Fees", "https://help.kalshi.com/en/articles/13823805-fees"),
            ("Kalshi fee schedule (July 2026 PDF)", "https://kalshi.com/docs/kalshi-fee-schedule.pdf"),
            ("Polymarket US — Fee Schedule", "https://docs.polymarket.us/fees"),
        ])
    )
    return _page(site, title="Kalshi vs Polymarket fees calculator (exact published formulas)",
                 path="fees.html", body=body,
                 description="Exact Kalshi and Polymarket US taker and maker fees at any price, "
                 "from the venues' published formulas.")


def render_same_bet(site: Site) -> str:
    body = (
        "<h1>Same event, same bet? Usually not quite.</h1>"
        "<p class=\"lede\">Two contracts can share a headline and still pay out differently. "
        "Three real cases:</p>"
        "<h2>1. Cardi B at the Super Bowl (February 2026)</h2>"
        "<p>Both venues listed whether Cardi B would perform at halftime. She appeared in another "
        "artist's set. Polymarket resolved <strong>YES</strong>. Kalshi ruled the question "
        "unresolvable and settled at the last traded price — about 26¢. Same facts, opposite "
        "economics, because the two rulebooks define \"perform\" differently.</p>"
        "<h2>2. CPI when the data is late</h2>"
        "<p>Polymarket's CPI contracts publish exactly what happens if the Bureau of Labor "
        "Statistics delays a release (fall back to the previous month within a window). Kalshi's "
        "matching contracts publish no such branch. Most months this never matters. In a delay, "
        "the two would settle by different rules.</p>"
        "<h2>3. Fed decisions and rounding</h2>"
        "<p>Polymarket US's Fed-decision buckets publish a rounding rule for moves that don't land "
        "on a listed size; Kalshi's do not. An unusual move — an intermeeting cut, an odd increment "
        "— is where those texts diverge.</p>"
        "<h2>How the verdict on each page is reached</h2>"
        "<p>A deterministic rule comparison extracts the economic terms from each venue's published "
        "text — subject, threshold, direction, settlement source, revision and cancellation policy — "
        "and compares them field by field. A pair is called the same bet only when every term "
        "matches and both venues' settlement terms are complete. Anything less is listed with the "
        "specific published differences. No model guesses; a lexical match on titles is never "
        "enough.</p>"
        "<p><a href=\"./\">See every pair we track →</a></p>"
    )
    return _page(site, title="Is it the same bet on Kalshi and Polymarket?", path="same-bet.html",
                 body=body, description="Why identical-looking Kalshi and Polymarket contracts can "
                 "settle differently, with real cases.")


_LEGAL_BADGE = {
    NO_STATE_ACTION: "dim",
    "Court ruling favoring the venue": "ok",
}


def _legal_states_table(data: dict) -> str:
    rows = ""
    for row in sorted(data.get("states", []), key=lambda r: str(r.get("state"))):
        status = str(row.get("status") or NO_STATE_ACTION)
        kind = _LEGAL_BADGE.get(status, "no")
        cats = ", ".join(str(c) for c in row.get("categories") or [])
        srcs = " ".join(
            f"<a href=\"{_esc(u)}\" rel=\"nofollow noopener\">[{i + 1}]</a>"
            for i, u in enumerate(row.get("sources") or [])
        )
        rows += (
            f"<tr><td><strong>{_esc(row.get('state'))}</strong></td>"
            f"<td><span class=\"badge {kind}\">{_esc(status)}</span></td>"
            f"<td>{_esc(row.get('summary') or '')}"
            + (f"<div class=\"muted\">Affects: {_esc(cats)}</div>" if cats else "")
            + (f"<div class=\"muted\">Sources: {srcs}</div>" if srcs else "")
            + "</td></tr>"
        )
    return (
        "<table><thead><tr><th>State</th><th>Status</th><th>What has happened</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def render_legal(site: Site) -> str:
    data = site.legal_states or {}
    as_of = str(data.get("as_of") or "September 2026")
    actions = [r for r in data.get("states", []) if str(r.get("status")) != NO_STATE_ACTION]
    if data.get("states"):
        table = (
            f"<h2>All 50 states and DC (as of {_esc(as_of)})</h2>"
            f"<p>{len(actions)} states have taken some public action; the rest have none reported. "
            "\"None reported\" means we found no regulator letter, suit, or court order — not that "
            "a state has approved anything.</p>"
            + _legal_states_table(data)
        )
    else:
        table = (
            "<h2>Where states have pushed back on sports contracts</h2>"
            "<table><thead><tr><th>State</th><th>What has happened</th></tr></thead><tbody>"
            "<tr><td>New York</td><td>The Attorney General filed a civil suit calling Kalshi's sports "
            "contracts illegal gambling.</td></tr>"
            "<tr><td>Massachusetts</td><td>A Superior Court judge issued a preliminary injunction "
            "barring in-state sports contracts without a gaming license (January 2026).</td></tr>"
            "<tr><td>Nevada</td><td>A temporary restraining order covered sports, election, and "
            "entertainment contracts; Kalshi removed those categories for Nevada users.</td></tr>"
            "<tr><td>New Jersey</td><td>A federal appellate court held the state cannot enforce its "
            "sports-betting laws against Kalshi while the case proceeds.</td></tr>"
            "</tbody></table>"
        )
    body = (
        f"<h1>Is Kalshi legal? Is Polymarket legal in the US? State by state, as of {_esc(as_of)}</h1>"
        "<p class=\"muted\">A factual tracker of public rulings and filings, not legal advice. "
        "This area changes month to month; every row links its sources.</p>"
        "<h2>The federal picture</h2>"
        "<p>Kalshi is a CFTC-regulated exchange. Polymarket US operates a separately regulated US "
        "venue; Polymarket's original global platform is not available to US accounts. The core "
        "legal question is whether event contracts are federal \"swaps\" under the Commodity "
        "Exchange Act, which would preempt state gambling law. <strong>Federal appeals courts are "
        "now split.</strong> The Third Circuit sided with Kalshi in April 2026 (New Jersey cannot "
        "enforce its gambling laws against Kalshi's sports contracts); the Ninth Circuit sided with "
        "Nevada in August 2026. New Jersey asked the U.S. Supreme Court to take the case on "
        "September 2, 2026. Until that is resolved, the answer genuinely depends on the state. "
        "Sports contracts are the target in nearly every action; elections and entertainment "
        "contracts are named in several. Economic contracts have drawn the least state action.</p>"
        + table
        + _sources([
            ("Holland & Knight — Prediction Markets at a Crossroads (Feb 2026)",
             "https://www.hklaw.com/en/insights/publications/2026/02/prediction-markets-at-a-crossroads-the-continued-jurisdictional-battle"),
            ("JURIST — federal court lets Kalshi continue sports contracts during litigation (Apr 2026)",
             "https://www.jurist.org/news/2026/04/us-federal-court-rules-platform-kalshi-can-continue-offering-sport-event-contracts-during-litigation/"),
            ("Forbes — New York's suit and the New Jersey ruling (Aug 2026)",
             "https://www.forbes.com/sites/zennonkapron/2026/08/04/new-york-wants-36-billion-from-kalshi-a-federal-judge-next-door-just-shielded-it/"),
            ("Epstein Becker Green — Prediction Markets v. State Gaming Laws",
             "https://www.commerciallitigationupdate.com/prediction-markets-v-state-gaming-laws-the-kalshi-litigation-gamble"),
        ])
    )
    return _page(site, title="Is Kalshi legal? Is Polymarket legal in the US? State-by-state tracker",
                 path="legal.html", body=body,
                 description="Factual, sourced tracker of court rulings and state actions affecting "
                 "Kalshi and Polymarket in every US state. Not legal advice.")


def render_taxes(site: Site) -> str:
    body = (
        "<h1>Kalshi and Polymarket taxes: what the venues send, and what they don't</h1>"
        "<p class=\"muted\">Facts about reporting, not tax advice. The IRS has not issued formal "
        "guidance on event contracts; talk to a CPA who knows trader taxation.</p>"
        "<h2>What forms you get</h2>"
        "<table><thead><tr><th>Venue</th><th>Forms reported by tax practitioners</th></tr></thead>"
        "<tbody><tr><td>Kalshi</td><td>1099-INT for interest and 1099-MISC for rewards; no form "
        "for trading profit itself.</td></tr>"
        "<tr><td>Polymarket</td><td>No tax forms; the on-chain record is the audit trail.</td></tr>"
        "</tbody></table>"
        "<h2>What that means</h2>"
        "<p>The absence of a form does not remove the obligation to report. Practitioners describe "
        "exporting the full trade history, computing net proceeds minus cost, and reporting the "
        "result — with an open question about characterization (Section 1256 contracts, gambling "
        "income, or ordinary income), on which the profession has not settled. Pick one reasonable "
        "treatment, apply it consistently, and document it.</p>"
        + _sources([
            ("Keeper — filing taxes on Polymarket and Kalshi (2026)",
             "https://www.keepertax.com/posts/how-to-file-taxes-on-kalshi-and-polymarket"),
            ("NATP — prediction market contracts on client returns",
             "https://www.natptax.com/news-insights/blog/prediction-market-contracts-are-showing-up-on-client-returns/"),
            ("Monaco CPA — do you get a 1099?", "https://www.monacocpa.cpa/prediction-market-tax"),
        ])
    )
    return _page(site, title="Kalshi taxes and Polymarket taxes: forms, reporting, open questions",
                 path="taxes.html", body=body,
                 description="What tax forms Kalshi and Polymarket issue, and what practitioners "
                 "say about reporting. Not tax advice.")


def render_referrals(site: Site) -> str:
    body = (
        "<h1>Kalshi and Polymarket referral programs, compared honestly</h1>"
        "<p class=\"muted\">Terms verified 2026-09-04 from the programs' own pages. Venues change "
        "these at will; the sources are linked.</p>"
        "<table><thead><tr><th></th><th>Kalshi</th><th>Polymarket</th></tr></thead><tbody>"
        "<tr><th>What a referred user gets</th><td>Trading credits on completing the sign-up "
        "requirements ($25 has been the published amount).</td><td>Program-dependent; the "
        "affiliate program pays the referrer, not the user.</td></tr>"
        "<tr><th>What the referrer gets</th><td><strong>Trading credits, not cash.</strong> Credits "
        "can only be used to trade; only trading returns are withdrawable.</td>"
        "<td><strong>Cash.</strong> The partner program lists $10 per referral's first deposit, a "
        "20% share of their perpetuals trading fees, and $0.01 per click, with revenue bounties.</td></tr>"
        "<tr><th>Who can refer</th><td>Any account.</td><td>Approved affiliates; Polymarket US runs a "
        "separate approved-affiliate program by application.</td></tr>"
        "</tbody></table>"
        "<p>This site may participate in these programs — see the disclosure below. The comparison "
        "above is the same whether or not you use a link here.</p>"
        + _sources([
            ("Polymarket partner program (Dub)", "https://partners.dub.co/polymarket"),
            ("Polymarket US — Referral Incentive Program", "https://docs.polymarket.us/incentives/referral"),
            ("iGaming Future — Kalshi referral code, credits not cash",
             "https://igamingfuture.com/prediction-markets/news/kalshi-referral-code/"),
        ])
    )
    return _page(site, title="Kalshi referral code vs Polymarket referral code: what each actually pays",
                 path="referrals.html", body=body,
                 description="Kalshi pays referral rewards in trading credits; Polymarket's partner "
                 "program pays cash. Terms compared, with sources.")


def render_methodology(site: Site) -> str:
    body = (
        "<h1>How these pages are made</h1>"
        "<p>Every comparison page is generated by software that reads each venue's published "
        "contract rules and prices, extracts the economic terms deterministically, and compares "
        "them field by field. No page is written by hand, no model guesses at meaning, and a pair "
        "is called the same bet only when the published terms match on every field and both "
        "venues' settlement terms are complete.</p>"
        "<p>The system behind it is a paper-only research instrument. It has never placed a trade, "
        "and its research record — including four pre-registered hypotheses that each returned a "
        f"negative result — is public at <a href=\"{REPO_URL}\" rel=\"noopener\">{_esc(REPO_URL)}</a>. "
        "That record is the reason to trust a page that says \"not verified\": the instrument is "
        "built to say no.</p>"
        "<h2>What is and isn't here</h2>"
        "<ul><li>Facts about published rules, fees, prices, legal filings, and program terms — "
        "with sources and dates.</li><li>No picks, no probabilities, no strategies, no advice.</li>"
        "<li>Affiliate relationships, disclosed on every page, that never affect a verdict.</li></ul>"
        "<p>Prices shown are the best ask at the moment of the nightly rebuild. Fees are computed "
        "from each venue's published formula at that price, not copied from a screen.</p>"
    )
    return _page(site, title="How this site works", path="methodology.html", body=body,
                 description="How the Kalshi vs Polymarket comparison pages are generated, "
                 "and what they deliberately are not.")


def _faq_jsonld(faq: list[dict]) -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": str(item.get("q") or ""),
                "acceptedAnswer": {"@type": "Answer", "text": str(item.get("a") or "")},
            }
            for item in faq
            if item.get("q") and item.get("a")
        ],
    }
    # </script> inside JSON would end the tag early; escape the slash.
    return "<script type=\"application/ld+json\">" + json.dumps(data).replace("</", "<\\/") + "</script>"


def render_comparison(site: Site) -> str:
    data = site.comparison or {}
    as_of = str(data.get("as_of") or "September 2026")
    rows = data.get("rows") or []
    faq = data.get("faq") or []
    table = ""
    if rows:
        body_rows = ""
        for i, row in enumerate(rows):
            srcs = " ".join(
                f"<a href=\"{_esc(u)}\" rel=\"nofollow noopener\">[{j + 1}]</a>"
                for j, u in enumerate(row.get("sources") or [])
            )
            note = f"<div class=\"muted\">Note: {_esc(row['note'])}</div>" if row.get("note") else ""
            body_rows += (
                f"<tr><th scope=\"row\">{_esc(row.get('topic'))}"
                f"<div class=\"muted\">{srcs}</div></th>"
                f"<td>{_esc(row.get('kalshi') or 'Not published')}</td>"
                f"<td>{_esc(row.get('polymarket_us') or 'Not published')}</td>"
                f"<td>{_esc(row.get('polymarket_global') or 'Not published')}{note}</td></tr>"
            )
        table = (
            f"<h2>Side by side (as of {_esc(as_of)})</h2>"
            "<div style=\"overflow-x:auto\"><table><thead><tr><th></th><th>Kalshi</th>"
            "<th>Polymarket US</th><th>Polymarket (global)</th></tr></thead>"
            f"<tbody>{body_rows}</tbody></table></div>"
            "<p class=\"muted\">\"Not published\" means the venue does not state it in its "
            "public documentation; we do not fill gaps with guesses. Bracketed numbers link "
            "the source for that row.</p>"
        )
    faq_html = ""
    if faq:
        faq_html = "<h2>Questions people ask</h2>" + "".join(
            f"<h3>{_esc(item.get('q'))}</h3><p>{_esc(item.get('a'))}</p>" for item in faq
        )
    same = sum(1 for p in site.pairs if p.same_bet)
    body = (
        "<h1>Kalshi vs Polymarket: the factual comparison</h1>"
        "<p class=\"lede\">Kalshi is a US-regulated exchange. Polymarket runs two venues: a US "
        "one and the original global platform, which US accounts cannot use. They list many of "
        "the same events, charge fees on a similar curve, and settle by different rulebooks. "
        "This page puts what each venue publishes next to each other — regulation, who can use "
        "it, funding, fees, settlement, disputes, taxes — with a source on every row and no "
        "opinion on which is \"better\".</p>"
        f"<p>For the contracts themselves: we track <strong>{len(site.pairs)} matched pairs</strong> "
        f"across both venues right now, and <strong>{same}</strong> verify as the same bet under "
        "both rulebooks. <a href=\"./\">See every pair →</a></p>"
        + table
        + "<h2>The parts with their own page</h2><div class=\"cards\">"
        "<div class=\"card\"><a href=\"fees\">Fees at any price</a><p>Both published formulas, "
        "with a calculator.</p></div>"
        "<div class=\"card\"><a href=\"legal\">Legal status by state</a><p>All 50 states and DC, "
        "sourced.</p></div>"
        "<div class=\"card\"><a href=\"taxes\">Taxes</a><p>What forms each venue sends.</p></div>"
        "<div class=\"card\"><a href=\"referrals\">Referral programs</a><p>Credits vs cash, "
        "honestly.</p></div>"
        "<div class=\"card\"><a href=\"same-bet\">Same event, same bet?</a><p>Three real cases "
        "where the answer was no.</p></div></div>"
        + faq_html
        + (_faq_jsonld(faq) if faq else "")
    )
    return _page(site, title="Kalshi vs Polymarket (2026): regulation, fees, funding, settlement, taxes — compared",
                 path="kalshi-vs-polymarket.html", body=body,
                 description="Kalshi vs Polymarket side by side: regulation, who can use each, "
                 "funding, exact fees, settlement and disputes, taxes, referrals. Every row sourced.")


def render_about(site: Site) -> str:
    body = (
        "<h1>About</h1>"
        "<p class=\"lede\">This site is one person's project, generated by software they wrote, "
        "published because the answer it gives — \"usually not the same bet\" — was worth sharing.</p>"
        "<p><strong>Who.</strong> Omar (GitHub: <a href=\"https://github.com/omac049\" "
        "rel=\"noopener\">omac049</a>), a marketing-analytics professional. Not affiliated with, "
        "employed by, or sponsored by Kalshi or Polymarket. Not a lawyer, accountant, or financial "
        "adviser.</p>"
        "<p><strong>What.</strong> Over six weeks in 2026 I tested four ideas for making money in "
        "prediction markets, with pass/fail thresholds fixed before looking at data. All four "
        "failed, and the write-ups are public. What survived was the instrument: a deterministic "
        "checker that reads both venues' rulebooks and says whether two contracts actually settle "
        "the same way. This site publishes what it finds, every night.</p>"
        "<p><strong>Corrections.</strong> If a page is wrong — a rule text out of date, a fee "
        "formula changed, a legal status moved — open an issue at "
        f"<a href=\"{REPO_URL}/issues\" rel=\"noopener\">{_esc(REPO_URL)}/issues</a>. Every claim on a "
        "pillar page carries a source; a correction that cites a better one is applied.</p>"
        "<p><strong>Money.</strong> The site may earn referral revenue, disclosed on every page. "
        "Referral relationships never change a verdict, a price, or a fee; those come from the "
        "venues' own published data and the method is public.</p>"
        "<p><a href=\"methodology\">How the pages are made →</a></p>"
    )
    return _page(site, title="About Same bet or not?", path="about.html", body=body,
                 description="Who makes this site, why, and how to send a correction.")


# --- build -----------------------------------------------------------------

def render_all(site: Site) -> dict[str, str]:
    """path -> html for every page. Pure; the caller writes files."""
    pages = {
        "index.html": render_index(site),
        "fees.html": render_fees(site),
        "same-bet.html": render_same_bet(site),
        "legal.html": render_legal(site),
        "taxes.html": render_taxes(site),
        "referrals.html": render_referrals(site),
        "methodology.html": render_methodology(site),
        "about.html": render_about(site),
        "kalshi-vs-polymarket.html": render_comparison(site),
    }
    for page in site.pairs:
        pages[f"compare/{page.slug}.html"] = render_pair(site, page)
    base = site.base_url.rstrip("/")
    pages["sitemap.xml"] = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
        + "".join(
            f"  <url><loc>{_esc(base)}/{_esc(_href(path))}</loc>"
            f"<lastmod>{site.generated_at.date().isoformat()}</lastmod></url>\n"
            for path in pages
        )
        + "</urlset>\n"
    )
    pages["robots.txt"] = f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"
    pages[f"{INDEXNOW_KEY}.txt"] = INDEXNOW_KEY + "\n"
    pages["manifest.json"] = json.dumps(
        {
            "site_version": SITE_VERSION,
            "generated_at": site.generated_at.isoformat(),
            "pairs": len(site.pairs),
            "same_bet_pairs": sum(1 for p in site.pairs if p.same_bet),
            "tradeable_pairs": sum(1 for p in site.pairs if p.tradeable),
            "pairs_with_quotes": sum(
                1 for p in site.pairs if p.kalshi_quote and p.polymarket_quote
            ),
            "legal_states": len((site.legal_states or {}).get("states", [])),
            "comparison_rows": len((site.comparison or {}).get("rows", [])),
        },
        indent=2,
    ) + "\n"
    return pages


def write_site(pages: dict[str, str], out_dir: Path) -> int:
    for path, content in pages.items():
        target = out_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return len(pages)


def verify_pages(pages: dict[str, str]) -> list[str]:
    """Build-time guardrails. A page failing these is a bug, not a style choice."""
    problems = []
    for path, content in pages.items():
        if not path.endswith(".html"):
            continue
        if DISCLOSURE[:40] not in content:
            problems.append(f"{path}: missing affiliate disclosure")
        if NOT_ADVICE[:40] not in content:
            problems.append(f"{path}: missing not-advice notice")
        if not _METHODOLOGY_LINK.search(content):
            problems.append(f"{path}: no methodology link")
    return problems


def verify_legal_states(data: dict | None) -> list[str]:
    """A state row asserting action without a loaded source is not publishable."""
    if not data:
        return []
    problems = []
    for row in data.get("states", []):
        if str(row.get("status") or NO_STATE_ACTION) != NO_STATE_ACTION and not row.get("sources"):
            problems.append(f"legal: {row.get('state')} claims '{row.get('status')}' without a source")
    return problems


def verify_comparison(data: dict | None) -> list[str]:
    """A comparison row without a loaded source is not publishable."""
    if not data:
        return []
    return [
        f"comparison: row '{row.get('topic')}' has no source"
        for row in data.get("rows", [])
        if not row.get("sources")
    ]


def build_site(
    observations: list[dict],
    *,
    base_url: str,
    rules: dict[str, str] | None = None,
    grades: dict[str, dict] | None = None,
    quotes: dict[str, dict] | None = None,
    legal_states: dict | None = None,
    comparison: dict | None = None,
    generated_at: datetime | None = None,
    analytics_id: str | None = None,
) -> tuple[Site, dict[str, str]]:
    """Assemble pair pages from the latest observation per pair and render."""
    latest: dict[tuple[str, str], dict] = {}
    for obs in sorted(observations, key=lambda o: str(o.get("observed_at") or "")):
        latest[(str(obs.get("kalshi_market_id")), str(obs.get("polymarket_market_id")))] = obs
    rules = rules or {}
    grades = grades or {}
    quotes = quotes or {}
    pairs = [
        PairPage(
            observation=obs,
            kalshi_rules=rules.get(str(obs.get("kalshi_market_id"))),
            polymarket_rules=rules.get(str(obs.get("polymarket_market_id"))),
            kalshi_grade=grades.get(str(obs.get("kalshi_market_id"))),
            polymarket_grade=grades.get(str(obs.get("polymarket_market_id"))),
            kalshi_quote=quotes.get(str(obs.get("kalshi_market_id"))),
            polymarket_quote=quotes.get(str(obs.get("polymarket_market_id"))),
        )
        for obs in latest.values()
    ]
    site = Site(
        base_url=base_url,
        generated_at=generated_at or datetime.now(UTC),
        pairs=pairs,
        analytics_id=analytics_id,
        legal_states=legal_states,
        comparison=comparison,
    )
    problems = verify_legal_states(legal_states) + verify_comparison(comparison)
    if problems:
        raise ValueError("site guardrails failed: " + "; ".join(problems))
    pages = render_all(site)
    problems = verify_pages(pages)
    if problems:
        raise ValueError("site guardrails failed: " + "; ".join(problems))
    return site, pages
