"""Early-exit model: what if a paired basket is unwound in-market, not held?

Every return-on-locked-capital number in the study assumes HOLD TO
SETTLEMENT: capital stays locked until the later leg's published anchor,
which is why 2c gaps on 150-day contracts annualize below the risk-free
rate. This module replays the recorded gap-observation series and asks the
one question that could change that verdict: after entering an executable
basket, how soon could BOTH legs have been sold back into the books at a
price that locks in the edge?

The unwind is priced without any new data, from an identity in Atlas's own
book normalization. Both adapters derive complement quotes as ``1 - price``
from the same side of the same book, so the bid on a held leg equals one
minus the ask of its complement leg. Selling both legs of basket D at bid
therefore grosses exactly ``2 - cost(complement basket)`` — and the
complement basket's cost is already recorded on every observation. Both
venues also publish quadratic taker fees (``rate * p * (1-p)``), symmetric
in ``p <-> 1-p``, so the recorded complement fees ARE the exit fees.

MEASUREMENT ONLY. This module reads recorded observations and reports
distributions; it never places, simulates placing, or scaffolds placing an
order, and nothing here feeds approval labels or the trading gate.

Honesty rules, encoded below and stated in the report's ``assumptions``:

- Entries require the recorded net gap to clear one full tick
  (``MIN_ENTRY_GAP``) — sub-tick "edges" are quantization noise.
- Exit prices are top-of-book; depth is not modeled. The report says so.
- Annualized figures floor the holding period at one day, because a
  20-minute round trip annualizes to five digits and a research meter must
  never round in its own favor.
- Entries whose series ends without an exit are reported as CENSORED, not
  dropped and not counted as failures.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from statistics import median

MIN_ENTRY_GAP = Decimal("0.01")
DEFAULT_THRESHOLDS = (Decimal("0.5"), Decimal("0.8"), Decimal("1.0"))
_MIN_ANNUALIZE_DAYS = Decimal(1)
_Q4 = Decimal("0.0001")
_DAY_SECONDS = Decimal(86400)


def _decimal(raw: object) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _parse_at(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _basket_by_legs(observation: dict) -> dict[str, dict]:
    return {
        str(basket.get("legs") or ""): basket
        for basket in observation.get("baskets") or []
        if isinstance(basket, dict)
    }


def _basket_fees(basket: dict) -> Decimal | None:
    kalshi = _decimal(basket.get("kalshi_fee"))
    polymarket = _decimal(basket.get("polymarket_fee"))
    if kalshi is None or polymarket is None:
        return None
    return kalshi + polymarket


def _complement_legs(legs: str) -> str | None:
    return {
        "kalshi_yes+polymarket_no": "kalshi_no+polymarket_yes",
        "kalshi_no+polymarket_yes": "kalshi_yes+polymarket_no",
    }.get(legs)


def _family(observation: dict) -> str:
    return str(observation.get("event_subject") or "").split("|")[0]


def _exit_pnl(entry: dict, later: dict, legs: str) -> Decimal | None:
    """Net P&L of unwinding basket ``legs`` (entered at ``entry``) at ``later``.

    Gross unwind proceeds are ``2 - cost(complement)`` by the book-complement
    identity; exit fees are the complement basket's recorded fees by the
    quadratic-fee symmetry. Entry cost and entry fees come from the entry
    observation's own basket.
    """
    comp_legs = _complement_legs(legs)
    if comp_legs is None:
        return None
    entry_basket = _basket_by_legs(entry).get(legs)
    comp_basket = _basket_by_legs(later).get(comp_legs)
    if not entry_basket or not comp_basket:
        return None
    entry_cost = _decimal(entry_basket.get("cost"))
    entry_fees = _basket_fees(entry_basket)
    comp_cost = _decimal(comp_basket.get("cost"))
    exit_fees = _basket_fees(comp_basket)
    if None in (entry_cost, entry_fees, comp_cost, exit_fees):
        return None
    return (Decimal(2) - comp_cost - exit_fees) - (entry_cost + entry_fees)


def _entry_stats(entry: dict, legs: str) -> tuple[Decimal, Decimal, Decimal | None] | None:
    """(net gap, total capital in, hold-to-settlement annualized) at entry."""
    basket = _basket_by_legs(entry).get(legs)
    if not basket:
        return None
    gap = _decimal(basket.get("gap"))
    cost = _decimal(basket.get("cost"))
    fees = _basket_fees(basket)
    if gap is None or cost is None or fees is None or cost + fees <= 0:
        return None
    capital = cost + fees
    timing = entry.get("settlement_timing") or {}
    days = _decimal(timing.get("days_to_settlement"))
    hold_annualized = None
    if days is not None and days > 0:
        hold_annualized = (gap / capital) * (Decimal(365) / days)
    return gap, capital, hold_annualized


def _simulate_direction(series: list[dict], legs: str, threshold: Decimal) -> list[dict]:
    """Walk one pair's series once for one direction and one exit threshold.

    One open position at a time: enter at the first observation whose recorded
    net gap clears ``MIN_ENTRY_GAP``, exit at the first later observation where
    the locked-in unwind P&L reaches ``threshold`` of the entry gap, then allow
    re-entry. A position still open when the series ends is censored.
    """
    events: list[dict] = []
    open_entry: dict | None = None
    open_stats: tuple[Decimal, Decimal, Decimal | None] | None = None
    for observation in series:
        if open_entry is None:
            stats = _entry_stats(observation, legs)
            if stats is not None and stats[0] >= MIN_ENTRY_GAP:
                open_entry, open_stats = observation, stats
            continue
        pnl = _exit_pnl(open_entry, observation, legs)
        if pnl is None:
            continue
        gap, capital, hold_annualized = open_stats
        if pnl >= gap * threshold:
            entered = _parse_at(open_entry.get("observed_at"))
            exited = _parse_at(observation.get("observed_at"))
            if entered is None or exited is None or exited <= entered:
                open_entry, open_stats = None, None
                continue
            held_days = Decimal((exited - entered).total_seconds()) / _DAY_SECONDS
            floored = max(held_days, _MIN_ANNUALIZE_DAYS)
            events.append(
                {
                    "outcome": "exited",
                    "family": _family(open_entry),
                    "entry_gap": gap,
                    "exit_pnl": pnl,
                    "held_days": held_days,
                    "annualized": (pnl / capital) * (Decimal(365) / floored),
                    "hold_annualized": hold_annualized,
                }
            )
            open_entry, open_stats = None, None
    if open_entry is not None:
        entered = _parse_at(open_entry.get("observed_at"))
        last = _parse_at(series[-1].get("observed_at"))
        censored_days = None
        if entered is not None and last is not None and last > entered:
            censored_days = Decimal((last - entered).total_seconds()) / _DAY_SECONDS
        gap, _capital, hold_annualized = open_stats
        events.append(
            {
                "outcome": "censored",
                "family": _family(open_entry),
                "entry_gap": gap,
                "censored_days": censored_days,
                "hold_annualized": hold_annualized,
            }
        )
    return events


def _quantized_median(values: list[Decimal]) -> str | None:
    if not values:
        return None
    return str(median(values).quantize(_Q4))


def _threshold_summary(events: list[dict]) -> dict:
    exited = [event for event in events if event["outcome"] == "exited"]
    censored = [event for event in events if event["outcome"] == "censored"]
    entries = len(events)
    return {
        "entries": entries,
        "exited": len(exited),
        "exited_share": (
            str((Decimal(len(exited)) / Decimal(entries)).quantize(_Q4)) if entries else None
        ),
        "median_days_to_exit": _quantized_median([e["held_days"] for e in exited]),
        "median_exit_pnl": _quantized_median([e["exit_pnl"] for e in exited]),
        "median_annualized": _quantized_median([e["annualized"] for e in exited]),
        "median_hold_annualized": _quantized_median(
            [e["hold_annualized"] for e in events if e["hold_annualized"] is not None]
        ),
        "censored": len(censored),
        "median_censored_days": _quantized_median(
            [e["censored_days"] for e in censored if e["censored_days"] is not None]
        ),
        "by_family": {
            family: {
                "entries": len(rows),
                "exited": len([e for e in rows if e["outcome"] == "exited"]),
                "median_days_to_exit": _quantized_median(
                    [e["held_days"] for e in rows if e["outcome"] == "exited"]
                ),
                "median_annualized": _quantized_median(
                    [e["annualized"] for e in rows if e["outcome"] == "exited"]
                ),
            }
            for family in sorted({e["family"] for e in events})
            if (rows := [e for e in events if e["family"] == family])
        },
    }


def early_exit_model(
    observations: list[dict], thresholds: tuple[Decimal, ...] = DEFAULT_THRESHOLDS
) -> dict:
    """Replay recorded observations under first-crossing early-exit rules.

    Reported per threshold: exit at the first moment the unwind locks in at
    least that fraction of the entry gap. ``median_hold_annualized`` sits
    alongside each threshold so the early-exit and hold-to-settlement numbers
    for the SAME entries are always read together.
    """
    series_by_pair: dict[tuple[str, str], list[dict]] = {}
    for observation in observations:
        key = (
            str(observation.get("kalshi_market_id") or ""),
            str(observation.get("polymarket_market_id") or ""),
        )
        if all(key):
            series_by_pair.setdefault(key, []).append(observation)
    for series in series_by_pair.values():
        series.sort(key=lambda o: str(o.get("observed_at") or ""))
    directions = ("kalshi_yes+polymarket_no", "kalshi_no+polymarket_yes")
    result: dict = {
        "paper_only": True,
        "assumptions": {
            "exit_pricing": "top_of_book_complement_identity",
            "exit_fees": "complement_basket_recorded_fees_quadratic_symmetry",
            "min_entry_gap": str(MIN_ENTRY_GAP),
            "annualize_floor_days": str(_MIN_ANNUALIZE_DAYS),
            "depth_modeled": False,
        },
        "pairs": len(series_by_pair),
        "thresholds": {},
    }
    for threshold in thresholds:
        events: list[dict] = []
        for series in series_by_pair.values():
            for legs in directions:
                events.extend(_simulate_direction(series, legs, threshold))
        result["thresholds"][str(threshold)] = _threshold_summary(events)
    return result
