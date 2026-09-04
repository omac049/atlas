"""Execute the repricing-lag charter. Reproducible, committed, paper-only.

Joins every settled Kalshi `KXMLBGAME` contract in the charter's fixed window
to its MLB game, derives lead-changing plays from MLB's public play-by-play,
pulls each game's public trade tape, and measures every play with
`atlas.repricing`. Output: repricing-result.json beside this file, carrying
every per-play row with the trade ids that decided it, plus a resolution log
of every game that could not be joined or fetched.

Fixed by the charter (§3): the sample window, the one excluded game, and every
measurement parameter. Nothing here is tunable after the freeze commit.

    .venv/bin/python docs/proof/run_repricing.py                 # full sample
    .venv/bin/python docs/proof/run_repricing.py --probe-excluded  # joins only
"""

import asyncio
import json
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from atlas.repricing import (
    PlayMeasurement,
    canonical_club,
    evaluate,
    lead_changes,
    measure_play,
    mlb_game_join_key,
    parse_ticker,
    parse_trades,
)

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
MLB = "https://statsapi.mlb.com/api/v1"
HERE = Path(__file__).resolve().parent

# Charter §3. ET first-pitch dates, inclusive. Fixed before any tape was read.
SAMPLE_START = "2026-08-15"
SAMPLE_END = "2026-09-10"
# The single game probed to confirm the endpoints exist (§6). Never sampled.
EXCLUDED_GAMES = {("2026-09-03", frozenset({"STL", "LAD"}))}

TAPE_BEFORE_FIRST_PITCH = timedelta(minutes=15)
TAPE_AFTER_LAST_PLAY = timedelta(minutes=20)
REQUEST_PAUSE_SECONDS = 0.2


def _key(key) -> list:
    """A join key as [ET date, sorted clubs] rather than a repr of a frozenset."""
    date_et, clubs = key
    return [date_et, sorted(clubs)]


def _fmt(value):
    """Decimals to 4 places for the artifact; everything else as-is or str."""
    from decimal import Decimal

    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value.quantize(Decimal("0.0001")))
    return value if isinstance(value, (bool, int, list)) else str(value)


async def get_json(client: httpx.AsyncClient, url: str, params: dict | None = None) -> dict:
    """Bounded, polite GET: brief pause every call, backoff on 429, 5 tries."""
    for attempt in range(5):
        await asyncio.sleep(REQUEST_PAUSE_SECONDS)
        response = await client.get(url, params=params)
        if response.status_code == 429:
            await asyncio.sleep(5 * (attempt + 1))
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"rate limited five times: {url}")


async def settled_game_contracts(client: httpx.AsyncClient) -> tuple[dict, list]:
    """join_key -> {canonical club: ticker}, for every settled contract in window."""
    contracts: dict = defaultdict(dict)
    log = []
    cursor = None
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
            if parsed.join_key in EXCLUDED_GAMES:
                log.append({"ticker": market["ticker"], "status": "EXCLUDED_PROBE_GAME"})
                continue
            contracts[parsed.join_key][parsed.team] = market["ticker"]
        cursor = payload.get("cursor")
        if not cursor or not markets:
            break
    return contracts, log


async def mlb_games(client: httpx.AsyncClient) -> dict:
    """join_key -> {gamePk, home, away, gameDate} across the window."""
    teams = await get_json(client, f"{MLB}/teams", {"sportId": 1})
    abbr = {team["id"]: team["abbreviation"] for team in teams.get("teams", [])}
    # ET dates in the window can begin as early as UTC the same day and end
    # after midnight UTC, so the schedule is pulled one day wider each side.
    start = (datetime.fromisoformat(SAMPLE_START) - timedelta(days=1)).date().isoformat()
    end = (datetime.fromisoformat(SAMPLE_END) + timedelta(days=1)).date().isoformat()
    schedule = await get_json(
        client, f"{MLB}/schedule", {"sportId": 1, "startDate": start, "endDate": end}
    )
    games = {}
    for day in schedule.get("dates", []):
        for game in day.get("games", []):
            away = abbr.get(game["teams"]["away"]["team"]["id"])
            home = abbr.get(game["teams"]["home"]["team"]["id"])
            if not away or not home:
                continue
            key = mlb_game_join_key(game["gameDate"], away, home)
            games[key] = {
                "gamePk": game["gamePk"],
                "home": canonical_club(home),
                "away": canonical_club(away),
                "gameDate": game["gameDate"],
                "state": game.get("status", {}).get("detailedState"),
            }
    return games


async def trade_tape(client: httpx.AsyncClient, ticker: str, start: datetime, end: datetime):
    rows = []
    cursor = None
    while True:
        params = {
            "ticker": ticker,
            "limit": 1000,
            "min_ts": int(start.timestamp()),
            "max_ts": int(end.timestamp()),
        }
        if cursor:
            params["cursor"] = cursor
        payload = await get_json(client, f"{KALSHI}/markets/trades", params)
        batch = payload.get("trades", [])
        rows.extend(batch)
        cursor = payload.get("cursor")
        if not cursor or not batch:
            break
    return parse_trades(rows)


