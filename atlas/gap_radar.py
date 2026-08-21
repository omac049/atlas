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
from decimal import ROUND_CEILING, Decimal

from atlas.fingerprints import build_fingerprint
from atlas.models import Market, VenueName
from atlas.settlement_timing import settlement_timing_annotation
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
# Venue-published taker fee models, encoded from primary sources 2026-08-19.
# The prior flat 2c/basket buffer understated fees exactly where gaps look
# best: near 50c both venues charge their quadratic peak (~3c/basket combined).
#
# - Kalshi: taker = ceil(M x 0.07 x C x P x (1-P)); its /series endpoint
#   publishes fee_type=quadratic, fee_multiplier=1 for every default macro
#   series (verified live). The venue ceils per ORDER; this model ceils per
#   CONTRACT, which can only overstate the fee — a profit meter must never
#   round in its own favor.
# - Polymarket: each Gamma market payload publishes its own feeSchedule
#   ({rate, exponent, takerOnly}; economics rate 0.05, verified live on the
#   tracked macro markets; makers pay nothing). feesEnabled=false markets are
#   free; a fee-enabled market MISSING its schedule gets the maximum published
#   category rate so an absent field can never flatter a gap.
KALSHI_TAKER_RATE = Decimal("0.07")
POLYMARKET_MAX_TAKER_RATE = Decimal("0.07")
_CENT = Decimal("0.01")

# Both venues quote in whole cents (Kalshi ticks at 1c; the Polymarket US
# gateway publishes orderPriceMinTickSize 0.01). A "gap" smaller than one tick
# is inside the quantization noise of the prices that produced it — and on the
# Polymarket side it is often an artifact of deriving the NO ask as
# `1 - bestBid` rather than a price anyone quoted. Measured 2026-08-20: 28
# executable observations sat under half a cent, and the entire FOMC 2026-10
# family sat at 0.1c, an order of magnitude below the tick.
#
# This does NOT change `executable_gap` — that stays "gross edge > 0" so the
# recorded series remains comparable across the whole study. It adds a stricter
# companion flag that the meter and the study count instead.
MIN_EXECUTABLE_GAP = Decimal("0.01")
# A basket is only an opportunity if you could take a meaningful amount of it.
# The binding leg is the thinner one; live GDP pairs on 2026-08-20 showed a
# 7.8c gap against 0.06 contracts of Polymarket depth.
MIN_EXECUTABLE_BASKET_CONTRACTS = Decimal(1)


def kalshi_taker_fee_per_contract(price: Decimal) -> Decimal:
    raw = KALSHI_TAKER_RATE * price * (Decimal(1) - price)
    return raw.quantize(_CENT, rounding=ROUND_CEILING)


def polymarket_taker_fee_per_share(
    price: Decimal, raw_market: dict
) -> tuple[Decimal, str]:
    """Published-schedule taker fee for one share, with the basis recorded.

    Two venues publish the same quadratic in different shapes, so the basis is
    always recorded alongside the number:

    - Polymarket **Global** (Gamma) publishes a ``feeSchedule``
      ``{rate, exponent, takerOnly}``.
    - Polymarket **US** (gateway) publishes a scalar ``feeCoefficient``
      (0.06 on every tracked macro market, verified live 2026-08-20) and no
      schedule object at all. Without this branch every US quote would fall to
      the max-rate fallback below, overstating the fee by ~17% and hiding gaps
      the venue would actually let you take.

    A fee-enabled market that publishes neither still gets the maximum
    published rate, so an absent field can never flatter a gap.
    """
    if raw_market.get("feesEnabled") is False:
        return Decimal(0), "venue_fees_disabled"
    base = price * (Decimal(1) - price)
    schedule = raw_market.get("feeSchedule") or {}
    rate = _decimal(schedule.get("rate"))
    if rate is not None:
        try:
            exponent = int(schedule.get("exponent") or 1)
        except (TypeError, ValueError):
            exponent = 1
        return rate * base**exponent, "venue_published_schedule"
    coefficient = _decimal(raw_market.get("feeCoefficient"))
    if coefficient is not None and coefficient >= 0:
        return coefficient * base, "venue_published_coefficient"
    return POLYMARKET_MAX_TAKER_RATE * base, "schedule_missing_max_rate_applied"

# Which Polymarket venue a leg came from decides whether the basket is even
# takeable, and it is recorded on every observation so no downstream metric has
# to infer it. `polymarket_global` is the offshore Gamma catalog: it publishes
# no book and `atlas/venues/polymarket_global.py` states it "can never reach
# shadow, approval, or paper-trading paths that require executable prices".
# Measured 2026-08-20: 100% of the radar's first 18,650 observations priced a
# Global leg, so every "executable" gap in that corpus was untakeable by
# construction. Observations stay in the corpus as research; the flag is what
# keeps them out of any tradeability claim.
TRADEABLE_POLYMARKET_VENUES = frozenset({VenueName.POLYMARKET_US})


