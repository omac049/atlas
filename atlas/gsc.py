"""Loop 1: the sites measure themselves in Google Search Console.

A nightly job (deploy/com.atlas.gsc.plist) pulls Search Analytics rows for the
owner's two Domain properties using a service account created in the owner's
*personal* Google account. The Search Console connector available to Claude in
this workspace is an employer account and is never used for these sites.

Raw rows are written per site and day under data/gsc/ (gitignored). `report`
turns them into the numbers each charter's pass line is judged on, the queries
in striking distance (the input to loop 2), and a ready-to-paste scoreboard row.
Nothing here changes a page, a charter or a pass line.

The key file is read only by `load_key`, to sign one token request per run. Its
contents are never logged or printed; errors name the path, never the values.
Setup and guardrails: docs/GSC.md.
"""

import argparse
import base64
import json
import os
import re
import stat
import time
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "gsc"
DEFAULT_KEY_FILE = Path.home() / ".config" / "atlas" / "gsc-service-account.json"
DEFAULT_SITES = ("sc-domain:samebetornot.com", "sc-domain:verifiedfees.com")
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GOOGLE_OAUTH_URL = "https://oauth2.googleapis.com/token"
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
QUERY_URL = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
ROW_LIMIT = 25_000
MAX_PAGES = 20
STRIKING = (8.0, 30.0)
TIMEOUT = httpx.Timeout(30.0)

# What each charter's pass line is judged on: docs/IDEATION.md (samebetornot.com)
# and docs/decisions/2026-09-08-fee-calculator-demand-test.md (verifiedfees.com).
SITE_META = {
    "sc-domain:samebetornot.com": {
        "label": "samebetornot.com", "clock_start": "2026-09-04", "verdict": "2026-10-16",
        "target_impressions_30d": 2000, "top": 20, "terms": "queries naming Kalshi or Polymarket",
    },
    "sc-domain:verifiedfees.com": {
        "label": "verifiedfees.com", "clock_start": "2026-09-08", "verdict": "2026-10-20",
        "target_impressions_30d": 2000, "top": 20, "terms": "fee queries naming a covered platform",
    },
}


class NotConfigured(Exception):
    """The owner's one-time setup (docs/GSC.md) has not been done yet."""


def data_dir() -> Path:
    return Path(os.environ.get("ATLAS_GSC_DATA_DIR") or DEFAULT_DATA_DIR)


def key_path() -> Path:
    return Path(os.environ.get("ATLAS_GSC_KEY_FILE") or DEFAULT_KEY_FILE).expanduser()


def configured_sites() -> tuple[str, ...]:
    raw = os.environ.get("ATLAS_GSC_SITES", "")
    return tuple(s.strip() for s in raw.split(",") if s.strip()) or DEFAULT_SITES


def slug(site: str) -> str:
    return re.sub(r"[^a-z0-9.-]+", "_", site.removeprefix("sc-domain:").lower()).strip("_")


# --------------------------------------------------------------------- auth


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _json_b64(value: dict) -> str:
    return _b64(json.dumps(value, separators=(",", ":")).encode())


def signed_assertion(client_email: str, private_key, audience: str, now: int) -> str:
    """The RS256 JWT that Google's service-account token exchange expects."""
    claims = {"iss": client_email, "scope": SCOPE, "aud": audience, "iat": now, "exp": now + 3600}
    signing_input = f"{_json_b64({'alg': 'RS256', 'typ': 'JWT'})}.{_json_b64(claims)}"
    signature = private_key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64(signature)}"


