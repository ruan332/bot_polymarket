from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]
CLIMA_BOT_DIR = ROOT_DIR / "clima_bot"
DEFAULT_ENV_PATH = CLIMA_BOT_DIR / ".env.clima_bot"
DEFAULT_STORAGE_PATH = CLIMA_BOT_DIR / "data" / "clima_bot.sqlite3"


class ClimaBotSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    live_trading: bool = False
    smoke_test_mode: bool = False
    paper_bankroll_usd: float = 1000.0
    polymarket_private_key: str = ""
    polymarket_api_key: str = ""
    polymarket_api_secret: str = ""
    polymarket_api_passphrase: str = ""
    polymarket_funder: str = ""
    polymarket_signature_type: int = 0
    polymarket_chain_id: int = 137
    polymarket_live_min_usdc_balance: float = 5.0
    polymarket_live_min_order_size: int = 5
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com"
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_data_api_url: str = "https://data-api.polymarket.com"
    polymarket_market_ws: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    clima_bot_remote_env_host: str = "bot-polymarket-vps"
    clima_bot_remote_env_path: str = "/opt/polymarket-bot/.env"
    clima_bot_category: str = "WEATHER"
    clima_bot_max_wallets: int = 10
    clima_bot_copy_trade_fraction: float = 0.08
    clima_bot_min_notional_usd: float = 1.0
    clima_bot_max_notional_usd: float = 2.5
    clima_bot_poll_interval_seconds: int = 20
    clima_bot_storage_path: str = str(DEFAULT_STORAGE_PATH)
    clima_bot_copy_wallets: Annotated[list[str], NoDecode] = Field(default_factory=list)
    clima_bot_auto_sync: bool = True

    @classmethod
    def load(cls, env_path: Path | None = None) -> "ClimaBotSettings":
        path = env_path or DEFAULT_ENV_PATH
        return cls(_env_file=path if path.exists() else None)

    @property
    def storage_path(self) -> Path:
        return Path(self.clima_bot_storage_path).expanduser()

    @field_validator("clima_bot_copy_wallets", mode="before")
    @classmethod
    def parse_wallets(cls, value: Any) -> list[str]:
        if value in (None, "", []):
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()]

    @field_validator("clima_bot_max_wallets", "clima_bot_poll_interval_seconds")
    @classmethod
    def validate_positive_int(cls, value: int) -> int:
        if value < 1:
            raise ValueError("value must be >= 1")
        return value

    @field_validator("clima_bot_copy_trade_fraction")
    @classmethod
    def validate_fraction(cls, value: float) -> float:
        if not 0 < value <= 1:
            raise ValueError("copy trade fraction must be between 0 and 1")
        return value

    @field_validator("clima_bot_min_notional_usd", "clima_bot_max_notional_usd", "paper_bankroll_usd")
    @classmethod
    def validate_positive_float(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("value must be > 0")
        return value
