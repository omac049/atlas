"""Execute Test A of the fine-print proof charter. Reproducible, committed.

Reads the locked corpus (test-a-corpus.json), resolves each gradeable dispute
against the venues, assembles control pools by the charter's rule, and runs
``atlas.proof.evaluate_test_a``. Output: test-a-result.json beside this file.

Operational decisions, fixed HERE before any grade was computed:

- "Category" for Polymarket is the venue's own taxonomy: the FIRST tag on the
  disputed market's event (Gamma orders tags; taking the first is
  deterministic). Control candidates come from ``/markets?closed=true&tag_id=…``
  in a ±45-day end-date window around the dispute's end date, widened once to
  ±120 days if fewer than 6 candidates return. No other widening.
- Kalshi disputes are resolved by bounded series-ticker probes. Kalshi prunes
  settled-market detail, so unresolvable disputes are expected; they are
  recorded as documented-but-ungradeable, never dropped silently.
- Disputed markets are graded from the SAME normalized form controls use
  (the Global adapter's normalizer), so no arm gets a friendlier parser.

Run:  .venv/bin/python docs/proof/run_test_a.py
"""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from atlas.proof import CONTROLS_PER_DISPUTE, evaluate_test_a, select_controls
from atlas.venues.polymarket_global import PolymarketGlobalHistoricalVenue

GAMMA = "https://gamma-api.polymarket.com"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
HERE = Path(__file__).resolve().parent

# Corpus slugs, resolved 2026-09-01 against Gamma search before grading. Ids
# match test-a-corpus.json; entry 16 is excluded there and absent here.
POLYMARKET_SLUGS = {
    1: "will-zelenskyy-wear-a-suit-before-july",
    2: "ukraine-agrees-to-give-trump-rare-earth-metals-before-april",
    3: "microstrategy-sells-any-bitcoin-by-may-31-2026",
    4: "us-x-iran-permanent-peace-deal-by-may-15-2026-144-885-839",
    5: "will-the-us-invade-venezuela-in-2025",
    6: "will-edmundo-gonzalez-win-the-2024-venezuela-presidential-election",
    7: "tiktok-banned-in-the-us-before-may-2025",
    8: "was-barron-involved-in-djt",
    9: "ethereum-etf-approved-by-may-31",
    10: "will-the-missing-submarine-be-found-by-june-23",
    11: "will-trump-create-a-national-bitcoin-reserve-in-his-first-100-days",
    12: "will-volodymyr-zelenskyy-be-the-2022-time-person-of-the-year",
    13: "trump-declassifies-ufo-files-in-2025",
    14: "will-the-government-shutdown-end-november-12-365",
    15: "will-cardi-b-perform-during-the-super-bowl-lx-halftime-show",
}

# Bounded guesses per Kalshi dispute plus the latest close date a market can
# have and still BE the disputed market. The first run omitted this guard and a
# series probe returned KXNFLWINS-27IND-9 — a live 2027-season market from the
# right series but the wrong season — into the disputed arm. Correcting it
# removes a trouble-laden F from the disputed side (i.e., the correction works
# AGAINST the theory, not for it). Every probe and its outcome is recorded.
KALSHI_TICKER_GUESSES = {
    17: (["KXKHAMENEI", "KXSUPREMELEADER", "KXKHAMENEIOUT"], "2026-04-01"),
    18: (["KXHALFTIMESHOW", "KXHALFTIMEPERFORMER", "KXBIGGAMEPERFORM"], "2026-03-01"),
    19: (["KXNFLWINS", "KXNFLSEASONWINS", "KXNFLWINTOTAL"], "2026-02-01"),
    20: (["KXOSCARVIEWERS", "KXOSCARSVIEWERS", "KXOSCARSRATINGS"], "2025-04-01"),
}

WINDOW_DAYS = 45
WIDE_WINDOW_DAYS = 120
MIN_CANDIDATES = 6


async def gamma_market(client: httpx.AsyncClient, slug: str) -> dict | None:
    response = await client.get(f"{GAMMA}/markets", params={"slug": slug, "closed": "true"})
    items = response.json() if response.status_code == 200 else []
    return items[0] if items else None


async def gamma_event_primary_tag(client: httpx.AsyncClient, event_slug: str) -> dict | None:
    response = await client.get(f"{GAMMA}/events", params={"slug": event_slug})
    items = response.json() if response.status_code == 200 else []
    tags = (items[0].get("tags") or []) if items else []
    return tags[0] if tags else None