def load_key(path: Path) -> dict:
    """Read the service-account key. Errors name the path, never the contents."""
    if not path.exists():
        raise NotConfigured(f"no service-account key at {path}")
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        raise NotConfigured(f"the key file at {path} is not readable JSON") from None
    if not isinstance(raw, dict) or raw.get("type") != "service_account":
        raise NotConfigured(f"the key file at {path} is not a Google service-account key")
    if not (raw.get("client_email") and raw.get("private_key")):
        raise NotConfigured(f"the key file at {path} is missing its email or private key")
    try:
        private_key = serialization.load_pem_private_key(raw["private_key"].encode(), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise NotConfigured(f"the private key in {path} could not be loaded") from None
    return {
        "client_email": raw["client_email"],
        "private_key": private_key,
        "token_uri": raw.get("token_uri") or GOOGLE_OAUTH_URL,
    }


def access_token(client: httpx.Client, key: dict, now: int | None = None) -> str:
    moment = int(time.time()) if now is None else now
    assertion = signed_assertion(key["client_email"], key["private_key"], key["token_uri"], moment)
    response = client.post(key["token_uri"], data={"grant_type": JWT_BEARER, "assertion": assertion})
    if response.status_code != 200:
        raise RuntimeError(
            f"Google refused the token request: HTTP {response.status_code} {response.text[:200]}"
        )
    return response.json()["access_token"]


# --------------------------------------------------------------------- pull


def query_rows(client: httpx.Client, token: str, site: str, body: dict) -> list[dict]:
    """Every row for one query, paged, and bounded so a runaway property cannot hang the job."""
    url = QUERY_URL.format(site=quote(site, safe=""))
    headers = {"Authorization": f"Bearer {token}"}
    rows: list[dict] = []
    for page in range(MAX_PAGES):
        payload = {**body, "rowLimit": ROW_LIMIT, "startRow": page * ROW_LIMIT}
        response = client.post(url, json=payload, headers=headers)
        if response.status_code in (401, 403):
            raise RuntimeError(
                f"{site}: HTTP {response.status_code}. Add the service account as a Restricted "
                "user on this Search Console property (docs/GSC.md, step 8)."
            )
        response.raise_for_status()
        batch = response.json().get("rows", [])
        rows.extend(batch)
        if len(batch) < ROW_LIMIT:
            return rows
    raise RuntimeError(f"{site}: more than {MAX_PAGES * ROW_LIMIT:,} rows; raise MAX_PAGES on purpose")


def _metrics(row: dict) -> dict:
    return {
        "clicks": row.get("clicks", 0),
        "impressions": row.get("impressions", 0),
        "ctr": row.get("ctr", 0.0),
        "position": row.get("position", 0.0),
    }


def _blank(site: str, day: str, stamp: str) -> dict:
    return {"site": site, "date": day, "pulled_at": stamp, "totals": None, "rows": []}


def pull(
    client: httpx.Client, key: dict, sites: Iterable[str], days: int, today: date, out_dir: Path
) -> dict:
    """Pull the trailing `days` ending yesterday; rewrite one file per day that has data.

    Search Console revises recent days, so every run re-pulls the whole window.
    """
    token = access_token(client, key)
    end = today - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    window = {
        "startDate": start.isoformat(), "endDate": end.isoformat(), "dataState": "all", "type": "web",
    }
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    summary = {}
    for site in sites:
        by_day: dict[str, dict] = {}
        for row in query_rows(client, token, site, {**window, "dimensions": ["date"]}):
            day = row["keys"][0]
            by_day.setdefault(day, _blank(site, day, stamp))["totals"] = _metrics(row)
        detail = query_rows(client, token, site, {**window, "dimensions": ["date", "page", "query"]})
        for row in detail:
            day, page, text = row["keys"]
            by_day.setdefault(day, _blank(site, day, stamp))["rows"].append(
                {"page": page, "query": text, **_metrics(row)}
            )
        folder = out_dir / slug(site)
        folder.mkdir(parents=True, exist_ok=True)
        for day, record in by_day.items():
            (folder / f"{day}.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
        summary[site] = {
            "days_with_data": len(by_day),
            "rows": len(detail),
            "window": f"{start.isoformat()}..{end.isoformat()}",
        }
    return summary


# ------------------------------------------------------------------- report


def load_days(folder: Path) -> list[dict]:
    if not folder.is_dir():
        return []
    return [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))]


def _within(days: list[dict], end: date, length: int) -> list[dict]:
    first = end - timedelta(days=length - 1)
    return [d for d in days if first <= date.fromisoformat(d["date"]) <= end]


def totals(days: list[dict]) -> dict:
    """Clicks and impressions summed; position weighted by impressions, as Search Console does."""
    parts = [d["totals"] for d in days if d.get("totals")]
    clicks = sum(p["clicks"] for p in parts)
    impressions = sum(p["impressions"] for p in parts)
    weighted = sum(p["position"] * p["impressions"] for p in parts)
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": round(clicks / impressions, 4) if impressions else 0.0,
        "position": round(weighted / impressions, 1) if impressions else None,
    }


