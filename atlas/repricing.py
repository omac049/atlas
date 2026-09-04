"""Repricing lag — does Kalshi's in-game price lag the game?

Fourth hypothesis, pre-registered in
``docs/decisions/2026-09-04-repricing-lag-charter.md``. The first three asked
whether a price is RIGHT; this asks WHEN it becomes right. After a
lead-changing MLB play, does the moneyline keep trading at pre-play prices
long enough, at enough size, and by a wide enough margin over fees, that a
participant holding only the public play-by-play feed could have taken the
stale price?

Retrospective and paper-only without exception: it reads two public tapes —
Kalshi's trade prints and MLB's play-by-play — and places, simulates, and
signs nothing. A PROVEN result opens only a paper-only shadow charter.

READ THIS BEFORE USING ANYTHING HERE.

- Every parameter below is fixed by the charter (§4–§5) and frozen at the
  commit whose hash the charter records. Changing one is a new instrument.
- Each per-play measurement carries the trade ids and timestamps that decided
  it, so any headline number traces back to specific prints on the tape.
- The confounds are structural and all cut AGAINST the theory: MLB's stringer
  timestamp is itself late (fast makers produce NEGATIVE lags, recorded as-is,
  never clipped); trades are a lower bound on stale liquidity; block trades
  are excluded because they are negotiated, not taken.
- One-way import: nothing in the approval pipeline may import this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median
from zoneinfo import ZoneInfo

from atlas.gap_radar import kalshi_taker_fee_per_contract

SCORING_VERSION = "1.0"

# --- Charter §4: per-play measurement parameters ---------------------------
MOVE_THRESHOLD = Decimal("0.05")  # a reprice is a trade >= P0 + 5c
STALE_BAND = Decimal("0.02")  # a stale fill is a trade within 2c of P0
HUMAN_FLOOR_SECONDS = 5  # fastest realistic reaction with the public feed
STALE_WINDOW_SECONDS = 60  # stale fills counted in [5s, 60s] after T0
VWAP_TRADES = 20  # P0 / P1 are VWAPs over 20 prints
# P0 is taken from prints that end this long BEFORE T0. Without the gap a
# market maker on a faster feed who reprices seconds before the stringer
# finalizes the play gets absorbed into P0, the move vanishes, and the theory
# is CREDITED for a lag it lost. Excluding the last 30s lets that early move
# show up as what it is — a negative lag, recorded as-is (charter §7).
PRE_WINDOW_GAP_SECONDS = 30
MIN_VWAP_TRADES = 5  # fewer than this and the play is unmeasurable, not zero
NO_REPRICE_CAP_SECONDS = 300  # a play that never reprices counts at 300s

# --- Charter §5: pass criteria ---------------------------------------------
MIN_PLAYS = 100
MIN_GAMES = 50
MIN_MEDIAN_LAG_SECONDS = Decimal(5)
MIN_STALE_CONTRACTS = Decimal(20)
MIN_STALE_PLAY_SHARE = Decimal("0.50")
MIN_MEDIAN_NET_GAP = Decimal("0.03")

_ET = ZoneInfo("America/New_York")

# Kalshi and MLB spell a few clubs differently. Both sides are normalized
# through this table before joining; anything unmatched is reported as
# UNJOINED, never guessed.
_CLUB_ALIASES = {
    "ARI": "AZ", "CHW": "CWS", "KCR": "KC", "SDP": "SD", "SFG": "SF",
    "TBR": "TB", "WAS": "WSH", "OAK": "ATH",
}

_TICKER = re.compile(
    r"^(?P<event>KXMLBGAME-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})"
    r"(?P<hhmm>\d{4})(?P<clubs>[A-Z]+))-(?P<team>[A-Z]+)$"
)
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"),
        start=1,
    )
}


def canonical_club(code: str) -> str:
    code = code.upper()
    return _CLUB_ALIASES.get(code, code)


@dataclass(frozen=True)
class ParsedTicker:
    ticker: str
    event_ticker: str
    date_et: str  # YYYY-MM-DD, Eastern — the ticker's date/time are ET
    time_et: str  # HHMM
    team: str  # canonical club whose win this contract pays on
    opponent: str  # canonical club on the other side

    @property
    def join_key(self) -> tuple[str, frozenset[str]]:
        return (self.date_et, frozenset({self.team, self.opponent}))


def parse_ticker(ticker: str) -> ParsedTicker | None:
    """`KXMLBGAME-26SEP032210STLLAD-STL` -> date 2026-09-03 ET, STL vs LAD.

    The two club codes are concatenated without a separator and vary in
    length, so the contract's own suffix is used to split them: the suffix
    must be a prefix or suffix of the concatenation, and whatever remains is
    the opponent. Anything else is unparseable and reported, not guessed.
    """
    found = _TICKER.match(ticker)
    if not found:
        return None
    clubs, team = found["clubs"], found["team"]
    if clubs.startswith(team) and len(clubs) > len(team):
        opponent = clubs[len(team):]
    elif clubs.endswith(team) and len(clubs) > len(team):
        opponent = clubs[: -len(team)]
    else:
        return None
    month = _MONTHS.get(found["mon"])
    if month is None:
        return None
    date_et = f"20{found['yy']}-{month:02d}-{int(found['dd']):02d}"
    return ParsedTicker(
        ticker=ticker,
        event_ticker=found["event"],
        date_et=date_et,
        time_et=found["hhmm"],
        team=canonical_club(team),
        opponent=canonical_club(opponent),
    )


def mlb_game_join_key(game_date_utc: str, away_abbr: str, home_abbr: str) -> tuple[str, frozenset[str]]:
    """The MLB schedule's UTC `gameDate` re-expressed as an Eastern date."""
    when = datetime.fromisoformat(game_date_utc).astimezone(_ET)
    return (when.date().isoformat(), frozenset({canonical_club(away_abbr), canonical_club(home_abbr)}))


