"""Cross-venue crypto overlap: same-subject anchoring, permanent non-equivalence.

Fixture texts frozen from the 2026-08-13 live census: Kalshi KXBTCD hourly
markets (CF Benchmarks BRTI 60-second average) and Polymarket daily
"bitcoin-above" markets (Binance BTC/USDT 1-minute candle close, noon ET,
date in the title). The venues' published sources and measurement windows are
permanently different, so crypto pairs must NEVER approve — they exist to
supply same-subject evidence-backed REJECTED labels, bounded per run.
"""

from decimal import Decimal

from test_cpi_yoy import _market

from atlas.backfill import (
    CRYPTO_REVIEW_REJECTIONS_PER_RUN,
    _historical_label,
)
from atlas.fingerprints import build_fingerprint
from atlas.models import MatchStatus
from atlas.verification import verify_equivalence

KALSHI_NOON_TITLE = "Will the price of Bitcoin be above $63,999.99 at 12 PM EDT on Aug 14, 2026?"
KALSHI_NOON_RULES = (
    "If the simple average of the sixty seconds of CF Benchmarks' Bitcoin Real-Time "
    "Index (BRTI) before 12 PM EDT is above 63999.99 at 12 PM EDT on Aug 14, 2026, "
    "then the market resolves to Yes.\n\n"
    "Not all cryptocurrency price data is the same. While checking a source like "
    "Google or Coinbase may help guide your decision, the price used to determine "
    "this market is based on CF Benchmarks' corresponding Real Time Index (RTI). At "
    "the last minute before expiration, 60 RTI prices are collected. The official "
    "and final value is the average of these prices."
)

POLYMARKET_NOON_TITLE = "Will Bitcoin be above $64,000 on August 14?"
POLYMARKET_NOON_RULES = (
    'This market will resolve to "Yes" if the Binance 1 minute candle for BTC/USDT '
    "12:00 in the ET timezone (noon) on the date specified in the title has a final "
    '"Close" price higher than the price specified in the title. Otherwise, this '
    'market will resolve to "No".\n\n'
    "The resolution source for this market is Binance, specifically the BTC/USDT "
    '"Close" prices currently available at https://www.binance.com/en/trade/BTC_USDT '
    'with "1m" and "Candles" selected on the top bar.\n\n'
    "Price precision is determined by the number of decimal places in the source."
)


def _polymarket_noon():
    market = _market("polymarket_us", POLYMARKET_NOON_TITLE, POLYMARKET_NOON_RULES)
    market.venue_market_id = "bitcoin-above-on-august-14-2026-64000"
    return market


def test_kalshi_noon_leg_canonicalizes():
    fingerprint = build_fingerprint(_market("kalshi", KALSHI_NOON_TITLE, KALSHI_NOON_RULES))
    assert fingerprint.event_subject == "crypto_price|btc|2026-08-14T16:00:00Z"
    assert fingerprint.resolution_source == "cf_benchmarks_brti"
    assert fingerprint.contract_scope == "price_at|60s_simple_average"
    assert (fingerprint.threshold, fingerprint.threshold_operator) == (
        Decimal("63999.99"),
        ">",
    )


def test_polymarket_candle_leg_canonicalizes_to_same_subject():
    fingerprint = build_fingerprint(_polymarket_noon())
    assert fingerprint.event_subject == "crypto_price|btc|2026-08-14T16:00:00Z"
    assert fingerprint.resolution_source == "binance_btcusdt_1m_close"
    assert fingerprint.contract_scope == "price_at|1m_candle_close"
    assert (fingerprint.threshold, fingerprint.threshold_operator) == (Decimal(64000), ">")


def test_crypto_pair_is_same_subject_but_never_equivalent():
    pair = verify_equivalence(
        _market("kalshi", KALSHI_NOON_TITLE, KALSHI_NOON_RULES),
        _polymarket_noon(),
    )
    assert pair.status is MatchStatus.REVIEW_REQUIRED
    assert "RESOLUTION_SOURCE_MISMATCH" in pair.differences
    assert "CONTRACT_SCOPE_MISMATCH" in pair.differences
    assert pair.decision.fingerprint_a.event_subject == pair.decision.fingerprint_b.event_subject


def test_crypto_pair_mints_rejected_on_divergent_settlement():
    pair = verify_equivalence(
        _market("kalshi", KALSHI_NOON_TITLE, KALSHI_NOON_RULES),
        _polymarket_noon(),
    )
    assert _historical_label(pair, "no", "yes") == ("REJECTED", "DIVERGED")
    assert _historical_label(pair, "no", "no") == (None, "INCONCLUSIVE")


def test_crypto_family_run_cap_is_bounded():
    assert CRYPTO_REVIEW_REJECTIONS_PER_RUN <= 10