def by_query(days: list[dict]) -> list[dict]:
    """One entry per query: summed clicks and impressions, weighted position, main page."""
    acc: dict[str, dict] = {}
    for day in days:
        for row in day.get("rows", []):
            entry = acc.setdefault(
                row["query"], {"clicks": 0, "impressions": 0, "weighted": 0.0, "pages": {}}
            )
            entry["clicks"] += row["clicks"]
            entry["impressions"] += row["impressions"]
            entry["weighted"] += row["position"] * row["impressions"]
            entry["pages"][row["page"]] = entry["pages"].get(row["page"], 0) + row["impressions"]
    out = [
        {
            "query": text,
            "clicks": e["clicks"],
            "impressions": e["impressions"],
            "position": round(e["weighted"] / e["impressions"], 1) if e["impressions"] else None,
            "page": max(e["pages"], key=e["pages"].get) if e["pages"] else None,
        }
        for text, e in acc.items()
    ]
    return sorted(out, key=lambda q: (-q["impressions"], q["position"] or 999.0, q["query"]))


def fee_platform_names(fees_dir: Path = REPO_ROOT / "docs" / "fees") -> list[str]:
    names: set[str] = set()
    for path in fees_dir.glob("*.json"):
        if path.name.startswith(("_", "verification")):
            continue
        schedule = json.loads(path.read_text())
        names.update({schedule["platform"].lower(), schedule["name"].split(" (")[0].lower()})
    return sorted(names)


def term_matcher(site: str, fee_names: list[str]) -> Callable[[str], bool] | None:
    """The queries a charter's 'top 20' clause is about."""
    if site == "sc-domain:samebetornot.com":
        return lambda text: "kalshi" in text or "polymarket" in text
    if site == "sc-domain:verifiedfees.com":
        return lambda text: "fee" in text and any(name in text for name in fee_names)
    return None


def _path(url: str | None) -> str:
    return re.sub(r"^https?://[^/]+", "", url or "") or "/"


def _scoreboard_row(site: str, today: date, latest: date, last7: dict, last30: dict,
                    best: dict | None, week: int) -> str:
    position = "—" if last7["position"] is None else last7["position"]
    notes = f"auto-pulled; {last30['impressions']:,} of 2,000 impressions in 30 days"
    if site == "sc-domain:verifiedfees.com":
        cell = f"{_path(best['page'])} ({best['position']})" if best else "—"
        return (f"| {week} | {today.isoformat()} | {last7['impressions']:,} | {last7['clicks']} | "
                f"{position} | {cell} | {notes} |")
    if best:
        notes += f"; best Kalshi/Polymarket query \"{best['query']}\" at {best['position']}"
    return (f"| {today.isoformat()} | last 7 days, data through {latest.isoformat()} | "
            f"{last7['impressions']:,} | {last7['clicks']} | {position} | {notes} |")


def site_report(site: str, days: list[dict], today: date, fee_names: list[str]) -> dict:
    meta = SITE_META.get(site, {"label": slug(site)})
    report: dict = {"site": site, "label": meta["label"], "data_through": None}
    if not days:
        return report
    latest = max(date.fromisoformat(d["date"]) for d in days)
    last30_days = _within(days, latest, 30)
    queries = by_query(last30_days)
    low, high = STRIKING
    report.update({
        "data_through": latest.isoformat(),
        "last_7_days": totals(_within(days, latest, 7)),
        "last_30_days": totals(last30_days),
        "top_queries": queries[:15],
        "striking_distance": [
            q for q in queries
            if q["position"] and low <= q["position"] <= high and q["impressions"] >= 3
        ][:15],
    })
    match = term_matcher(site, fee_names)
    if match is None or "target_impressions_30d" not in meta:
        return report
    terms = [q for q in queries if q["position"] and match(q["query"])]
    best = min(terms, key=lambda q: q["position"]) if terms else None
    week = ((today - date.fromisoformat(meta["clock_start"])).days + 6) // 7
    report["pass_line"] = {
        "impressions_30d": report["last_30_days"]["impressions"],
        "impressions_target": meta["target_impressions_30d"],
        "terms": meta["terms"],
        "best_term": best,
        "top_n": meta["top"],
        "in_top_n": bool(best and best["position"] <= meta["top"]),
        "clock_start": meta["clock_start"],
        "verdict": meta["verdict"],
        "week": week,
        "scoreboard_row": _scoreboard_row(
            site, today, latest, report["last_7_days"], report["last_30_days"], best, week
        ),
    }
    return report


