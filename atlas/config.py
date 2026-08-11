from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ATLAS_", env_file=".env", extra="ignore")

    env: str = "development"
    trading_enabled: bool = False
    database_url: str = "postgresql://atlas:atlas@localhost:5432/atlas"
    redis_url: str = "redis://localhost:6379/0"
    minimum_net_roi: float = 0.03
    minimum_absolute_profit: float = 1.0
    maximum_horizon_days: int = 30
    maximum_book_age_seconds: int = 5
    http_timeout_seconds: float = 15.0
    kalshi_rest_url: str = "https://external-api.kalshi.com/trade-api/v2"
    polymarket_us_public_url: str = "https://gateway.polymarket.us"
    polymarket_global_public_url: str = "https://gamma-api.polymarket.com"


settings = Settings()
