from decimal import Decimal


def polymarket_us_taker_fee(
    contracts: Decimal, price: Decimal, theta: Decimal = Decimal(0)
) -> Decimal:
    return theta * contracts * price * (Decimal(1) - price)


def zero_fixture_fee(*_args, **_kwargs) -> Decimal:
    return Decimal(0)