def polymarket_leg_is_tradeable(market: Market) -> bool:
    return market.venue in TRADEABLE_POLYMARKET_VENUES


PAIR_KIND = "CANDIDATE_TWIN_SHAPE_NOT_PROVEN"


def _twin_shape(k_fp, p_fp) -> str | None:
    """The twin shape two same-subject fingerprints form, or None.

    A threshold contract is NEVER compared to a categorical one: "will CPI come
    in above 3.2%" and "will the Republicans win the House" can share neither a
    basket nor a meaning, and pairing them would manufacture a gap out of two
    unrelated claims.
    """
    k_threshold = k_fp.threshold is not None and bool(k_fp.threshold_operator)
    p_threshold = p_fp.threshold is not None and bool(p_fp.threshold_operator)
    if k_threshold != p_threshold:
        return None
    if k_threshold:
        if k_fp.threshold != p_fp.threshold or k_fp.threshold_unit != p_fp.threshold_unit:
            return None
        if k_fp.threshold_operator == p_fp.threshold_operator:
            return EQUIVALENT_SHAPE
        if (k_fp.threshold_operator, p_fp.threshold_operator) in _COMPLEMENT_OPERATORS:
            return INVERSE_SHAPE
        return None
    # Categorical twins. The caller has already checked that both legs name the
    # SAME affirmative outcome; requiring it to be non-null is what keeps a
    # joint contract out. Polymarket lists "2026 Balance of Power: D Senate, D
    # House" markets that normalize to the house-control subject with NO
    # affirmative outcome, so a null-tolerant match would pair a joint
    # Senate+House bet with a House-only bet — different claims, phantom gap.
    if not k_fp.affirmative_outcome:
        return None
    # Equivalent shape only. "Democrats win" and "Republicans win" are NOT a
    # published complement: ties and third outcomes exist, which is exactly why
    # both venues publish tiebreak clauses. Calling them inverse would be
    # inference, and this module never infers.
    return EQUIVALENT_SHAPE


def match_twin_shapes(
    kalshi_markets: list[Market], polymarket_markets: list[Market]
) -> list[dict]:
    """Deterministically pair markets whose canonical terms form a twin shape.

    Requires an identical canonical ``event_subject`` (family|anchor form from
    the specialized normalizers) plus identical direction, scope, and action.
    Two twin kinds then qualify, and a contract of one kind is never compared
    to a contract of the other:

    - **Threshold twins** — equal threshold units and either identical
      threshold+operator (equivalent shape) or an exact operator complement at
      the same threshold (inverse shape).
    - **Categorical twins** — neither leg publishes a threshold at all, and
      both name the same affirmative outcome (e.g. chamber control: "will the
      Republican Party win the House in 2026"). Equivalent shape only.

    Everything else is ignored here and left to ``verify_equivalence``, whose
    status is recorded per observation.
    """
    pairs: list[dict] = []
    kalshi_fps = [(market, build_fingerprint(market)) for market in kalshi_markets]
    polymarket_fps = [(market, build_fingerprint(market)) for market in polymarket_markets]
    for k_market, k_fp in kalshi_fps:
        subject = k_fp.event_subject or ""
        if "|" not in subject:
            continue
        for p_market, p_fp in polymarket_fps:
            if (p_fp.event_subject or "") != subject:
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
            shape = _twin_shape(k_fp, p_fp)
            if shape is None:
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


def _quote_value(raw_value: object) -> Decimal | None:
    """A venue quote that may be a scalar or a ``{value, currency}`` object.

    Gamma (Polymarket Global) publishes ``bestAsk: "0.71"``; the Polymarket US
    gateway publishes ``bestAskQuote: {"value": "0.7100", "currency": "USD"}``.
    """
    if isinstance(raw_value, dict):
        return _decimal(raw_value.get("value"))
    return _decimal(raw_value)


