"""Cross-venue price-gap radar for twin-shaped candidate pairs.

PAPER-ONLY RESEARCH INSTRUMENT. This module observes and records; it never
places orders and has no path to any execution capability. The pairs it
watches are CANDIDATES whose deterministic verification is still
``REVIEW_REQUIRED`` — they are never trusted, never approved, and never enter
the approved-pair registry or the paper-execution path. Every observation
records that caveat alongside the verification mismatch codes.

The point of the radar is a measurement the project currently lacks: how often
do the two venues actually disagree on the same twin-shaped claim, by how
much, and would the disagreement survive fees at executable top-of-book
prices? The cumulative "$2k paper bankroll" derived from these observations is
an honest research meter for the original aspiration, not a trading signal.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from atlas.fingerprints import build_fingerprint
from atlas.models import Market
from atlas.verification import verify_equivalence

# K-YES pays exactly when PM-YES pays (same predicate).
EQUIVALENT_SHAPE = "equivalent_shape"
# K-YES pays exactly when PM-NO pays (operator complements at the same value).
INVERSE_SHAPE = "inverse_shape"

_COMPLEMENT_OPERATORS = {(">", "<="), ("<=", ">"), (">=", "<"), ("<", ">=")}

BANKROLL_START = Decimal(2000)
# Stake a fixed fraction of the current bankroll per opportunity so the meter
# measures compounding, not arithmetic accumulation. 5% of the $2,000 start is
# exactly the old flat $100 cap, so the meter's recorded history reads the
# same at the starting bankroll. The Kalshi displayed size still caps every
# stake: a growing bankroll cannot pretend thin books absorb more contracts.
STAKE_FRACTION = Decimal("0.05")
# Flat per-basket fee/slippage buffer; recorded on every observation so the
# assumption stays visible instead of being baked in silently.
GAP_FEE_BUFFER = Decimal("0.02")

PAIR_KIND = "CANDIDATE_TWIN_SHAPE_NOT_PROVEN"


def match_twin_shapes(
    kalshi_markets: list[Market], polymarket_markets: list[Market]
) -> list[dict]:
    """Deterministically pair markets whose canonical terms form a twin shape.

    Requires an identical canonical ``event_subject`` (family|anchor form from
    the specialized normalizers), equal threshold units, and either identical
    threshold+operator (equivalent shape) or an exact operator complement at
    the same threshold (inverse shape). Everything else is ignored here and
    left to ``verify_equivalence``, whose status is recorded per observation.
    """
    pairs: list[dict] = []
    kalshi_fps = [(market, build_fingerprint(market)) for market in kalshi_markets]
    polymarket_fps = [(market, build_fingerprint(market)) for market in polymarket_markets]
    for k_market, k_fp in kalshi_fps:
        subject = k_fp.event_subject or ""
        if "|" not in subject or k_fp.threshold is None or not k_fp.threshold_operator:
            continue
        for p_market, p_fp in polymarket_fps:
            if (p_fp.event_subject or "") != subject:
                continue
            if p_fp.threshold is None or not p_fp.threshold_operator:
                continue
            if k_fp.threshold != p_fp.threshold or k_fp.threshold_unit != p_fp.threshold_unit:
                continue
            # Direction and scope must match exactly: without this, a Kalshi
            # hike-25 bucket pairs with a Polymarket CUT-25 bucket (same
            # subject, threshold, and operator — opposite economics) and the
            # radar reports a phantom 30-cent "gap". Caught on the first live
            # scan; the basket legs are only locked within one direction.
            if (
                k_fp.affirmative_outcome != p_fp.affirmative_outcome
                or k_fp.contract_scope != p_fp.contract_scope
                or k_fp.event_action != p_fp.event_action
            ):
                continue
            if k_fp.threshold_operator == p_fp.threshold_operator:
                shape = EQUIVALENT_SHAPE
            elif (k_fp.threshold_operator, p_fp.threshold_operator) in _COMPLEMENT_OPERATORS:
                shape = INVERSE_SHAPE
            else:
                continue
            pairs.append(
                {
                    "shape": shape,
                    "event_subject": subject,
                    "kalshi_market": k_market,
                    "polymarket_market": p_market,
                }
            )
    return pairs


def _decimal(raw: object) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except ArithmeticError:
        return None


def kalshi_quotes(market: Market) -> dict[str, Decimal | None] | None:
    """Top-of-book asks from the venue payload; a zero-size ask is no quote."""
    raw = market.raw_market_json
    yes_ask = _decimal(raw.get("yes_ask_dollars"))
    no_ask = _decimal(raw.get("no_ask_dollars"))
    yes_size = _decimal(raw.get("yes_ask_size_fp"))
    no_size = _decimal(raw.get("no_ask_size_fp"))
    if yes_ask is not None and (yes_ask <= 0 or (yes_size is not None and yes_size <= 0)):
        yes_ask = None
    if no_ask is not None and (no_ask <= 0 or (no_size is not None and no_size <= 0)):
        no_ask = None
    if yes_ask is None and no_ask is None:
        return None
    return {"yes_ask": yes_ask, "no_ask": no_ask, "yes_size": yes_size, "no_size": no_size}


def polymarket_quotes(market: Market) -> dict[str, Decimal | None] | None:
    """Top-of-book from Gamma best bid/ask; NO ask is 1 minus the YES bid.

    Gamma exposes no displayed size, so fills are ASSUMED at the quote — that
    assumption is recorded on every observation and in the bankroll summary.
    """
    raw = market.raw_market_json
    yes_ask = _decimal(raw.get("bestAsk"))
    yes_bid = _decimal(raw.get("bestBid"))
    no_ask = (Decimal(1) - yes_bid) if yes_bid is not None and yes_bid > 0 else None
    if yes_ask is not None and yes_ask <= 0:
        yes_ask = None
    if yes_ask is None and no_ask is None:
        return None
    return {"yes_ask": yes_ask, "no_ask": no_ask, "yes_size": None, "no_size": None}


def _baskets(shape: str, kalshi: dict, polymarket: dict) -> list[dict]:
    """Locked baskets: exactly one leg pays $1 at settlement IF the twin
    relationship holds (which is unproven — hence candidate-only)."""
    if shape == INVERSE_SHAPE:
        combos = (
            ("kalshi_yes+polymarket_yes", kalshi["yes_ask"], polymarket["yes_ask"], "yes_size"),
            ("kalshi_no+polymarket_no", kalshi["no_ask"], polymarket["no_ask"], "no_size"),
        )
    else:
        combos = (
            ("kalshi_yes+polymarket_no", kalshi["yes_ask"], polymarket["no_ask"], "yes_size"),
            ("kalshi_no+polymarket_yes", kalshi["no_ask"], polymarket["yes_ask"], "no_size"),
        )
    baskets = []
    for legs, k_price, p_price, size_key in combos:
        if k_price is None or p_price is None:
            continue
        cost = k_price + p_price
        gap = Decimal(1) - cost - GAP_FEE_BUFFER
        baskets.append(
            {
                "legs": legs,
                "cost": str(cost),
                "fee_buffer": str(GAP_FEE_BUFFER),
                "gap": str(gap),
                "kalshi_size": str(kalshi[size_key]) if kalshi[size_key] is not None else None,
            }
        )
    return baskets


def observe_pair(pair: dict, observed_at: str | None = None) -> dict | None:
    """One radar observation for one twin-shaped pair; None when unquotable."""
    kalshi_market: Market = pair["kalshi_market"]
    polymarket_market: Market = pair["polymarket_market"]
    kalshi = kalshi_quotes(kalshi_market)
    polymarket = polymarket_quotes(polymarket_market)
    if kalshi is None or polymarket is None:
        return None
    baskets = _baskets(pair["shape"], kalshi, polymarket)
    if not baskets:
        return None
    verification = verify_equivalence(kalshi_market, polymarket_market, "gap-radar")
    best = max(baskets, key=lambda basket: Decimal(basket["gap"]))
    best_gap = Decimal(best["gap"])
    return {
        "observation_id": str(uuid.uuid4()),
        "observed_at": observed_at or datetime.now(UTC).isoformat(),
        "paper_only": True,
        "trusted": False,
        "pair_kind": PAIR_KIND,
        "shape": pair["shape"],
        "event_subject": pair["event_subject"],
        "kalshi_market_id": kalshi_market.market_id,
        "kalshi_title": kalshi_market.title,
        "polymarket_market_id": polymarket_market.market_id,
        "polymarket_title": polymarket_market.title,
        "verification_status": verification.status.value,
        "mismatch_codes": list(verification.differences),
        "baskets": baskets,
        "best_gap": str(best_gap),
        "best_basket": best["legs"],
        "executable_gap": best_gap > 0,
        "polymarket_fill_assumed_at_quote": True,
    }


def paper_bankroll_summary(observations: list[dict]) -> dict:
    """The honest $2k meter: what the starting bankroll would be if every
    distinct executable gap had been paper-taken under recorded assumptions.

    One opportunity per pair per UTC day (a persistent gap re-observed across
    scans is one opportunity, not many). Stake per opportunity is
    ``STAKE_FRACTION`` of the current bankroll, further capped — when the
    Kalshi displayed size is known — by the cost of the displayed contracts.
    """
    bankroll = BANKROLL_START
    taken: set[tuple[str, str, str]] = set()
    opportunities = 0
    for observation in sorted(observations, key=lambda o: str(o.get("observed_at") or "")):
        if not observation.get("executable_gap"):
            continue
        best_gap = _decimal(observation.get("best_gap"))
        if best_gap is None or best_gap <= 0:
            continue
        day = str(observation.get("observed_at") or "")[:10]
        key = (
            str(observation.get("kalshi_market_id")),
            str(observation.get("polymarket_market_id")),
            day,
        )
        if key in taken:
            continue
        taken.add(key)
        best = next(
            (b for b in observation.get("baskets", []) if b["legs"] == observation.get("best_basket")),
            None,
        )
        if best is None:
            continue
        cost = _decimal(best.get("cost"))
        if cost is None or cost <= 0:
            continue
        stake = min(bankroll * STAKE_FRACTION, bankroll)
        size = _decimal(best.get("kalshi_size"))
        if size is not None:
            stake = min(stake, size * cost)
        if stake <= 0:
            continue
        bankroll += (stake / cost) * best_gap
        opportunities += 1
    return {
        "paper_only": True,
        "start_bankroll": str(BANKROLL_START),
        "paper_bankroll": str(bankroll.quantize(Decimal("0.01"))),
        "distinct_executable_opportunities": opportunities,
        "observations_reviewed": len(observations),
        "assumptions": {
            "stake_fraction_of_bankroll": str(STAKE_FRACTION),
            "stake_capped_by_kalshi_displayed_size": True,
            "fee_buffer_per_basket": str(GAP_FEE_BUFFER),
            "polymarket_fill_assumed_at_quote": True,
            "dedup": "one opportunity per pair per UTC day",
            "pairs_are_candidates_not_proven_twins": True,
        },
    }