def _window_row(name: str, t: dict) -> str:
    position = "—" if t["position"] is None else t["position"]
    return f"| {name} | {t['impressions']:,} | {t['clicks']} | {t['ctr']:.1%} | {position} |"


def render_markdown(reports: list[dict], today: date) -> str:
    lines = [f"# Search Console report, {today.isoformat()}", ""]
    for r in reports:
        lines += [f"## {r['label']}", ""]
        if not r["data_through"]:
            lines += ["No data pulled yet.", ""]
            continue
        lines += [
            f"Data through {r['data_through']}. Search Console lags two to three days.", "",
            "| Window | Impressions | Clicks | CTR | Avg position |", "|---|---|---|---|---|",
            _window_row("Last 7 days", r["last_7_days"]),
            _window_row("Last 30 days", r["last_30_days"]), "",
        ]
        line = r.get("pass_line")
        if line:
            best = line["best_term"]
            where = (f"{best['position']} for \"{best['query']}\" on {_path(best['page'])}"
                     if best else "none yet")
            verdict = (
                f"**Pass line**, week {line['week']}, verdict {line['verdict']}: "
                f"{line['impressions_30d']:,} of {line['impressions_target']:,} impressions in the "
                f"trailing 30 days. Best position on {line['terms']}: {where}. "
                f"Top {line['top_n']}: {'yes' if line['in_top_n'] else 'not yet'}."
            )
            lines += [verdict, "", "Scoreboard row:", "", "```", line["scoreboard_row"], "```", ""]
        lines.append("Top queries, last 30 days:")
        lines += [
            f"- {q['query']}: {q['impressions']} impressions, {q['clicks']} clicks, "
            f"position {q['position']}, {_path(q['page'])}" for q in r["top_queries"]
        ] or ["- none"]
        lines += ["", "Striking distance, positions 8 to 30 (loop 2's input):"]
        lines += [
            f"- {q['query']}: position {q['position']}, {q['impressions']} impressions, "
            f"{_path(q['page'])}" for q in r["striking_distance"]
        ] or ["- none"]
        lines.append("")
    return "\n".join(lines) + "\n"


def one_line(r: dict) -> str:
    if not r["data_through"]:
        return f"{r['label']}: no data yet"
    last7, last30 = r["last_7_days"], r["last_30_days"]
    return (f"{r['label']}: through {r['data_through']}, 7d {last7['impressions']:,} impressions "
            f"{last7['clicks']} clicks, 30d {last30['impressions']:,} impressions")


# ---------------------------------------------------------------------- cli


def _status(out: Path) -> int:
    path = key_path()
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
        loose = "; readable by other users, run chmod 600 on it" if mode & 0o077 else ""
        print(f"key: present at {path}, mode {oct(mode)}{loose}")
    else:
        print(f"key: missing at {path}; owner setup pending, see docs/GSC.md")
    for site in configured_sites():
        folder = out / slug(site)
        files = sorted(folder.glob("*.json")) if folder.is_dir() else []
        span = f"{files[0].stem}..{files[-1].stem}" if files else "no data yet"
        print(f"{site}: {len(files)} days, {span}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m atlas.gsc", description="Search Console data for the owner's sites."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    pull_cmd = sub.add_parser("pull", help="pull the trailing window for every configured site")
    pull_cmd.add_argument("--days", type=int, default=35)
    sub.add_parser("report", help="write data/gsc/report.md and report.json")
    sub.add_parser("status", help="setup state and data coverage; never prints key contents")
    args = parser.parse_args(argv)
    out = data_dir()
    if args.command == "status":
        return _status(out)
    today = datetime.now(UTC).date()
    if args.command == "pull":
        try:
            key = load_key(key_path())
        except NotConfigured as exc:
            print(f"skipped: not configured ({exc}); see docs/GSC.md")
            return 0
        with httpx.Client(timeout=TIMEOUT) as client:
            summary = pull(client, key, configured_sites(), args.days, today, out)
        print(json.dumps(summary, sort_keys=True))
        return 0
    names = fee_platform_names()
    reports = [site_report(s, load_days(out / slug(s)), today, names) for s in configured_sites()]
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    (out / "report.json").write_text(
        json.dumps({"generated_at": stamp, "sites": reports}, indent=1) + "\n"
    )
    (out / "report.md").write_text(render_markdown(reports, today))
    for r in reports:
        print(one_line(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