def polymarket_quotes(
    market: Market, sizes: dict[str, Decimal | None] | None = None
) -> dict[str, Decimal | None] | None:
    """Top-of-book from the venue's best bid/ask; NO ask is 1 minus the YES bid.

    ``sizes`` carries displayed depth when the venue publishes a book. Gamma
    (Global) publishes none, so those fills stay ASSUMED at the quote and the
    observation records that. The Polymarket US gateway *does* publish a
    two-sided book (``/v1/markets/{slug}/book``, keyed ``bids``/``offers``), so
    US observations can be sized on both legs instead of one.

    Depth is mapped to the side you would actually take: buying YES lifts the
    top **offer**, and buying NO is selling YES into the top **bid**.
    """
    raw = market.raw_market_json
    yes_ask = _quote_value(raw.get("bestAsk") if "bestAsk" in raw else raw.get("bestAskQuote"))
    yes_bid = _quote_value(raw.get("bestBid") if "bestBid" in raw else raw.get("bestBidQuote"))
    no_ask = (Decimal(1) - yes_bid) if yes_bid is not None and yes_bid > 0 else None
    if yes_ask is not None and yes_ask <= 0:
        yes_ask = None
    if yes_ask is None and no_ask is None:
        return None
    depth = sizes or {}
    yes_size = depth.get("yes_size")
    no_size = depth.get("no_size")
    # A quote with a published size of zero is not a quote.
    if yes_ask is not None and yes_size is not None and yes_size <= 0:
        yes_ask = None
    if no_ask is not None and no_size is not None and no_size <= 0:
        no_ask = None
    if yes_ask is None and no_ask is None:
        return None
    return {"yes_ask": yes_ask, "no_ask": no_ask, "yes_size": yes_size, "no_size": no_size}


def _baskets(
    shape: str, kalshi: dict, polymarket: dict, polymarket_raw: dict
) -> list[dict]:
    """Locked baskets: exactly one leg pays $1 at settlement IF the twin
    relationship holds (which is unproven — hence candidate-only)."""
    # Each combo names the size key for BOTH legs. They differ whenever the
    # basket mixes sides (Kalshi YES against Polymarket NO), and using one key
    # for both would silently read the wrong leg's depth.
    if shape == INVERSE_SHAPE:
        combos = (
            ("kalshi_yes+polymarket_yes", kalshi["yes_ask"], polymarket["yes_ask"],
             "yes_size", "yes_size"),
            ("kalshi_no+polymarket_no", kalshi["no_ask"], polymarket["no_ask"],
             "no_size", "no_size"),
        )
    else:
        combos = (
            ("kalshi_yes+polymarket_no", kalshi["yes_ask"], polymarket["no_ask"],
             "yes_size", "no_size"),
            ("kalshi_no+polymarket_yes", kalshi["no_ask"], polymarket["yes_ask"],
             "no_size", "yes_size"),
        )
    baskets = []
    for legs, k_price, p_price, k_size_key, p_size_key in combos:
        if k_price is None or p_price is None:
            continue
        cost = k_price + p_price
        kalshi_fee = kalshi_taker_fee_per_contract(k_price)
        polymarket_fee, polymarket_fee_basis = polymarket_taker_fee_per_share(
            p_price, polymarket_raw
        )
        gap = Decimal(1) - cost - kalshi_fee - polymarket_fee
        kalshi_size = kalshi[k_size_key]
        polymarket_size = polymarket.get(p_size_key)
        # A paired basket can only be as large as its THINNER leg. When a venue
        # publishes no depth the binding size is unknown, not unlimited, so it
        # stays None rather than defaulting to the leg that did publish.
        known = [size for size in (kalshi_size, polymarket_size) if size is not None]
        basket_size = min(known) if len(known) == 2 else None
        baskets.append(
            {
                "legs": legs,
                "cost": str(cost),
                "kalshi_fee": str(kalshi_fee),
                "polymarket_fee": str(polymarket_fee),
                "polymarket_fee_basis": polymarket_fee_basis,
                "gap": str(gap),
                "kalshi_size": str(kalshi_size) if kalshi_size is not None else None,
                "polymarket_size": str(polymarket_size) if polymarket_size is not None else None,
                "basket_size": str(basket_size) if basket_size is not None else None,
            }
        )
    return baskets