async def gamma_candidates(
    client: httpx.AsyncClient, tag_id: str, anchor: datetime, days: int
) -> list[dict]:
    response = await client.get(
        f"{GAMMA}/markets",
        params={
            "closed": "true",
            "tag_id": tag_id,
            "end_date_min": (anchor - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z"),
            "end_date_max": (anchor + timedelta(days=days)).strftime("%Y-%m-%dT23:59:59Z"),
            "limit": "100",
        },
    )
    return response.json() if response.status_code == 200 else []


async def main() -> None:
    normalize = PolymarketGlobalHistoricalVenue._normalize_market
    corpus = json.loads((HERE / "test-a-corpus.json").read_text())
    documented = len(corpus["disputes"])

    disputed_markets = []
    control_markets = []
    resolution_log = []
    disputed_ids: set[str] = set()

    async with httpx.AsyncClient(timeout=40) as client:
        raw_by_id: dict[int, dict] = {}
        for dispute_id, slug in POLYMARKET_SLUGS.items():
            raw = await gamma_market(client, slug)
            if raw is None:
                resolution_log.append(
                    {"id": dispute_id, "venue": "polymarket", "slug": slug, "status": "UNFETCHABLE"}
                )
                continue
            raw_by_id[dispute_id] = raw
            market = normalize(raw)
            disputed_markets.append(market)
            disputed_ids.add(market.market_id)
            resolution_log.append(
                {
                    "id": dispute_id,
                    "venue": "polymarket",
                    "slug": slug,
                    "status": "OK",
                    "market_id": market.market_id,
                    "rules_chars": len(market.raw_rules_text or ""),
                }
            )

        for dispute_id, (guesses, max_close) in KALSHI_TICKER_GUESSES.items():
            found = None
            for guess in guesses:
                response = await client.get(
                    f"{KALSHI}/markets", params={"series_ticker": guess, "limit": 5}
                )
                markets = response.json().get("markets", []) if response.status_code == 200 else []
                # A live same-series market from a LATER season is not the
                # disputed market; only accept one that closed inside the
                # dispute's era.
                markets = [
                    m for m in markets
                    if str(m.get("close_time") or "9999") <= max_close
                ]
                if markets:
                    found = (guess, markets)
                    break
            resolution_log.append(
                {
                    "id": dispute_id,
                    "venue": "kalshi",
                    "probes": guesses,
                    "max_close": max_close,
                    "status": "OK" if found else "UNFETCHABLE_LIKELY_PRUNED",
                }
            )
            if found:
                # Grading a whole ladder for one dispute would overweight it;
                # take the single market with the largest rules text.
                richest = max(
                    found[1], key=lambda m: len((m.get("rules_primary") or "") + (m.get("rules_secondary") or ""))
                )
                from atlas.venues.kalshi import KalshiVenue

                market = KalshiVenue._normalize_market(richest)
                disputed_markets.append(market)
                disputed_ids.add(market.market_id)

        for dispute_id, raw in raw_by_id.items():
            events = raw.get("events") or []
            event_slug = events[0].get("slug") if events else None
            tag = await gamma_event_primary_tag(client, event_slug) if event_slug else None
            if tag is None:
                resolution_log.append(
                    {"id": dispute_id, "status": "NO_TAG_NO_CONTROLS", "event_slug": event_slug}
                )
                continue
            market = normalize(raw)
            anchor = market.close_time or datetime.now(UTC)
            candidates_raw = await gamma_candidates(client, str(tag["id"]), anchor, WINDOW_DAYS)
            widened = False
            if len(candidates_raw) < MIN_CANDIDATES:
                candidates_raw = await gamma_candidates(
                    client, str(tag["id"]), anchor, WIDE_WINDOW_DAYS
                )
                widened = True
            candidates = [normalize(item) for item in candidates_raw]
            picked = select_controls(market, candidates, excluded_ids=disputed_ids)
            control_markets.extend(picked)
            resolution_log.append(
                {
                    "id": dispute_id,
                    "status": "CONTROLS_SELECTED",
                    "tag": {"id": tag.get("id"), "label": tag.get("label")},
                    "window_days": WIDE_WINDOW_DAYS if widened else WINDOW_DAYS,
                    "candidates": len(candidates),
                    "picked": [m.market_id for m in picked],
                }
            )

    graded_at = datetime.now(UTC)
    report = evaluate_test_a(
        disputed_markets, control_markets, graded_at, corpus_size_documented=documented
    )
    report["resolution_log"] = resolution_log
    report["controls_per_dispute_target"] = CONTROLS_PER_DISPUTE
    out = HERE / "test-a-result.json"
    out.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"result written: {out}")
    print(
        f"outcome={report['outcome']} documented={report['corpus_size_documented']} "
        f"gradeable={report['corpus_size_gradeable']} controls={report['controls']['markets']}"
    )
    print(
        f"median disputed={report['disputed']['median_score']} "
        f"control={report['controls']['median_score']} gap={report['median_score_gap']}"
    )
    print(
        f"trouble rates: disputed={report['disputed']['trouble_rate']} "
        f"control={report['controls']['trouble_rate']} ratio={report['trouble_rate_ratio']}"
    )
    print(f"criteria: {report['criteria']}")


if __name__ == "__main__":
    asyncio.run(main())
