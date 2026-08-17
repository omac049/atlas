"""Market-watch view over recorded gap observations.

The gap radar already records, for every twin-shaped candidate pair, the best
executable basket and its gap at a point in time. That is a time series per
event subject, but it was only ever surfaced as a handful of recent rows, so the
dashboard could not answer the questions an operator actually asks: what are we
watching, which way did it move, how wide has it been, and when did we last see it.

This module reshapes those observations into one row per subject. It is a pure
function over data already fetched for the bankroll summary — no extra I/O, no
venue calls — and it decides nothing: `verification_status` is carried through
verbatim from the deterministic verifier, and every row stays labelled a
candidate rather than a proven twin.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

# Enough points to read a shape without bloating the payload; the radar samples
# every monitor cycle (and every 30s inside a release window).
HISTORY_POINTS = 24

# A gap this small is inside the noise of quote timing and the fee buffer, so the
# board shows it as flat rather than implying a move that is not really there.
FLAT_GAP_DELTA = Decimal("0.0005")

# Change is measured against the open of a window, the way a market board reads,
# rather than only against the previous scan. `None` means "everything recorded".
WINDOWS: tuple[tuple[str, int | None], ...] = (
    ("1h", 1),
    ("24h", 24),
    ("7d", 168),
    ("all", None),
)
DEFAULT_WINDOW = "24h"

# A pair turning executable is the only genuinely time-sensitive event on the
# board — a gap that survives depth right now, and usually will not for long.
CROSSINGS_PER_ROW = 8
RECENT_CROSSING_HOURS = 24
RECENT_CROSSINGS_SHOWN = 12

# Live pairs flicker across the executable threshold on almost every scan (one
# observed pair produced 155 rising edges in five days, always at the same gap).
# Alerting on every edge would bury the next real one, so consecutive edges are
# folded into a single episode until the pair has been quiet for this long.
CROSSING_COOLDOWN_MINUTES = 30


def _decimal(raw: object) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_timestamp(raw: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _downsample(points: list[Decimal], limit: int = HISTORY_POINTS) -> list[str]:
    """Evenly spaced samples that always keep the first and last point.

    Truncating to the newest N would silently redraw a long window as a short
    one; a 7-day sparkline must actually span seven days.
    """
    if len(points) <= limit:
        return [str(point) for point in points]
    step = (len(points) - 1) / (limit - 1)
    sampled = [points[round(index * step)] for index in range(limit)]
    return [str(point) for point in sampled]


def _direction(delta: Decimal | None) -> str:
    if delta is None:
        return "NEW"
    if delta > FLAT_GAP_DELTA:
        return "WIDENING"
    if delta < -FLAT_GAP_DELTA:
        return "NARROWING"
    return "FLAT"


def _crossings(observations: list[dict]) -> list[dict[str, object]]:
    """Episodes where a pair became executable, not every threshold flicker.

    A pair that dips out of executable for one scan and back in has not produced
    two opportunities — it produced one that is still running. Each episode
    reports when it opened, when it was last seen, and the best gap reached
    inside it, so the strip says "since 20:09, peak +3.0¢" rather than repeating
    the same alert every few minutes.
    """
    episodes: list[dict[str, object]] = []
    cooldown = timedelta(minutes=CROSSING_COOLDOWN_MINUTES)
    current: dict[str, object] | None = None
    last_executable_at: datetime | None = None

    for observation in observations:
        if not observation.get("executable_gap"):
            continue
        observed = _parse_timestamp(observation.get("observed_at"))
        gap = _decimal(observation.get("best_gap"))
        continues = (
            current is not None
            and observed is not None
            and last_executable_at is not None
            and observed - last_executable_at <= cooldown
        )
        if continues and current is not None:
            current["observations"] = int(current["observations"]) + 1
            current["last_executable_at"] = str(observation.get("observed_at") or "")
            peak = _decimal(current["peak_gap"])
            if gap is not None and (peak is None or gap > peak):
                current["peak_gap"] = str(gap)
        else:
            current = {
                "observed_at": str(observation.get("observed_at") or ""),
                "last_executable_at": str(observation.get("observed_at") or ""),
                "best_gap": str(observation.get("best_gap") or ""),
                "peak_gap": str(gap) if gap is not None else None,
                "best_basket": str(observation.get("best_basket") or ""),
                "verification_status": str(observation.get("verification_status") or ""),
                "observations": 1,
            }
            episodes.append(current)
        if observed is not None:
            last_executable_at = observed
    return episodes


def _window_stats(observations: list[dict], hours: int | None, now: datetime) -> dict[str, object]:
    """Open/change/high/low/history for one subject inside one time window."""
    if hours is None:
        scoped = observations
    else:
        cutoff = now - timedelta(hours=hours)
        scoped = [
            observation
            for observation in observations
            if (parsed := _parse_timestamp(observation.get("observed_at"))) is not None
            and parsed >= cutoff
        ]
    gaps = [gap for gap in (_decimal(o.get("best_gap")) for o in scoped) if gap is not None]
    if not gaps:
        # A window with no readings reports emptiness instead of borrowing numbers
        # from outside it, which would make a stale pair look freshly observed.
        return {
            "observations": len(scoped),
            "open": None,
            "change": None,
            "direction": "NO_DATA",
            "high": None,
            "low": None,
            "executable_observations": 0,
            "history": [],
        }
    change = gaps[-1] - gaps[0]
    return {
        "observations": len(scoped),
        "open": str(gaps[0]),
        "change": str(change),
        "direction": _direction(change) if len(gaps) > 1 else "NEW",
        "high": str(max(gaps)),
        "low": str(min(gaps)),
        "executable_observations": sum(1 for o in scoped if o.get("executable_gap")),
        "history": _downsample(gaps),
    }


def _row(subject: str, observations: list[dict], now: datetime) -> dict[str, object]:
    """Collapse one subject's ordered observations into a single board row."""
    latest = observations[-1]
    crossings = _crossings(observations)
    gaps = [gap for gap in (_decimal(o.get("best_gap")) for o in observations) if gap is not None]
    latest_gap = _decimal(latest.get("best_gap"))
    previous_gap = gaps[-2] if len(gaps) > 1 else None
    delta = (
        latest_gap - previous_gap if latest_gap is not None and previous_gap is not None else None
    )
    executable_history = [bool(o.get("executable_gap")) for o in observations]
    return {
        "event_subject": subject,
        "shape": str(latest.get("shape") or ""),
        "verification_status": str(latest.get("verification_status") or ""),
        # Never let the board imply these are proven equivalents.
        "pair_kind": str(latest.get("pair_kind") or ""),
        "trusted": bool(latest.get("trusted")),
        "mismatch_codes": [str(code) for code in (latest.get("mismatch_codes") or [])],
        "kalshi_market_id": str(latest.get("kalshi_market_id") or ""),
        "kalshi_title": str(latest.get("kalshi_title") or ""),
        "polymarket_market_id": str(latest.get("polymarket_market_id") or ""),
        "polymarket_title": str(latest.get("polymarket_title") or ""),
        "best_basket": str(latest.get("best_basket") or ""),
        "best_gap": str(latest_gap) if latest_gap is not None else None,
        "previous_gap": str(previous_gap) if previous_gap is not None else None,
        "gap_delta": str(delta) if delta is not None else None,
        "direction": _direction(delta),
        "executable_now": bool(latest.get("executable_gap")),
        "executable_observations": sum(1 for flag in executable_history if flag),
        "widest_gap": str(max(gaps)) if gaps else None,
        "narrowest_gap": str(min(gaps)) if gaps else None,
        "observations": len(observations),
        "first_observed_at": str(observations[0].get("observed_at") or ""),
        "last_observed_at": str(latest.get("observed_at") or ""),
        # Oldest-to-newest, for a sparkline.
        "history": _downsample(gaps),
        # Per-window open/change/high/low so the board can show change against a
        # window open — the market-board convention — not only the previous scan.
        "windows": {
            name: _window_stats(observations, hours, now) for name, hours in WINDOWS
        },
        "crossings_total": len(crossings),
        "crossings": crossings[-CROSSINGS_PER_ROW:],
        "last_crossing_at": crossings[-1]["observed_at"] if crossings else None,
    }


