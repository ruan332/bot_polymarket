from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent
AGENTS_CONFIG_PATH = ROOT_DIR / "config" / "agents.yaml"
RISK_CONFIG_PATH = ROOT_DIR / "config" / "risk.yaml"
CRYPTO_CONFIG_PATH = ROOT_DIR / "config" / "crypto.yaml"


class AgentRuntimeConfig(BaseModel):
    model: str
    provider: str
    temperature: float = 0.0
    max_tokens: int = 512
    fallback_model: str
    daily_cost_limit_usd: float = 1.0
    scan_limit: int | None = None
    confidence_threshold: float | None = None
    review_confidence_threshold: float | None = None
    order_ttl_seconds: int | None = None


class AgentsConfig(BaseModel):
    agents: dict[str, AgentRuntimeConfig]


class CryptoTierSettings(BaseModel):
    min_edge: float = 0.20
    min_confidence: float = 0.60
    min_volume_24h: float = 5000.0
    min_news_sources: int = 1
    min_news_support_score: float = 0.50
    max_news_conflict_score: float = 0.35
    max_position_usd: float = 75.0
    cooldown_minutes: int = 30

    @field_validator("min_edge", "min_confidence", "min_news_support_score", "max_news_conflict_score")
    @classmethod
    def validate_probability(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("value must be between 0 and 1")
        return value


class CryptoSettings(BaseModel):
    enabled: bool = True
    direct_coin_only: bool = False
    major_assets: list[str] = Field(default_factory=lambda: ["ETH", "SOL", "XRP", "DOGE"])
    scan_priority: list[Literal["btc", "major", "small_cap"]] = Field(
        default_factory=lambda: ["btc", "major", "small_cap"]
    )
    market_kind_priority: list[Literal["direct_coin", "indirect_crypto"]] = Field(
        default_factory=lambda: ["direct_coin", "indirect_crypto"]
    )
    indirect_min_edge_buffer: float = 0.06
    indirect_min_confidence_buffer: float = 0.05
    indirect_min_volume_multiplier: float = 1.5
    indirect_max_position_scale: float = 0.65
    btc: CryptoTierSettings = Field(
        default_factory=lambda: CryptoTierSettings(
            min_edge=0.20,
            min_confidence=0.60,
            min_volume_24h=5000.0,
            min_news_sources=1,
            min_news_support_score=0.45,
            max_news_conflict_score=0.45,
            max_position_usd=100.0,
            cooldown_minutes=20,
        )
    )
    major: CryptoTierSettings = Field(
        default_factory=lambda: CryptoTierSettings(
            min_edge=0.23,
            min_confidence=0.64,
            min_volume_24h=10000.0,
            min_news_sources=2,
            min_news_support_score=0.60,
            max_news_conflict_score=0.30,
            max_position_usd=70.0,
            cooldown_minutes=45,
        )
    )
    small_cap: CryptoTierSettings = Field(
        default_factory=lambda: CryptoTierSettings(
            min_edge=0.28,
            min_confidence=0.72,
            min_volume_24h=20000.0,
            min_news_sources=3,
            min_news_support_score=0.72,
            max_news_conflict_score=0.20,
            max_position_usd=35.0,
            cooldown_minutes=120,
        )
    )

    def tier(self, tier_name: str) -> CryptoTierSettings:
        if tier_name == "btc":
            return self.btc
        if tier_name == "major":
            return self.major
        return self.small_cap

    def market_kind_rank(self, market_kind: str) -> int:
        try:
            return self.market_kind_priority.index(market_kind)  # type: ignore[arg-type]
        except ValueError:
            return len(self.market_kind_priority)


class RiskSettings(BaseModel):
    min_edge: float = 0.19
    min_confidence: float = 0.55
    max_kelly_fraction: float = 0.25
    max_single_exposure_fraction: float = 0.10
    max_asset_exposure_fraction: float = 0.18
    max_strategy_exposure_fraction: float = 0.25
    max_single_position_usd: float = 100.0
    max_total_exposure_usd: float = 250.0
    max_daily_spend_usd: float = 5.0
    min_market_volume_24h: float = 5000.0
    max_order_price: float = 0.90
    max_spread_bps: int = 250
    max_slippage_bps: int = 150
    max_open_positions: int = 5
    circuit_breaker_error_threshold: int = 5
    circuit_breaker_loss_threshold_usd: float = 100.0
    circuit_breaker_cooldown_seconds: int = 300
    default_limit_buffer_bps: int = 50
    daily_drawdown_limit_fraction: float = 0.08
    loss_streak_size_discount: float = 0.15
    min_risk_fraction_after_losses: float = 0.35
    exit_scale_out_fraction: float = 0.50
    synthetic_asset_exposure_fraction: float = 0.10

    @field_validator(
        "min_edge",
        "min_confidence",
        "max_kelly_fraction",
        "max_single_exposure_fraction",
        "max_asset_exposure_fraction",
        "max_strategy_exposure_fraction",
        "daily_drawdown_limit_fraction",
        "loss_streak_size_discount",
        "min_risk_fraction_after_losses",
        "exit_scale_out_fraction",
        "synthetic_asset_exposure_fraction",
    )
    @classmethod
    def validate_probability(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("value must be between 0 and 1")
        return value


class RiskConfig(BaseModel):
    risk: RiskSettings


class CryptoConfig(BaseModel):
    crypto: CryptoSettings


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql://trading:trading@localhost:5432/trading"
    redis_url: str = "redis://localhost:6379/0"
    live_trading: bool = False
    smoke_test_mode: bool = False
    news_validation_enabled: bool = True
    review_llm_enabled: bool = False
    execution_llm_enabled: bool = False
    review_llm_fail_open: bool = False
    execution_llm_fail_open: bool = True
    max_daily_spend_usd: float = 5.0
    max_single_position_usd: float = 100.0
    paper_bankroll_usd: float = 1000.0
    agent_heartbeat_ttl_seconds: int = 45
    polymarket_private_key: str = ""
    polymarket_api_key: str = ""
    polymarket_api_secret: str = ""
    polymarket_api_passphrase: str = ""
    polymarket_funder: str = ""
    polymarket_signature_type: int = 0
    polymarket_chain_id: int = 137
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com"
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_market_ws: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    news_provider_primary: Literal["marketaux", "alphavantage"] = "marketaux"
    news_provider_fallback: Literal["marketaux", "alphavantage"] = "alphavantage"
    news_lookback_hours: int = 24
    news_http_timeout_seconds: int = 15
    news_fallback_on_quota: bool = True
    news_fallback_on_rate_limit: bool = True
    news_fallback_on_upstream_error: bool = True
    news_fallback_on_empty_result: bool = False
    marketaux_api_key: str = ""
    marketaux_base_url: str = "https://api.marketaux.com/v1/news/all"
    marketaux_language: str = "en"
    marketaux_limit_per_request: int = 3
    alphavantage_api_key: str = ""
    alphavantage_base_url: str = "https://www.alphavantage.co/query"
    alphavantage_news_limit: int = 50


def get_enabled_agent_names(settings: AppSettings, agents_config: AgentsConfig) -> list[str]:
    names = list(agents_config.agents.keys())
    if not settings.news_validation_enabled:
        names = [name for name in names if name != "news_validator"]
    return names


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def load_agents_config(path: Path = AGENTS_CONFIG_PATH) -> AgentsConfig:
    return AgentsConfig.model_validate(_load_yaml(path))


def load_risk_config(path: Path = RISK_CONFIG_PATH) -> RiskSettings:
    return RiskConfig.model_validate(_load_yaml(path)).risk


def load_crypto_config(path: Path = CRYPTO_CONFIG_PATH) -> CryptoSettings:
    return CryptoConfig.model_validate(_load_yaml(path)).crypto


def infer_provider_from_model(model: str) -> tuple[str | None, str]:
    raw_model = model.strip()
    if "/" in raw_model:
        provider, model_name = raw_model.split("/", 1)
        return provider.strip().lower(), model_name.strip()
    lowered = raw_model.lower()
    if lowered.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai", raw_model
    if lowered.startswith("claude"):
        return "anthropic", raw_model
    if lowered.startswith("gemini") or lowered.startswith("models/gemini"):
        return "google", raw_model
    return None, raw_model


def update_agent_model(
    agent_name: str,
    model: str,
    provider: str | None = None,
    fallback_model: str | None = None,
    path: Path = AGENTS_CONFIG_PATH,
) -> AgentsConfig:
    payload = _load_yaml(path)
    if agent_name not in payload.get("agents", {}):
        raise KeyError(f"unknown agent: {agent_name}")
    previous_provider = str(payload["agents"][agent_name].get("provider", "")).strip().lower()
    inferred_provider, normalized_model = infer_provider_from_model(model)
    selected_provider = (provider or inferred_provider or payload["agents"][agent_name].get("provider", "")).strip().lower()
    payload["agents"][agent_name]["model"] = normalized_model
    if selected_provider:
        payload["agents"][agent_name]["provider"] = selected_provider
    if fallback_model:
        _, normalized_fallback = infer_provider_from_model(fallback_model)
        payload["agents"][agent_name]["fallback_model"] = normalized_fallback
    elif selected_provider != previous_provider:
        payload["agents"][agent_name]["fallback_model"] = normalized_model
    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(payload, file, sort_keys=False)
    return AgentsConfig.model_validate(payload)
