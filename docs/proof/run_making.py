"""Fifth charter runner: fetch Kalshi tapes, load Atlas books, replay, judge.

Paper-only. Reads public trade prints and the local order-book snapshots, writes
one JSON artifact per arm beside this file, and places nothing.

  python docs/proof/run_making.py --arm B                       # MLB games, fourth charter's window
  python docs/proof/run_making.py --arm A                       # Fed markets settling by 2026-09-16
  python docs/proof/run_making.py --arm A --settle-by 2026-10-28  # the single widening in section 9
  python docs/proof/run_making.py --probe                       # the excluded STL@LAD game, validation only

Section 11.3: Arm B's verdict is written to its artifact but not printed until
`--reveal`, so it stays unread until Arm A has run.
"""

import argparse
import asyncio
import json
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from atlas import making
from atlas.repricing import canonical_club, mlb_game_join_key, parse_ticker

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
MLB = "https://statsapi.mlb.com/api/v1"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CHARTER = "docs/decisions/2026-09-08-market-making-charter.md"
TAPES = ROOT / "data" / "making" / "tapes"
BOOKS_BEGIN = datetime(2026, 8, 21, tzinfo=UTC)  # section 4: Arm A replays from here
ARM_A_SETTLE_BY = "2026-09-16"
# Arm B: the fourth charter's window and exclusion, unchanged (section 3).
SAMPLE_START = "2026-08-15"
SAMPLE_END = "2026-09-10"
EXCLUDED_GAMES = {("2026-09-03", frozenset({"STL", "LAD"}))}
REQUEST_PAUSE_SECONDS = 0.2


async def get_json(client: httpx.AsyncClient, url: str, params: dict | None = None) -> dict:
    for attempt in range(4):
        response = await client.get(url, params=params, timeout=30)
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            await asyncio.sleep(2 * (attempt + 1))
            continue
        response.raise_for_status()
        await asyncio.sleep(REQUEST_PAUSE_SECONDS)
        return response.json()
    raise RuntimeError(f"unreachable: {url}")


async def fetch_market(client: httpx.AsyncClient, ticker: str) -> dict:
    return (await get_json(client, f"{KALSHI}/markets/{ticker}")).get("market", {})


async def fetch_tape(client: httpx.AsyncClient, ticker: str, start: datetime, end: datetime) -> list[dict]:
    """Every print in [start, end], cursor-paged, cached on disk so re-runs never refetch."""
    TAPES.mkdir(parents=True, exist_ok=True)
    cache = TAPES / f"{ticker}__{int(start.timestamp())}__{int(end.timestamp())}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    rows, cursor = [], None
    while True:
        params = {"ticker": ticker, "limit": 1000,
                  "min_ts": int(start.timestamp()), "max_ts": int(end.timestamp())}
        if cursor:
            params["cursor"] = cursor
        payload = await get_json(client, f"{KALSHI}/markets/trades", params)
        batch = payload.get("trades", [])
        rows.extend(batch)
        cursor = payload.get("cursor")
        if not cursor or not batch:
            break
    cache.write_text(json.dumps(rows))
    return rows


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _result(market: dict) -> str | None:
    result = str(market.get("result") or "").lower()
    return result if result in ("yes", "no") else None


def load_books(db: Path, market_id: str, start: datetime, end: datetime) -> list[making.Book]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT timestamp, payload_json FROM orderbook_snapshots "
            "WHERE market_id = ? AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (market_id, start.isoformat(), end.isoformat()),
        ).fetchall()
    finally:
        con.close()
    return making.parse_books(rows)