def observe_pair(
    pair: dict,
    observed_at: str | None = None,
    polymarket_sizes: dict[str, Decimal | None] | None = None,
) -> dict | None:
    """One radar observation for one twin-shaped pair; None when unquotable.

    The ``settlement_timing`` field is a DESCRIPTIVE annotation only (see
    ``atlas.settlement_timing``): it records whether one venue may settle
    earlier than the other and how far out the basket's capital stays locked.
    It never affects ``verification_status``, ``mismatch_codes``, the baskets,
    the fees, or the gap — it exists so the study can ask whether an observed
    gap is carry compensation or mispricing.
    """
    kalshi_market: Market = pair["kalshi_market"]
    polymarket_market: Market = pair["polymarket_market"]
    kalshi = kalshi_quotes(kalshi_market)
    polymarket = polymarket_quotes(polymarket_market, polymarket_sizes)
    if kalshi is None or polymarket is None:
        return None
    baskets = _baskets(
        pair["shape"], kalshi, polymarket, polymarket_market.raw_market_json
    )
    if not baskets:
        return None
    verification = verify_equivalence(kalshi_market, polymarket_market, "gap-radar")
    best = max(baskets, key=lambda basket: Decimal(basket["gap"]))
    best_gap = Decimal(best["gap"])
    # Only claim a real fill when the venue actually published the depth we
    # would have taken; a missing size is unknown, never unlimited.
    sized_at_book = best.get("polymarket_size") is not None
    best_size = _decimal(best.get("basket_size")) or _decimal(best.get("kalshi_size"))
    observed_at_value = observed_at or datetime.now(UTC).isoformat()
    return {
        "observation_id": str(uuid.uuid4()),
        "observed_at": observed_at_value,
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
        # Stricter companion flags. `executable_gap` alone counted sub-tick
        # noise and dust-sized books as opportunities.
        "meets_tick_floor": best_gap >= MIN_EXECUTABLE_GAP,
        "meets_size_floor": (
            best_size is not None and best_size >= MIN_EXECUTABLE_BASKET_CONTRACTS
        ),
        "best_basket_size": str(best_size) if best_size is not None else None,
        "polymarket_venue": polymarket_market.venue.value,
        "tradeable_venue_pair": polymarket_leg_is_tradeable(polymarket_market),
        "polymarket_fill_assumed_at_quote": not sized_at_book,
        # Descriptive caution tag + lock-up horizon. Gates nothing.
        "settlement_timing": settlement_timing_annotation(
            kalshi_market, polymarket_market, observed_at=observed_at_value
        ),
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
    unsized_skipped = 0
    tradeable_opportunities = 0
    assumed_fill_opportunities = 0
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
        # The binding leg is the THINNER one. `basket_size` is present only when
        # both venues published depth; otherwise fall back to the Kalshi leg.
        size = _decimal(best.get("basket_size")) or _decimal(best.get("kalshi_size"))
        if size is None:
            # No published depth on either leg. Previously this ran UNCAPPED at
            # the full 5% stake, which is the one place the meter rounded in its
            # own favour — 8 of 26 opportunities and 35% of the recorded profit
            # came from assumed depth (measured 2026-08-20). An unknown size is
            # unknown, not unlimited, so the opportunity is skipped and counted.
            unsized_skipped += 1
            continue
        stake = min(stake, size * cost)
        if stake <= 0:
            continue
        bankroll += (stake / cost) * best_gap
        opportunities += 1
        if observation.get("tradeable_venue_pair"):
            tradeable_opportunities += 1
        if observation.get("polymarket_fill_assumed_at_quote", True):
            assumed_fill_opportunities += 1
    return {
        "paper_only": True,
        "start_bankroll": str(BANKROLL_START),
        "paper_bankroll": str(bankroll.quantize(Decimal("0.01"))),
        "distinct_executable_opportunities": opportunities,
        # Only these could have been taken with real money: the Polymarket leg
        # was on the US venue rather than the offshore Gamma catalog.
        "tradeable_executable_opportunities": tradeable_opportunities,
        # Counted rather than silently dropped, so a shrinking meter is always
        # attributable to missing depth rather than to a missing opportunity.
        "unsized_opportunities_skipped": unsized_skipped,
        "opportunities_with_assumed_polymarket_fill": assumed_fill_opportunities,
        "observations_reviewed": len(observations),
        "assumptions": {
            "stake_fraction_of_bankroll": str(STAKE_FRACTION),
            "stake_capped_by_thinner_leg_displayed_size": True,
            "unsized_opportunities_are_skipped_not_uncapped": True,
            "fee_model": {
                "kalshi": (
                    "ceil_per_contract(0.07 x P x (1-P)) — venue schedule, "
                    "fee_multiplier=1 verified per macro series 2026-08-19; "
                    "per-contract ceil is conservative vs the venue's per-order ceil"
                ),
                "polymarket": (
                    "per-market published feeSchedule rate x P x (1-P) "
                    "(takerOnly; economics rate 0.05 verified live); "
                    "fees-disabled markets free; missing schedule -> max rate 0.07"
                ),
            },
            "polymarket_fill_assumed_at_quote": (
                "per-observation; true on Polymarket Global (no published book), "
                "false on Polymarket US when the gateway book supplied depth"
            ),
            "dedup": "one opportunity per pair per UTC day",
            "pairs_are_candidates_not_proven_twins": True,
        },
    }