@dataclass(frozen=True)
class LeadChange:
    t0: datetime  # MLB `about.endTime` — when the play was finalized
    benefiting: str  # "home" | "away": the club whose score rose
    inning: int
    half: str
    event: str
    score_before: tuple[int, int]  # (home, away)
    score_after: tuple[int, int]


def lead_changes(plays: list[dict]) -> list[LeadChange]:
    """Plays whose result changes which club leads, derived from scores only.

    Sign of (home - away) before vs after. A change to or from a tie counts.
    The benefiting club is the one whose score rose on the play — there is no
    interpretation in this, only subtraction.
    """
    changes: list[LeadChange] = []
    previous = (0, 0)
    for play in plays:
        result = play.get("result") or {}
        about = play.get("about") or {}
        current = (int(result.get("homeScore") or 0), int(result.get("awayScore") or 0))
        if current == previous:
            continue
        sign_before = (previous[0] > previous[1]) - (previous[0] < previous[1])
        sign_after = (current[0] > current[1]) - (current[0] < current[1])
        end_time = about.get("endTime")
        if sign_before != sign_after and end_time:
            changes.append(
                LeadChange(
                    t0=datetime.fromisoformat(end_time),
                    benefiting="home" if current[0] > previous[0] else "away",
                    inning=int(about.get("inning") or 0),
                    half=str(about.get("halfInning") or ""),
                    event=str(result.get("event") or ""),
                    score_before=previous,
                    score_after=current,
                )
            )
        previous = current
    return changes


@dataclass(frozen=True)
class Trade:
    trade_id: str
    at: datetime
    yes_price: Decimal
    count: Decimal
    is_block: bool


def parse_trades(rows: list[dict]) -> list[Trade]:
    """Kalshi `/markets/trades` rows -> sorted, typed, block trades kept but
    flagged (every metric excludes them explicitly)."""
    trades = []
    for row in rows:
        try:
            trades.append(
                Trade(
                    trade_id=str(row.get("trade_id")),
                    at=datetime.fromisoformat(str(row["created_time"])),
                    yes_price=Decimal(str(row["yes_price_dollars"])),
                    count=Decimal(str(row.get("count_fp") or "0")),
                    is_block=bool(row.get("is_block_trade")),
                )
            )
        except (KeyError, ValueError, ArithmeticError):
            continue
    return sorted(trades, key=lambda trade: trade.at)


def _vwap(trades: list[Trade]) -> Decimal | None:
    volume = sum((trade.count for trade in trades), Decimal(0))
    if volume <= 0:
        return None
    return sum((trade.yes_price * trade.count for trade in trades), Decimal(0)) / volume


@dataclass
class PlayMeasurement:
    t0: str
    benefiting_ticker: str
    pre_price: Decimal | None = None
    repricing_lag_seconds: Decimal | None = None
    repriced_trade_id: str | None = None
    no_reprice: bool = False
    stale_contracts: Decimal = Decimal(0)
    stale_trade_ids: list[str] = field(default_factory=list)
    post_price: Decimal | None = None
    net_gap: Decimal | None = None
    unmeasurable: str | None = None


