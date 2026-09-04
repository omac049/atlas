"""Static site generator for the demand test in docs/IDEATION.md.

Turns what Atlas already computes — cross-venue twin pairs, the deterministic
verifier's verdict on each, the venues' own fee formulas, settlement-timing
annotations, and archived rules text — into a static HTML site: one factual
comparison page per pair, plus pillar pages whose every claim carries a source
and a date.

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

SITE_VERSION = "0.1"
TRUSTED_STATUSES = {"APPROVED_EQUIVALENT", "APPROVED_INVERSE"}

# Verified 2026-09-04 against the venues' published schedules; the calculator
# page cites both. Kalshi's per-contract ceiling is the conservative reading
# already used by atlas/gap_radar.py.
KALSHI_TAKER_RATE = Decimal("0.07")
KALSHI_MAKER_SHARE = Decimal("0.25")
POLYMARKET_US_TAKER_RATE = Decimal("0.06")
POLYMARKET_US_MAKER_RATE = Decimal("0.0125")

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


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:120] or "page"


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _cents(value: object) -> str:
    try:
        return f"{Decimal(str(value)) * 100:.1f}¢"
    except (ArithmeticError, ValueError, TypeError):
        return "—"


def _dollars(value: object) -> str:
    try:
        return f"${Decimal(str(value)):.2f}"
    except (ArithmeticError, ValueError, TypeError):
        return "—"


@dataclass
class PairPage:
    """One comparison page, built from a radar observation plus optional
    archived rules text and clarity grades for each leg."""

    observation: dict
    kalshi_rules: str | None = None
    polymarket_rules: str | None = None
    kalshi_grade: dict | None = None
    polymarket_grade: dict | None = None

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
    def reasons(self) -> list[tuple[str, str]]:
        codes = list(self.observation.get("mismatch_codes") or [])
        timing = self.observation.get("settlement_timing") or {}
        if timing.get("asymmetric") and "SETTLEMENT_TIMING_ASYMMETRIC" not in codes:
            codes.append("SETTLEMENT_TIMING_ASYMMETRIC")
        return [(code, REASON_PROSE.get(code, code.replace("_", " ").lower())) for code in codes]

    @property
    def title(self) -> str:
        # Kalshi titles are already plain questions; the subject key is not.
        headline = str(self.observation.get("kalshi_title") or "").rstrip("?")
        if not headline:
            subject = str(self.observation.get("event_subject") or "").replace("|", " ")
            headline = subject.replace("_", " ")
        return f"Kalshi vs Polymarket: {headline}"


@dataclass
class Site:
    base_url: str
    generated_at: datetime
    pairs: list[PairPage] = field(default_factory=list)
    # A GA4 measurement id (G-XXXXXXX). None = no analytics tag at all; the
    # demand test counts social clicks, so measurement is opt-in by the owner.
    analytics_id: str | None = None

    @property
    def stamp(self) -> str:
        return self.generated_at.strftime("%Y-%m-%d %H:%M UTC")


# --- page chrome -----------------------------------------------------------

_CSS = """
:root{--ink:#1a1a1a;--muted:#5c5c5c;--line:#e3e3e3;--ok:#1f7a3a;--warn:#9a4a00;--bg:#fff}
*{box-sizing:border-box}body{margin:0;font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,
sans-serif;color:var(--ink);background:var(--bg)}main{max-width:860px;margin:0 auto;
padding:24px 20px 60px}header nav{display:flex;flex-wrap:wrap;gap:14px;padding:14px 20px;
border-bottom:1px solid var(--line);font-size:14px}header nav a{color:var(--ink);
text-decoration:none}h1{font-size:1.7rem;line-height:1.2;margin:.2em 0 .4em}h2{font-size:1.2rem;
margin:1.6em 0 .5em}table{border-collapse:collapse;width:100%;margin:.6em 0}th,td{text-align:left;
padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top;font-size:15px}
th{font-weight:600}.verdict{padding:14px 16px;border-radius:8px;margin:1em 0;font-weight:600}
.verdict.ok{background:#e9f6ee;color:var(--ok)}.verdict.no{background:#fbf1e6;color:var(--warn)}
.muted{color:var(--muted);font-size:14px}blockquote{margin:.6em 0;padding:.6em 1em;
border-left:3px solid var(--line);color:#333;font-size:14px;white-space:pre-wrap}
footer{max-width:860px;margin:0 auto;padding:20px;border-top:1px solid var(--line);
font-size:13px;color:var(--muted)}input[type=number]{font-size:16px;padding:6px 8px;width:110px}
.calc{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:1em 0}
@media(max-width:600px){.calc{grid-template-columns:1fr}}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;
background:#f0f0f0;color:#333;margin-left:6px}
"""

_GA4_ID = re.compile(r"G-[A-Z0-9]{6,12}")
_METHODOLOGY_LINK = re.compile(r'href="(\.\./)*methodology"')


def _href(path: str) -> str:
    """Files are written as .html; links and canonicals are extensionless.

    Static hosts (Cloudflare Pages among them) serve `/legal` for legal.html
    and redirect `/legal.html` to it, so a link carrying the extension costs a
    redirect on every click and a "page with redirect" note in Search Console.
    """
    if path == "index.html":
        return ""
    return path.removesuffix(".html")

_NAV = (
    ("index.html", "Compare"),
    ("fees.html", "Fee calculator"),
    ("same-bet.html", "Same bet or not?"),
    ("legal.html", "Legal status"),
    ("taxes.html", "Taxes"),
    ("referrals.html", "Referral terms"),
    ("methodology.html", "How this works"),
)


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
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_esc(title)}</title>"
        f"<meta name=\"description\" content=\"{_esc(description)}\">"
        f"<link rel=\"canonical\" href=\"{_esc(canonical)}\">"
        f"<style>{_CSS}</style>{analytics}</head><body>"
        f"<header><nav>{nav}</nav></header><main>{body}</main>"
        f"<footer><p>{_esc(DISCLOSURE)}</p><p>{_esc(NOT_ADVICE)}</p>"
        f"<p>Generated {_esc(site.stamp)} from published venue data. "
        f"<a href=\"{root}methodology\">How these pages are made.</a></p></footer>"
        "</body></html>\n"
    )


# --- pair pages ------------------------------------------------------------

def _leg_table(page: PairPage) -> str:
    obs = page.observation
    best = next(
        (b for b in obs.get("baskets", []) if b.get("legs") == obs.get("best_basket")),
        None,
    )
    kalshi_fee = _cents(best.get("kalshi_fee")) if best and best.get("kalshi_fee") else "—"
    poly_fee = _cents(best.get("polymarket_fee")) if best and best.get("polymarket_fee") else "—"
    venue = str(obs.get("polymarket_venue") or "polymarket")
    venue_label = (
        "Polymarket US"
        if venue == "polymarket_us"
        else "Polymarket (global — not available to US accounts)"
    )
    return (
        "<table><thead><tr><th>Venue</th><th>Contract</th><th>Taker fee at last observed price</th>"
        "</tr></thead><tbody>"
        f"<tr><td>Kalshi</td><td>{_esc(obs.get('kalshi_title', ''))}"
        f"<div class=\"muted\">{_esc(obs.get('kalshi_market_id', ''))}</div></td>"
        f"<td>{kalshi_fee} per contract</td></tr>"
        f"<tr><td>{_esc(venue_label)}</td><td>{_esc(obs.get('polymarket_title', ''))}"
        f"<div class=\"muted\">{_esc(obs.get('polymarket_market_id', ''))}</div></td>"
        f"<td>{poly_fee} per share</td></tr></tbody></table>"
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
            "terms differ in ways that could settle differently:</div>"
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
        rules += f"<h2>What Kalshi publishes</h2><blockquote>{_esc(page.kalshi_rules[:1200])}</blockquote>"
    if page.polymarket_rules:
        rules += (
            f"<h2>What Polymarket publishes</h2><blockquote>{_esc(page.polymarket_rules[:1200])}</blockquote>"
        )
    tradeable_note = (
        ""
        if page.tradeable
        else "<p class=\"muted\">The Polymarket leg here is the global venue, which US accounts "
        "cannot trade; it is shown for the rules comparison only.</p>"
    )
    body = (
        f"<h1>{_esc(page.title)}</h1>"
        f"<p class=\"muted\">Last observed {_esc(str(obs.get('observed_at', ''))[:16].replace('T', ' '))} UTC. "
        "Prices and fees change constantly; the rules comparison is the durable part.</p>"
        + verdict
        + tradeable_note
        + _leg_table(page)
        + timing_html
        + _grade_line("Kalshi", page.kalshi_grade)
        + _grade_line("Polymarket", page.polymarket_grade)
        + rules
        + "<p class=\"muted\">Fees use each venue's published taker formula at the last observed "
        "price — see the <a href=\"../fees\">fee calculator</a> for any price.</p>"
    )
    description = (
        f"{'Verified the same bet' if page.same_bet else 'Not verified as the same bet'}: "
        f"{obs.get('kalshi_title', '')} vs {obs.get('polymarket_title', '')}."
    )
    return _page(site, title=page.title, path=f"compare/{page.slug}.html", body=body, description=description)


# --- pillar pages ----------------------------------------------------------

def _sources(items: list[tuple[str, str]]) -> str:
    return "<h2>Sources</h2><ul>" + "".join(
        f"<li><a href=\"{_esc(url)}\" rel=\"nofollow noopener\">{_esc(label)}</a></li>"
        for label, url in items
    ) + "</ul>"


def render_index(site: Site) -> str:
    same = [p for p in site.pairs if p.same_bet]
    rows = "".join(
        f"<tr><td><a href=\"compare/{p.slug}\">{_esc(p.title)}</a></td>"
        f"<td>{'Same bet ✓' if p.same_bet else 'Not verified'}</td>"
        f"<td>{'Yes' if p.tradeable else 'Global only'}</td></tr>"
        for p in sorted(site.pairs, key=lambda p: (not p.same_bet, p.title))
    )
    body = (
        "<h1>Kalshi vs Polymarket, contract by contract</h1>"
        "<p>The same event is often listed on both venues. Whether it is the <em>same bet</em> "
        "depends on the fine print — the settlement source, what happens if the data is late, "
        "how the number is rounded. These pages compare the venues' own published terms for "
        f"every matched pair we track, using a deterministic rule check. Right now: "
        f"<strong>{len(site.pairs)} pairs</strong>, of which <strong>{len(same)}</strong> verify as "
        "the same bet.</p>"
        "<p class=\"muted\">Regenerated from live venue data. No picks, no predictions — "
        "just what each venue says it will do.</p>"
        "<table><thead><tr><th>Pair</th><th>Verdict</th><th>US-tradeable</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )
    return _page(site, title="Kalshi vs Polymarket — same bet or not, per contract", path="index.html",
                 body=body, description="Contract-by-contract comparison of Kalshi and Polymarket "
                 "using each venue's published rules.")


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
        "<p>Both venues charge more when a contract is near 50¢ and almost nothing near the "
        "extremes — the fee is a curve, not a flat rate. The headline numbers (\"7%\" vs \"6%\") "
        "are the coefficients in that curve. Enter a price and a size:</p>"
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
        "1.5¢ (Polymarket US). At 90¢ they are 0.63¢ (rounded to 1¢) and 0.54¢. Deposits, withdrawals, and any crypto on-ramp costs are "
        "separate and not modeled here.</p>"
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
        "<p>Two contracts can share a headline and still pay out differently. Three real cases:</p>"
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


def render_legal(site: Site) -> str:
    body = (
        "<h1>Where Kalshi and Polymarket stand legally (as of September 2026)</h1>"
        "<p class=\"muted\">A factual tracker of public rulings and filings, not legal advice. "
        "This area is changing month to month; check the sources for the current state.</p>"
        "<h2>The federal picture</h2>"
        "<p>Kalshi is a CFTC-regulated exchange. Polymarket US operates a separately regulated US "
        "venue; Polymarket's original global platform is not available to US accounts. Federal "
        "courts have generally treated Kalshi's event contracts as swaps under the Commodity "
        "Exchange Act, which would preempt state gambling law — a US appellate court allowed Kalshi "
        "to keep offering sports contracts in New Jersey while litigation continues.</p>"
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
        "<p>The pattern so far: federal courts lean toward CFTC jurisdiction; several state courts "
        "and regulators treat sports contracts as gambling. Economic and political contracts have "
        "drawn less state action than sports contracts. Your own state's status can change with a "
        "single ruling.</p>"
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
                 description="Factual tracker of the court rulings and state actions affecting "
                 "Kalshi and Polymarket in 2026. Not legal advice.")


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
        "negative result — is public. That record is the reason to trust a page that says "
        "\"not verified\": the instrument is built to say no.</p>"
        "<h2>What is and isn't here</h2>"
        "<ul><li>Facts about published rules, fees, legal filings, and program terms — with sources "
        "and dates.</li><li>No picks, no probabilities, no strategies, no advice.</li>"
        "<li>Affiliate relationships, disclosed on every page, that never affect a verdict.</li></ul>"
    )
    return _page(site, title="How this site works", path="methodology.html", body=body,
                 description="How the Kalshi vs Polymarket comparison pages are generated, "
                 "and what they deliberately are not.")


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
    pages["manifest.json"] = json.dumps(
        {
            "site_version": SITE_VERSION,
            "generated_at": site.generated_at.isoformat(),
            "pairs": len(site.pairs),
            "same_bet_pairs": sum(1 for p in site.pairs if p.same_bet),
            "tradeable_pairs": sum(1 for p in site.pairs if p.tradeable),
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


def build_site(
    observations: list[dict],
    *,
    base_url: str,
    rules: dict[str, str] | None = None,
    grades: dict[str, dict] | None = None,
    generated_at: datetime | None = None,
    analytics_id: str | None = None,
) -> tuple[Site, dict[str, str]]:
    """Assemble pair pages from the latest observation per pair and render."""
    latest: dict[tuple[str, str], dict] = {}
    for obs in sorted(observations, key=lambda o: str(o.get("observed_at") or "")):
        latest[(str(obs.get("kalshi_market_id")), str(obs.get("polymarket_market_id")))] = obs
    rules = rules or {}
    grades = grades or {}
    pairs = [
        PairPage(
            observation=obs,
            kalshi_rules=rules.get(str(obs.get("kalshi_market_id"))),
            polymarket_rules=rules.get(str(obs.get("polymarket_market_id"))),
            kalshi_grade=grades.get(str(obs.get("kalshi_market_id"))),
            polymarket_grade=grades.get(str(obs.get("polymarket_market_id"))),
        )
        for obs in latest.values()
    ]
    site = Site(
        base_url=base_url,
        generated_at=generated_at or datetime.now(UTC),
        pairs=pairs,
        analytics_id=analytics_id,
    )
    pages = render_all(site)
    problems = verify_pages(pages)
    if problems:
        raise ValueError("site guardrails failed: " + "; ".join(problems))
    return site, pages
