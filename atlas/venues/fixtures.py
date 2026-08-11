from datetime import UTC, datetime
from decimal import Decimal

from atlas.models import Market, OrderBook, OrderBookLevel, VenueName


def fixture_markets() -> dict[VenueName, list[Market]]:
    common = {
        "title": "Will the Federal Reserve raise the federal funds target by 25 basis points by September 30, 2026?",
        "resolution_source": "Federal Reserve",
        "resolution_text": "Official Federal Reserve announcement by the deadline.",
        "event_subject": "Federal Reserve",
        "event_action": "raises federal funds target",
        "threshold": Decimal(25),
        "threshold_operator": ">=",
        "threshold_unit": "basis_points",
        "measurement_period": "September 2026",
        "geography": "US",
        "timezone": "America/New_York",
        "revision_policy": "initial_release",
        "category": "economics",
        "raw_rules_text": "Resolve YES if the Federal Reserve raises the federal funds target by at least 25 basis points by 2026-09-30. Otherwise resolve NO.",
    }
    return {
        VenueName.KALSHI: [
            Market(
                market_id="kalshi:KALSHI-FED-SEP26",
                venue=VenueName.KALSHI,
                venue_market_id="KALSHI-FED-SEP26",
                **common,
            )
        ],
        VenueName.POLYMARKET_US: [
            Market(
                market_id="polymarket_us:PM-FED-SEP26",
                venue=VenueName.POLYMARKET_US,
                venue_market_id="PM-FED-SEP26",
                **common,
            )
        ],
    }


def fixture_books() -> dict[str, OrderBook]:
    now = datetime.now(UTC)
    return {
        "kalshi:KALSHI-FED-SEP26": OrderBook(
            venue=VenueName.KALSHI,
            market_id="kalshi:KALSHI-FED-SEP26",
            timestamp=now,
            yes_asks=[
                OrderBookLevel(price=Decimal("0.41"), quantity=Decimal(100)),
                OrderBookLevel(price=Decimal("0.42"), quantity=Decimal(80)),
            ],
            no_bids=[
                OrderBookLevel(price=Decimal("0.59"), quantity=Decimal(100)),
                OrderBookLevel(price=Decimal("0.58"), quantity=Decimal(80)),
            ],
            yes_bids=[OrderBookLevel(price=Decimal("0.40"), quantity=Decimal(100))],
            no_asks=[OrderBookLevel(price=Decimal("0.60"), quantity=Decimal(100))],
            sequence=1,
        ),
        "polymarket_us:PM-FED-SEP26": OrderBook(
            venue=VenueName.POLYMARKET_US,
            market_id="polymarket_us:PM-FED-SEP26",
            timestamp=now,
            no_asks=[
                OrderBookLevel(price=Decimal("0.54"), quantity=Decimal(100)),
                OrderBookLevel(price=Decimal("0.55"), quantity=Decimal(80)),
            ],
            yes_bids=[OrderBookLevel(price=Decimal("0.46"), quantity=Decimal(100))],
            yes_asks=[OrderBookLevel(price=Decimal("0.47"), quantity=Decimal(100))],
            no_bids=[OrderBookLevel(price=Decimal("0.53"), quantity=Decimal(100))],
            sequence=1,
        ),
    }
