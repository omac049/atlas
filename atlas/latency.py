"""Phase 2 of the 90-day study: latency-adjusted shadow execution. Paper-only.

The charter (docs/NINETY_DAY_STUDY.md) asks for every executable observation
to be replayed under execution delays of 250 ms, 500 ms, 1 s and 2 s "using the
next recorded quotes", with both legs required to remain fillable. The radar's
5-minute sweep records no quotes between observations, and the book stream
watches only the twelve Fed-decision markets, so no such quotes existed. This
module adds the missing instrument and the measurement it enables:

- ``burst_books``: the moment a radar pass records its first tradeable
  executable gap, both legs are sampled every 250 ms for 20 s and saved as
  ordinary snapshots through the ordinary path. Instrumentation only; no rule
  changes, and the observation itself is untouched.
- ``replay_observation``: for an observation that has burst data, the
  fee-adjusted basket is rebuilt from the latest book at or before each delay
  with the radar's own basket arithmetic, and judged fillable or not.

Nothing here places an order or reads a credential.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median

from atlas.gap_radar import _baskets
from atlas.models import OrderBook

DELAYS = (Decimal("0.25"), Decimal("0.5"), Decimal(1), Decimal(2))
BURST_SECONDS = 20.0
BURST_INTERVAL_SECONDS = 0.25
# One pair per radar pass: two legs at 4 requests/s stays inside both venues' public read limits.
LOOKAHEAD = timedelta(seconds=3)  # the widest delay plus a margin
DETAIL_ROWS = 500  # per-observation detail kept in the weekly artifact


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def burst_eligible(observation: dict) -> bool:
    """Only a tradeable, executable gap on the venue with a real book is worth
    sampling: everything else the replay could never judge."""
    return bool(
        observation.get("tradeable_venue_pair") and observation.get("executable_gap")
        and observation.get("polymarket_venue") == "polymarket_us"
    )


async def burst_books(
    kalshi, pmus, store, observation: dict,
    seconds: float = BURST_SECONDS,
    interval: float = BURST_INTERVAL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, int]:
    """Sample both legs' books on a fixed cadence for a bounded time.

    Each book is saved as it arrives, timestamped by the adapter at fetch time,
    so a replay can find "the latest quote at or before t + delay". One leg
    failing never stops the other; failures are counted, not raised.
    """
    kalshi_id = str(observation["kalshi_market_id"]).removeprefix("kalshi:")
    pmus_id = str(observation["polymarket_market_id"]).removeprefix("polymarket_us:")
    counts = {"kalshi": 0, "polymarket_us": 0, "errors": 0, "rounds": 0}
    deadline = clock() + seconds
    while clock() < deadline:
        started = clock()
        books = await asyncio.gather(
            kalshi.get_orderbook(kalshi_id), pmus.get_orderbook(pmus_id), return_exceptions=True
        )
        for venue, book in zip(("kalshi", "polymarket_us"), books, strict=True):
            if isinstance(book, BaseException):
                counts["errors"] += 1
                continue
            await store.save_orderbook(book)
            counts[venue] += 1
        counts["rounds"] += 1
        await sleep(max(0.0, interval - (clock() - started)))
    return counts


def book_quotes(book: OrderBook) -> dict[str, Decimal | None] | None:
    """Top-of-book asks in the radar's quote shape; None when nothing is offered."""
    yes = min(book.yes_asks, key=lambda level: level.price, default=None)
    no = min(book.no_asks, key=lambda level: level.price, default=None)
    if yes is None and no is None:
        return None
    return {
        "yes_ask": yes.price if yes else None,
        "no_ask": no.price if no else None,
        "yes_size": yes.quantity if yes else None,
        "no_size": no.quantity if no else None,
    }


def latest_after(books: list[OrderBook], start: datetime, until: datetime) -> OrderBook | None:
    """The newest book strictly after `start` and at or before `until`."""
    window = [b for b in books if start < b.timestamp <= until]
    return max(window, key=lambda b: b.timestamp) if window else None


def _leg_sides(legs: str) -> tuple[str, str]:
    kalshi_leg, polymarket_leg = legs.split("+")
    return kalshi_leg.removeprefix("kalshi_"), polymarket_leg.removeprefix("polymarket_")


