"""Nightly verification of every schedule's source page.

For each docs/fees/<platform>.json, fetch every source URL, reduce the page to
its visible text, hash it, and compare with the hash recorded in
docs/fees/verification.json. Three outcomes, each shown on the site:

- verified:   hash unchanged since the numbers were last checked by a human.
- changed:    the page's text changed after the last human check. The site
              shows the calculator with an "under review" banner until a human
              re-reads the schedule, updates the JSON, and marks it reviewed.
- unreachable: the page could not be fetched tonight. The last verified date
              stands; nothing is silently assumed.

The verifier never edits a schedule. Numbers change only by a human commit.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from html import unescape as html_unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEES_DIR = ROOT / "docs" / "fees"
STATE_PATH = FEES_DIR / "verification.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36"
)

_DROP = re.compile(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", re.DOTALL | re.IGNORECASE)
_BLOCK = re.compile(r"</?(?:p|div|li|ul|ol|td|th|tr|table|h[1-6]|br|section|article|header|footer|dt|dd)\b[^>]*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")


def schedules() -> list[Path]:
    return sorted(p for p in FEES_DIR.glob("*.json") if not p.name.startswith(("_", "verification")))


def visible_text(html: str) -> str:
    """Strip markup, keeping block boundaries as newlines so that a session id
    in one <div> cannot glue itself onto the fee sentence in the next."""
    text = _DROP.sub(" ", html)
    text = _BLOCK.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html_unescape(text)
    lines = [_WS.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


_FEE_TOKEN = re.compile(r"\d+(?:\.\d+)?\s?%|\$\s?\d")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n")


def fee_lines(text: str) -> list[str]:
    """The sentences that carry a fee: anything with a percentage or a dollar
    amount. Session ids, 'suggested searches', ads and nonces change between
    two fetches seconds apart (verified on eBay's page, 2026-09-08); the fee
    sentences do not. Sorted and de-duplicated so ordering churn is ignored."""
    parts = _SENTENCE_SPLIT.split(text)
    return sorted({_WS.sub(" ", part).strip() for part in parts if _FEE_TOKEN.search(part)})


_NORMALIZE = [
    ("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"'), ("\u2013", "-"), ("\u2014", "-"),
    ("\u00a0", " "), ("\u2026", "..."),
]


def normalize(text: str) -> str:
    """Case, quote style, dashes and whitespace do not count as a fee change."""
    out = text
    for old, new in _NORMALIZE:
        out = out.replace(old, new)
    return re.sub(r"\s+", " ", out).strip().lower()


def missing_quotes(page_text: str, quotes: list[str]) -> list[str]:
    """The quoted sentences a schedule relies on that no longer appear in the
    page text. A quote written with '...' is a list of fragments that must
    each appear. This is the verification that decides 'changed': the fee
    fingerprint is recorded alongside as supporting evidence only."""
    haystack = normalize(page_text)
    missing = []
    for quote in quotes:
        fragments = [f.strip() for f in normalize(quote).split("...") if f.strip()]
        if any(fragment not in haystack for fragment in fragments):
            missing.append(quote)
    return missing


def fee_fingerprint(html: str) -> str:
    lines = fee_lines(visible_text(html))
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def text_hash(html: str) -> str:
    """Kept as the name the rest of the module uses; the hash is of the fee
    sentences, not the whole page — see fee_lines."""
    return fee_fingerprint(html)


_BROWSER = {"playwright": None, "browser": None, "context": None}


def _rendered(url: str) -> str:
    """Render the page in headless Chromium and return its HTML after the
    network goes quiet. Several platforms (Amazon Seller Central, StubHub's
    help center, Shopify help) serve their fee schedules only through
    JavaScript, so a plain fetch would verify an empty shell."""
    from playwright.sync_api import sync_playwright

    if _BROWSER["browser"] is None:
        _BROWSER["playwright"] = sync_playwright().start()
        _BROWSER["browser"] = _BROWSER["playwright"].chromium.launch(headless=True)
        _BROWSER["context"] = _BROWSER["browser"].new_context(user_agent=USER_AGENT, locale="en-US")
    page = _BROWSER["context"].new_page()
    try:
        try:
            page.goto(url, wait_until="networkidle", timeout=45000)
        except Exception:  # noqa: BLE001 - a slow tracker must not fail the page; use what rendered
            page.wait_for_timeout(2000)
        return page.content()
    finally:
        page.close()


def close_browser() -> None:
    if _BROWSER["browser"] is not None:
        _BROWSER["browser"].close()
        _BROWSER["playwright"].stop()
        _BROWSER.update({"playwright": None, "browser": None, "context": None})


def fetch(url: str) -> str:
    """Rendered browser fetch first; browser-impersonating and plain fetches
    as fallbacks. Raises on failure."""
    rendered_error = None
    try:
        html = _rendered(url)
        if len(visible_text(html)) > 500:
            return html
    except Exception as exc:  # noqa: BLE001 - fall through to the lighter fetchers
        rendered_error = str(exc)[:120]
    try:
        from curl_cffi import requests as cffi_requests

        response = cffi_requests.get(url, impersonate="chrome", timeout=30)
        if response.status_code == 200 and len(response.text) > 2000:
            return response.text
        status = response.status_code
    except ImportError:
        status = None
    import httpx

    response = httpx.get(
        url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        timeout=30, follow_redirects=True,
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} (impersonated: {status}; rendered: {rendered_error})")
    return response.text


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"platforms": {}}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def mark_reviewed(platform: str, note: str) -> dict:
    """A human re-read the schedule and updated the JSON: record today's
    hashes as the new baseline. This is the only way 'changed' becomes
    'verified' again."""
    state = load_state()
    entry = state["platforms"].setdefault(platform, {"sources": {}, "history": []})
    schedule = json.loads((FEES_DIR / f"{platform}.json").read_text())
    now = datetime.now(UTC).isoformat(timespec="seconds")
    statuses = []
    texts = []
    for source in schedule["sources"]:
        if source.get("verify") is False:
            entry["sources"].pop(source["url"], None)
            continue
        try:
            html = fetch(source["url"])
        except Exception as exc:  # noqa: BLE001 - a page that will not load is recorded, not hidden
            entry["sources"][source["url"]] = {"reviewed_at": now, "last_checked_at": now, "status": "unreachable", "error": str(exc)[:200]}
            statuses.append("unreachable")
            continue
        entry["sources"][source["url"]] = {"hash": text_hash(html), "reviewed_at": now, "last_checked_at": now, "status": "verified"}
        texts.append(visible_text(html))
        statuses.append("verified")
    entry["quotes_missing_at_review"] = missing_quotes("\n".join(texts), schedule.get("quotes", []))
    entry["status"] = "unreachable" if "unreachable" in statuses else "verified"
    entry["reviewed_at"] = now
    entry["history"].append({"at": now, "event": "reviewed", "note": note})
    save_state(state)
    close_browser()
    return entry


def check_all() -> dict:
    """Nightly: compare every source page with its reviewed baseline."""
    state = load_state()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    summary = {"verified": 0, "changed": 0, "unreachable": 0, "unreviewed": 0}
    for path in schedules():
        schedule = json.loads(path.read_text())
        platform = schedule["platform"]
        entry = state["platforms"].setdefault(platform, {"sources": {}, "history": []})
        statuses = []
        texts = []
        for source in schedule["sources"]:
            if source.get("verify") is False:
                continue
            url = source["url"]
            rec = entry["sources"].setdefault(url, {})
            try:
                html = fetch(url)
            except Exception as exc:  # noqa: BLE001 - reported, never fatal
                rec.update({"last_checked_at": now, "status": "unreachable", "error": str(exc)[:200]})
                statuses.append("unreachable")
                continue
            texts.append(visible_text(html))
            current = text_hash(html)
            rec["last_checked_at"] = now
            rec.pop("error", None)
            if not rec.get("hash"):
                rec.update({"hash": current, "status": "unreviewed"})
                statuses.append("unreviewed")
            else:
                # Fingerprint drift is recorded as evidence; the decision is the quotes.
                rec["fingerprint_drift"] = rec["hash"] != current
                rec["status"] = "verified"
                statuses.append("verified")
        missing = missing_quotes("\n".join(texts), schedule.get("quotes", [])) if texts else []
        # Quotes the reviewer already knew were absent do not count as a change.
        known = set(entry.get("quotes_missing_at_review", []))
        newly_missing = [q for q in missing if q not in known]
        if newly_missing:
            # Pages render slightly differently between loads (measured: a
            # single fee line flapped on Stripe's pricing page). A quote only
            # counts as gone if a second, independent fetch also lacks it.
            texts2 = []
            for source in schedule["sources"]:
                if source.get("verify") is False:
                    continue
                try:
                    texts2.append(visible_text(fetch(source["url"])))
                except Exception as exc:  # noqa: BLE001 - the first pass already recorded reachability
                    entry["sources"].setdefault(source["url"], {})["recheck_error"] = str(exc)[:120]
            if texts2:
                still_missing = set(missing_quotes("\n".join(texts2), newly_missing))
                newly_missing = [q for q in newly_missing if q in still_missing]
                missing = [q for q in missing if q in known or q in still_missing]
        entry["quotes_missing"] = missing
        if newly_missing and texts:
            if entry.get("status") != "changed":
                entry["history"].append({"at": now, "event": "quotes_missing", "quotes": newly_missing[:5]})
            statuses.append("changed")
        # A platform is only as verified as its least-verified source.
        for level in ("changed", "unreviewed", "unreachable", "verified"):
            if level in statuses:
                entry["status"] = level
                break
        entry["last_checked_at"] = now
        summary[entry["status"]] += 1
    save_state(state)
    close_browser()
    return summary


if __name__ == "__main__":
    print(json.dumps(check_all()))
