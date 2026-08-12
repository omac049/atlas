"""Bounded read-only diagnostic: why are all Fed Rates cross-venue pairs inconclusive?

Replicates the exact backfill pairing path (Kalshi settled events x Polymarket Global
tag-100196 final binaries -> verify_equivalence) but aggregates the mismatch codes the
batch report discards. No DB writes, no labels created.
"""

import asyncio
import json
from collections import Counter

from atlas.backfill import _finalize_polymarket_markets, historical_event_candidates
from atlas.validation import settled_outcome
from atlas.venues.kalshi import KalshiVenue
from atlas.venues.polymarket_global import PolymarketGlobalHistoricalVenue
from atlas.verification import verify_equivalence

TAG = "100196"


async def main() -> None:
    kalshi = KalshiVenue(fixture=False)
    globalpm = PolymarketGlobalHistoricalVenue(tag_ids=(TAG,))

    closed = await globalpm.list_closed_markets(max_pages=1)
    final = await _finalize_polymarket_markets(closed, globalpm, concurrency=8)
    events = await kalshi.list_settled_events(max_pages=100)
    candidates = historical_event_candidates(events, final)[:50]
    print(f"polymarket_closed={len(closed)} final_binary={len(final)} "
          f"kalshi_settled_events={len(events)} candidate_events={len(candidates)}")

    mismatch_counts: Counter[str] = Counter()
    pair_signatures: Counter[tuple] = Counter()
    statuses: Counter[str] = Counter()
    examples: dict[tuple, tuple[str, str]] = {}
    total_pairs = 0

    for event, pm_markets in candidates:
        ticker = str(event.get("event_ticker") or "")
        try:
            kalshi_markets = await kalshi.list_settled_event_markets(ticker)
        except (OSError, KeyError, TypeError, ValueError) as error:
            print(f"  fetch_failed event={ticker} {type(error).__name__}")
            continue
        print(f"  event={ticker} title={str(event.get('title'))[:60]!r} "
              f"kalshi_markets={len(kalshi_markets)} pm_markets={len(pm_markets)}")
        for left in kalshi_markets:
            if settled_outcome(left) is None:
                continue
            for right in pm_markets:
                pair = verify_equivalence(left, right, f"diag:{left.market_id}::{right.market_id}")
                total_pairs += 1
                statuses[pair.status.value] += 1
                codes = tuple(sorted(
                    pair.decision.mismatch_codes if pair.decision else pair.differences
                ))
                for code in codes:
                    mismatch_counts[code] += 1
                pair_signatures[codes] += 1
                if codes not in examples:
                    examples[codes] = (left.title[:70], right.title[:70])

    print(f"\ntotal_pairs={total_pairs}")
    print("statuses:", json.dumps(dict(statuses)))
    print("\ntop mismatch codes:")
    for code, count in mismatch_counts.most_common(15):
        print(f"  {count:5d}  {code}")
    print("\ntop signatures (code combos) with one example pair each:")
    for codes, count in pair_signatures.most_common(8):
        print(f"  {count:5d}  {list(codes)}")
        left_title, right_title = examples[codes]
        print(f"         e.g. {left_title!r} <-> {right_title!r}")


asyncio.run(main())