def replay_observation(
    observation: dict,
    kalshi_books: list[OrderBook],
    pmus_books: list[OrderBook],
    delays: tuple[Decimal, ...] = DELAYS,
) -> dict:
    """One executable observation replayed at each delay against burst books.

    At each delay the latest book strictly after the observation and at or
    before observed_at + delay is used for each leg. "Survived" means both legs
    are still quoted with size and the fee-adjusted gap is still positive.
    """
    t0 = datetime.fromisoformat(str(observation["observed_at"]))
    legs = str(observation.get("best_basket") or "")
    fee_terms = observation.get("polymarket_fee_terms")
    out = {
        "observation_id": observation.get("observation_id"),
        "event_subject": observation.get("event_subject"),
        "observed_at": t0.isoformat(),
        "legs": legs,
        "original_gap": observation.get("best_gap"),
        "original_basket_size": observation.get("best_basket_size"),
        "delays": {},
    }
    if fee_terms is None or "+" not in legs:
        out["measurable"] = False
        out["reason"] = "no_fee_terms_recorded"
        return out
    kalshi_side, polymarket_side = _leg_sides(legs)
    original_size = _decimal(observation.get("best_basket_size"))
    days = _decimal((observation.get("settlement_timing") or {}).get("days_to_settlement"))
    for delay in delays:
        key = f"{delay}s"
        until = t0 + timedelta(seconds=float(delay))
        kalshi_book = latest_after(kalshi_books, t0, until)
        pmus_book = latest_after(pmus_books, t0, until)
        if kalshi_book is None or pmus_book is None:
            out["delays"][key] = {"measurable": False, "reason": "no_quote_within_delay"}
            continue
        kalshi = book_quotes(kalshi_book) or {}
        polymarket = book_quotes(pmus_book) or {}
        kalshi_quoted = kalshi.get(f"{kalshi_side}_ask") is not None
        polymarket_quoted = polymarket.get(f"{polymarket_side}_ask") is not None
        row = {
            "measurable": True,
            "kalshi_quote_at": kalshi_book.timestamp.isoformat(),
            "polymarket_quote_at": pmus_book.timestamp.isoformat(),
            "both_legs_quoted": kalshi_quoted and polymarket_quoted,
            "one_leg_only": kalshi_quoted != polymarket_quoted,
            "survived": False,
        }
        if kalshi_quoted and polymarket_quoted:
            basket = next(
                (b for b in _baskets(str(observation.get("shape")), kalshi, polymarket, fee_terms)
                 if b["legs"] == legs),
                None,
            )
            if basket is not None:
                gap = Decimal(basket["gap"])
                cost = Decimal(basket["cost"])
                size = _decimal(basket.get("basket_size"))
                fillable = size is not None and size > 0
                survived = fillable and gap > 0
                annualized = None
                if survived and days is not None and days > 0 and cost > 0:
                    annualized = ((gap / cost) * (Decimal(365) / days)).quantize(Decimal("0.0001"))
                row.update({
                    "gap_after": str(gap),
                    "basket_size_after": str(size) if size is not None else None,
                    "fillable_both": fillable,
                    "partial_fill": (
                        original_size is not None and size is not None and size < original_size
                    ),
                    "survived": survived,
                    "annualized_return": str(annualized) if annualized is not None else None,
                })
        out["delays"][key] = row
    out["measurable"] = any(v.get("measurable") for v in out["delays"].values())
    return out


def latency_summary(rows: list[dict], delays: tuple[Decimal, ...] = DELAYS) -> dict:
    """Per delay: how many observations could be measured, how many survived,
    how often only one leg was left, how often the size had shrunk."""
    summary = {}
    for delay in delays:
        key = f"{delay}s"
        measured = [r["delays"][key] for r in rows if r["delays"].get(key, {}).get("measurable")]
        survivors = [m for m in measured if m.get("survived")]
        gaps = [Decimal(m["gap_after"]) for m in measured if m.get("gap_after") is not None]
        returns = [Decimal(m["annualized_return"]) for m in survivors if m.get("annualized_return")]
        summary[key] = {
            "measurable": len(measured),
            "survived": len(survivors),
            "survival_share": (
                str((Decimal(len(survivors)) / len(measured)).quantize(Decimal("0.001")))
                if measured else None
            ),
            "one_leg_only": sum(1 for m in measured if m.get("one_leg_only")),
            "partial_fill": sum(1 for m in measured if m.get("partial_fill")),
            "median_gap_after": str(median(gaps)) if gaps else None,
            "median_annualized_return": str(median(returns)) if returns else None,
        }
    return summary


async def latency_report(store, delays: tuple[Decimal, ...] = DELAYS) -> dict:
    """The weekly phase-2 artifact, regenerable from the database."""
    observations = await store.all_gap_observations()
    eligible = [
        o for o in observations
        if o.get("polymarket_fee_terms") is not None and burst_eligible(o)
    ]
    rows = []
    for observation in eligible:
        t0 = datetime.fromisoformat(str(observation["observed_at"]))
        kalshi_books = await store.orderbooks_between(
            str(observation["kalshi_market_id"]), t0, t0 + LOOKAHEAD
        )
        pmus_books = await store.orderbooks_between(
            str(observation["polymarket_market_id"]), t0, t0 + LOOKAHEAD
        )
        if not kalshi_books and not pmus_books:
            continue  # no burst ran for this observation
        rows.append(replay_observation(observation, kalshi_books, pmus_books, delays))
    return {
        "charter": "docs/NINETY_DAY_STUDY.md",
        "phase": 2,
        "paper_only": True,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "delays_seconds": [str(d) for d in delays],
        "burst_seconds": BURST_SECONDS,
        "burst_interval_seconds": BURST_INTERVAL_SECONDS,
        "eligible_observations": len(eligible),
        "observations_with_bursts": len(rows),
        "summary": latency_summary(rows, delays),
        "observations": rows[-DETAIL_ROWS:],
    }