async def measure_game(client, key, tickers, game) -> tuple[list[dict], dict]:
    feed = await get_json(client, f"https://statsapi.mlb.com/api/v1.1/game/{game['gamePk']}/feed/live")
    plays = feed.get("liveData", {}).get("plays", {}).get("allPlays", [])
    changes = lead_changes(plays)
    if not changes:
        return [], {"key": _key(key), "status": "NO_LEAD_CHANGES", "plays": len(plays)}
    first_pitch = datetime.fromisoformat(game["gameDate"])
    last_end = max(change.t0 for change in changes)
    tape_start = first_pitch - TAPE_BEFORE_FIRST_PITCH
    tape_end = last_end + TAPE_AFTER_LAST_PLAY
    tapes = {}
    for club, ticker in tickers.items():
        try:
            tapes[club] = await trade_tape(client, ticker, tape_start, tape_end)
        except (httpx.HTTPError, RuntimeError) as exc:
            return [], {"key": _key(key), "status": "TAPE_FETCH_FAILED", "ticker": ticker, "error": type(exc).__name__}
    rows = []
    for change in changes:
        club = game[change.benefiting]
        ticker = tickers.get(club)
        if ticker is None:
            rows.append({"key": _key(key), "t0": change.t0.isoformat(), "status": "NO_CONTRACT_FOR_BENEFITING_CLUB", "club": club})
            continue
        measurement = measure_play(tapes[club], change.t0, ticker)
        rows.append({
            "key": _key(key), "gamePk": game["gamePk"], "inning": change.inning, "half": change.half,
            "event": change.event, "score_before": change.score_before, "score_after": change.score_after,
            "benefiting": club, **{k: _fmt(v) for k, v in vars(measurement).items()
                                    if k != "stale_trade_ids"},
            "stale_trade_ids": measurement.stale_trade_ids,
            "_measurement": measurement,
        })
    return rows, {"key": _key(key), "status": "OK", "lead_changes": len(changes),
                  "tape_prints": {club: len(tape) for club, tape in tapes.items()}}


async def main(probe_excluded: bool) -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        contracts, log = await settled_game_contracts(client)
        games = await mlb_games(client)
        if probe_excluded:
            key = next(iter(EXCLUDED_GAMES))
            # Re-list the excluded game's contracts explicitly for the probe.
            payload = await get_json(client, f"{KALSHI}/markets", {"series_ticker": "KXMLBGAME", "status": "settled", "limit": 200})
            tickers = {}
            for market in payload.get("markets", []):
                parsed = parse_ticker(market["ticker"])
                if parsed and parsed.join_key == key:
                    tickers[parsed.team] = market["ticker"]
            print("probe game:", key, "| contracts:", tickers, "| mlb:", games.get(key))
            rows, entry = await measure_game(client, key, tickers, games[key])
            print("resolution:", entry)
            for row in rows:
                row.pop("_measurement", None)
                print(json.dumps(row, default=str)[:400])
            print("PROBE ONLY — nothing written.")
            return

        joined = {key: (tickers, games[key]) for key, tickers in contracts.items() if key in games}
        unjoined = [_key(key) for key in contracts if key not in games]
        print(f"contracts in window: {len(contracts)} games | joined to MLB: {len(joined)} | unjoined: {len(unjoined)}")
        all_rows, measurements, resolution = [], [], list(log)
        games_covered = 0
        for index, (key, (tickers, game)) in enumerate(sorted(joined.items()), start=1):
            try:
                rows, entry = await measure_game(client, key, tickers, game)
            except (httpx.HTTPError, RuntimeError) as exc:
                rows, entry = [], {"key": _key(key), "status": "GAME_FAILED", "error": type(exc).__name__}
            resolution.append(entry)
            if entry.get("status") == "OK":
                games_covered += 1
            for row in rows:
                measurement = row.pop("_measurement", None)
                if isinstance(measurement, PlayMeasurement):
                    measurements.append(measurement)
                all_rows.append(row)
            if index % 10 == 0:
                print(f"progress: {index}/{len(joined)} games, {len(measurements)} plays, {time.strftime('%H:%M:%S')}", flush=True)
        report = evaluate(measurements, games_covered)
        report["sample_window_et"] = [SAMPLE_START, SAMPLE_END]
        report["excluded_games"] = [list(k) for k in EXCLUDED_GAMES]
        report["unjoined_games"] = unjoined
        report["resolution_log"] = resolution
        report["plays"] = all_rows
        report["run_at"] = datetime.now(UTC).isoformat()
        out = HERE / "repricing-result.json"
        out.write_text(json.dumps(report, indent=2, default=str) + "\n")
        print(f"result written: {out}")
        print(f"outcome={report['outcome']} plays={report['plays_measurable']}/{report['plays_total']} games={games_covered}")
        print(f"median_lag={report['median_lag_seconds']}s negative_lags={report['negative_lag_plays']} no_reprice={report['no_reprice_plays']}")
        print(f"stale_share={report['stale_play_share']} median_net_gap={report['median_net_gap']}")
        print(f"criteria={report['criteria']}")


if __name__ == "__main__":
    asyncio.run(main(probe_excluded="--probe-excluded" in sys.argv))