def snapshot_markets(db: Path) -> dict[str, datetime]:
    """Every Kalshi market with snapshots, with its first snapshot time."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT market_id, MIN(timestamp) FROM orderbook_snapshots "
            "WHERE market_id LIKE 'kalshi:%' GROUP BY market_id"
        ).fetchall()
    finally:
        con.close()
    return {m: datetime.fromisoformat(t) for m, t in rows}


# --------------------------------------------------------------------- Arm A


async def arm_a_cases(client: httpx.AsyncClient, db: Path, settle_by: str) -> tuple[list[dict], list[dict]]:
    """Markets with books that settle by `settle_by` (section 3), and why others were left out."""
    deadline = datetime.fromisoformat(settle_by).replace(hour=23, minute=59, second=59, tzinfo=UTC)
    cases, log = [], []
    for market_id, first_seen in sorted(snapshot_markets(db).items()):
        ticker = market_id.removeprefix("kalshi:")
        market = await fetch_market(client, ticker)
        close = _when(market.get("close_time"))
        if not market or close is None:
            log.append({"market_id": market_id, "status": "NO_MARKET_RECORD"})
            continue
        if close > deadline:
            log.append({"market_id": market_id, "status": "SETTLES_AFTER_WINDOW", "close_time": close.isoformat()})
            continue
        window_start = max(first_seen, BOOKS_BEGIN)
        cases.append({"market_id": market_id, "ticker": ticker, "window_start": window_start,
                      "window_end": close, "result": _result(market), "status": market.get("status")})
    return cases, log


# --------------------------------------------------------------------- Arm B


async def settled_game_contracts(client: httpx.AsyncClient, exclude: bool = True) -> tuple[dict, list]:
    """join_key -> {canonical club: ticker}, for every settled contract in the window."""
    contracts: dict = defaultdict(dict)
    log, cursor = [], None
    while True:
        params = {"series_ticker": "KXMLBGAME", "status": "settled", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        payload = await get_json(client, f"{KALSHI}/markets", params)
        markets = payload.get("markets", [])
        for market in markets:
            parsed = parse_ticker(market["ticker"])
            if parsed is None:
                log.append({"ticker": market["ticker"], "status": "UNPARSEABLE_TICKER"})
                continue
            if not SAMPLE_START <= parsed.date_et <= SAMPLE_END:
                continue
            if exclude and parsed.join_key in EXCLUDED_GAMES:
                log.append({"ticker": market["ticker"], "status": "EXCLUDED_PROBE_GAME"})
                continue
            contracts[parsed.join_key][parsed.team] = market["ticker"]
        cursor = payload.get("cursor")
        if not cursor or not markets:
            break
    return contracts, log


async def mlb_games(client: httpx.AsyncClient) -> dict:
    """join_key -> {gamePk, home, away, gameDate} across the window, one day wider each side."""
    teams = await get_json(client, f"{MLB}/teams", {"sportId": 1})
    abbr = {team["id"]: team["abbreviation"] for team in teams.get("teams", [])}
    start = (datetime.fromisoformat(SAMPLE_START) - timedelta(days=1)).date().isoformat()
    end = (datetime.fromisoformat(SAMPLE_END) + timedelta(days=1)).date().isoformat()
    schedule = await get_json(client, f"{MLB}/schedule", {"sportId": 1, "startDate": start, "endDate": end})
    games = {}
    for day in schedule.get("dates", []):
        for game in day.get("games", []):
            away = abbr.get(game["teams"]["away"]["team"]["id"])
            home = abbr.get(game["teams"]["home"]["team"]["id"])
            if not away or not home:
                continue
            key = mlb_game_join_key(game["gameDate"], away, home)
            games[key] = {"gamePk": game["gamePk"], "home": canonical_club(home),
                          "away": canonical_club(away), "gameDate": game["gameDate"]}
    return games


async def arm_b_cases(client: httpx.AsyncClient, probe: bool = False) -> tuple[list[dict], list[dict]]:
    """One home-team contract per settled game in the window (section 3)."""
    contracts, log = await settled_game_contracts(client, exclude=not probe)
    games = await mlb_games(client)
    if probe:
        contracts = {k: v for k, v in contracts.items() if k in EXCLUDED_GAMES}
    cases = []
    for key, tickers in sorted(contracts.items()):
        game = games.get(key)
        if game is None:
            log.append({"game": [key[0], sorted(key[1])], "status": "UNJOINED"})
            continue
        ticker = tickers.get(game["home"])
        if ticker is None:
            log.append({"game": [key[0], sorted(key[1])], "status": "NO_HOME_CONTRACT"})
            continue
        market = await fetch_market(client, ticker)
        close = _when(market.get("close_time"))
        if close is None:
            log.append({"ticker": ticker, "status": "NO_CLOSE_TIME"})
            continue
        cases.append({"market_id": f"kalshi:{ticker}", "ticker": ticker,
                      "window_start": datetime.fromisoformat(game["gameDate"]), "window_end": close,
                      "result": _result(market), "status": market.get("status")})
    return cases, log


# ------------------------------------------------------------------- replay


def run_case(case: dict, arm: str, prints: list[making.Print], books, with_grid: bool) -> dict:
    primary = making.replay(case["market_id"], arm, prints, case["window_start"], case["window_end"],
                            case["result"], making.PRIMARY, books)
    slow = making.replay(case["market_id"], arm, prints, case["window_start"], case["window_end"],
                         case["result"], making.SLOW, books)
    grid = {}
    if with_grid:
        for params in making.grid():
            r = making.replay(case["market_id"], arm, prints, case["window_start"], case["window_end"],
                              case["result"], params, books)
            grid[params.label()] = r
    return {"primary": primary, "slow": slow, "grid": grid}


def summarise(arm: str, runs: list[dict], with_grid: bool) -> dict:
    primary = [r["primary"] for r in runs]
    slow = [r["slow"] for r in runs]
    out = {"verdict": making.verdict(arm, primary, slow), "grid": []}
    if with_grid and runs:
        for label in runs[0]["grid"]:
            out["grid"].append({"params": label, **making.criteria([r["grid"][label] for r in runs])})
    return out


def instrument_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arm", choices=["A", "B"])
    parser.add_argument("--probe", action="store_true", help="the excluded STL@LAD game only; validation")
    parser.add_argument("--settle-by", default=ARM_A_SETTLE_BY, help="Arm A: include markets settling by this date")
    parser.add_argument("--db", default=str(ROOT / "data" / "atlas.sqlite3"))
    parser.add_argument("--no-grid", action="store_true", help="skip the sensitivity grid")
    parser.add_argument("--reveal", action="store_true", help="print Arm B's verdict (section 11.3)")
    parser.add_argument("--out")
    args = parser.parse_args()
    if not args.arm and not args.probe:
        parser.error("choose --arm A, --arm B, or --probe")
    arm = "B" if args.probe else args.arm
    out_path = Path(args.out) if args.out else HERE / ("making-probe.json" if args.probe
                                                        else f"making-result-arm{arm}.json")
    started = time.time()
    async with httpx.AsyncClient(headers={"User-Agent": "atlas-making-replay/0.1"}) as client:
        if arm == "A":
            cases, log = await arm_a_cases(client, Path(args.db), args.settle_by)
        else:
            cases, log = await arm_b_cases(client, probe=args.probe)
        print(f"arm {arm}: {len(cases)} markets to replay, {len(log)} left out", flush=True)
        # Every tape is fetched before any replay: Kalshi keeps prints for about six
        # weeks, so a slow replay must never delay a fetch.
        tapes = []
        for n, case in enumerate(cases, start=1):
            tapes.append(await fetch_tape(client, case["ticker"], case["window_start"], case["window_end"]))
            if n % 25 == 0 or n == len(cases):
                print(f"  fetched {n}/{len(cases)} tapes", flush=True)
        runs, records = [], []
        for n, (case, rows) in enumerate(zip(cases, tapes, strict=True), start=1):
            prints = making.parse_prints(rows)
            books = load_books(Path(args.db), case["market_id"], case["window_start"], case["window_end"]) if arm == "A" else None
            run = run_case(case, arm, prints, books, with_grid=not args.no_grid)
            runs.append(run)
            record = run["primary"].to_dict()
            record["slow_net_pnl"] = str(run["slow"].net_pnl)
            record["slow_contracts_filled"] = str(run["slow"].contracts_filled)
            record["books_loaded"] = len(books) if books is not None else None
            record["prints_fetched"] = len(rows)
            records.append(record)
            print(f"  [{n}/{len(cases)}] {case['ticker']}: prints={run['primary'].prints_in_window} "
                  f"fills={len(run['primary'].fills)} settled={run['primary'].settled}", flush=True)
    summary = summarise(arm, runs, with_grid=not args.no_grid)
    artifact = {
        "charter": CHARTER,
        "arm": arm,
        "probe": args.probe,
        "settle_by": args.settle_by if arm == "A" else None,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "instrument_commit": instrument_commit(),
        "paper_only": True,
        "primary_params": making.PRIMARY.label(),
        "slow_params": making.SLOW.label(),
        "fee_examples": making.fee_examples(),
        "markets": records,
        "left_out": log,
        "verdict": summary["verdict"],
        "sensitivity_grid": summary["grid"],
        "runtime_seconds": round(time.time() - started, 1),
    }
    out_path.write_text(json.dumps(artifact, indent=1) + "\n")
    verdict = summary["verdict"]
    if arm == "B" and not args.probe and not args.reveal:
        print(f"wrote {out_path.name}: {verdict['sample']}. Verdict sealed until Arm A has run "
              "(charter section 11.3); re-run with --reveal to print it.")
        return 0
    print(f"wrote {out_path.name}: {verdict['sample']}")
    print(f"verdict: {verdict['verdict']}  primary: {verdict['primary']}  c4: {verdict['c4_survives_slow']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