def measure_play(trades: list[Trade], t0: datetime, benefiting_ticker: str) -> PlayMeasurement:
    """Charter §4, applied to the BENEFITING club's contract tape.

    `trades` must be that contract's prints, sorted. Block trades are excluded
    from every metric. Insufficient tape on either side of T0 makes the play
    unmeasurable — reported as such, never scored as zero lag or zero gap.
    """
    taken = [trade for trade in trades if not trade.is_block]
    pre_cutoff = t0 - timedelta(seconds=PRE_WINDOW_GAP_SECONDS)
    before = [trade for trade in taken if trade.at < pre_cutoff]
    # The reprice search starts at the pre-window cutoff, not at T0, so a move
    # that beat the public feed lands as a negative lag instead of disappearing.
    search = [trade for trade in taken if trade.at >= pre_cutoff]
    after = [trade for trade in taken if trade.at >= t0]
    result = PlayMeasurement(t0=t0.isoformat(), benefiting_ticker=benefiting_ticker)

    pre = before[-VWAP_TRADES:]
    if len(pre) < MIN_VWAP_TRADES:
        result.unmeasurable = "INSUFFICIENT_PRE_TAPE"
        return result
    p0 = _vwap(pre)
    if p0 is None:
        result.unmeasurable = "INSUFFICIENT_PRE_TAPE"
        return result
    result.pre_price = p0

    cap = t0 + timedelta(seconds=NO_REPRICE_CAP_SECONDS)
    repriced = next(
        (trade for trade in search if trade.at <= cap and trade.yes_price >= p0 + MOVE_THRESHOLD),
        None,
    )
    if repriced is None:
        result.no_reprice = True
        result.repricing_lag_seconds = Decimal(NO_REPRICE_CAP_SECONDS)
    else:
        result.repriced_trade_id = repriced.trade_id
        result.repricing_lag_seconds = Decimal(
            str(round((repriced.at - t0).total_seconds(), 3))
        )

    window_start = t0 + timedelta(seconds=HUMAN_FLOOR_SECONDS)
    window_end = t0 + timedelta(seconds=STALE_WINDOW_SECONDS)
    stale = [
        trade
        for trade in after
        if window_start <= trade.at <= window_end and abs(trade.yes_price - p0) <= STALE_BAND
    ]
    result.stale_contracts = sum((trade.count for trade in stale), Decimal(0))
    result.stale_trade_ids = [trade.trade_id for trade in stale]

    post = [trade for trade in after if trade.at > window_end][:VWAP_TRADES]
    if len(post) < MIN_VWAP_TRADES:
        result.unmeasurable = "INSUFFICIENT_POST_TAPE"
        return result
    p1 = _vwap(post)
    if p1 is None:
        result.unmeasurable = "INSUFFICIENT_POST_TAPE"
        return result
    result.post_price = p1
    result.net_gap = p1 - p0 - kalshi_taker_fee_per_contract(p0)
    return result


def evaluate(measurements: list[PlayMeasurement], games_covered: int) -> dict:
    """Charter §5: three criteria, all required, on an adequate sample."""
    measurable = [m for m in measurements if m.unmeasurable is None]
    adequate = len(measurable) >= MIN_PLAYS and games_covered >= MIN_GAMES

    lags = [m.repricing_lag_seconds for m in measurable if m.repricing_lag_seconds is not None]
    gaps = [m.net_gap for m in measurable if m.net_gap is not None]
    stale_plays = [m for m in measurable if m.stale_contracts >= MIN_STALE_CONTRACTS]

    median_lag = median(lags) if lags else None
    median_gap = median(gaps) if gaps else None
    stale_share = (
        Decimal(len(stale_plays)) / Decimal(len(measurable)) if measurable else None
    )
    negative_lags = sum(1 for lag in lags if lag < 0)

    criteria = {
        "median_lag_ge_5s": median_lag is not None and median_lag >= MIN_MEDIAN_LAG_SECONDS,
        "stale_fills_20_contracts_on_50pct_of_plays": (
            stale_share is not None and stale_share >= MIN_STALE_PLAY_SHARE
        ),
        "median_net_gap_ge_3c": median_gap is not None and median_gap >= MIN_MEDIAN_NET_GAP,
    }
    return {
        "scoring_version": SCORING_VERSION,
        "charter": "docs/decisions/2026-09-04-repricing-lag-charter.md",
        "plays_total": len(measurements),
        "plays_measurable": len(measurable),
        "plays_unmeasurable": {
            reason: sum(1 for m in measurements if m.unmeasurable == reason)
            for reason in sorted({m.unmeasurable for m in measurements if m.unmeasurable})
        },
        "games_covered": games_covered,
        "adequate_sample": adequate,
        "median_lag_seconds": str(median_lag) if median_lag is not None else None,
        # Negative lags mean the market moved BEFORE the stringer finalized the
        # play — a fast maker beat the public feed. Counted against the theory.
        "negative_lag_plays": negative_lags,
        "no_reprice_plays": sum(1 for m in measurable if m.no_reprice),
        "stale_play_share": str(stale_share.quantize(Decimal("0.001"))) if stale_share else None,
        "median_net_gap": str(median_gap.quantize(Decimal("0.0001"))) if median_gap else None,
        "criteria": criteria,
        "outcome": (
            "PROVEN" if adequate and all(criteria.values())
            else "DISPROVEN" if adequate
            else "INCONCLUSIVE_SAMPLE_TOO_SMALL"
        ),
    }