def _sort_key(row: dict[str, object]) -> tuple:
    gap = _decimal(row.get("best_gap"))
    return (
        # Executable now leads the board: it is the only state that could ever matter.
        0 if row["executable_now"] else 1,
        # Rows with no readable gap sort last rather than pretending to be flat.
        -(gap if gap is not None else Decimal(-999)),
        str(row["event_subject"]),
    )


def build_watchlist(
    observations: list[dict],
    *,
    limit: int = 100,
    now: datetime | None = None,
    total_observations: int | None = None,
) -> dict[str, object]:
    """One row per event subject, widest live gap first.

    `total_observations` is how many exist in storage. When the caller loaded
    fewer, the all-time figures below are computed from a window rather than the
    whole record, and the report says so instead of quietly relabelling a partial
    range as all-time.
    """
    now = now or datetime.now(UTC)
    grouped: dict[str, list[dict]] = {}
    for observation in observations:
        subject = str(observation.get("event_subject") or "")
        if not subject:
            continue
        grouped.setdefault(subject, []).append(observation)
    for entries in grouped.values():
        entries.sort(key=lambda item: str(item.get("observed_at") or ""))

    rows = sorted((_row(subject, obs, now) for subject, obs in grouped.items()), key=_sort_key)
    executable = [row for row in rows if row["executable_now"]]
    widest = max(
        (gap for gap in (_decimal(row.get("best_gap")) for row in rows) if gap is not None),
        default=None,
    )
    return {
        "paper_only": True,
        "pairs_are_candidates_not_proven_twins": True,
        "tracked_subjects": len(rows),
        "executable_now": len(executable),
        "widest_gap": str(widest) if widest is not None else None,
        "observations_reviewed": len(observations),
        "observations_recorded": total_observations
        if total_observations is not None
        else len(observations),
        "history_truncated": bool(
            total_observations is not None and total_observations > len(observations)
        ),
        "history_points": HISTORY_POINTS,
        "windows": [name for name, _ in WINDOWS],
        "default_window": DEFAULT_WINDOW,
        "generated_at": now.isoformat(),
        "recent_crossings": _recent_crossings(rows, now),
        "crossing_window_hours": RECENT_CROSSING_HOURS,
        "rows": rows[:limit],
    }


def _recent_crossings(rows: list[dict[str, object]], now: datetime) -> list[dict[str, object]]:
    """Newest-first feed of pairs that turned executable inside the alert window."""
    cutoff = now - timedelta(hours=RECENT_CROSSING_HOURS)
    events: list[dict[str, object]] = []
    for row in rows:
        for crossing in row.get("crossings", []):
            # Filter on last activity, not on when the episode opened: a pair that
            # has been executable since yesterday is the most current alert there
            # is, and filtering by start time would hide exactly that case.
            observed = _parse_timestamp(crossing.get("last_executable_at"))
            if observed is None or observed < cutoff:
                continue
            events.append(
                {
                    "event_subject": row["event_subject"],
                    # Carried through so an alert can never read as an approval.
                    "verification_status": crossing.get("verification_status")
                    or row["verification_status"],
                    "still_executable": row["executable_now"],
                    **crossing,
                }
            )
    events.sort(key=lambda event: str(event["last_executable_at"]), reverse=True)
    return events[:RECENT_CROSSINGS_SHOWN]
