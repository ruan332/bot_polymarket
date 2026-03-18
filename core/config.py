from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent
AGENTS_CONFIG_PATH = ROOT_DIR / "config" / "agents.yaml"
RISK_CONFIG_PATH = ROOT_DIR / "config" / "risk.yaml"


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


class RiskSettings(BaseModel):
    min_edge: float = 0.19
    min_confidence: float = 0.55
    max_kelly_fraction: float = 0.25
    max_single_exposure_fraction: float = 0.10
    max_single_position_usd: float = 100.0
    max_total_exposure_usd: float = 250.0
    max_daily_spend_usd: float = 5.0
    max_spread_bps: int = 250
    max_slippage_bps: int = 150
    max_open_positions: int = 5
    circuit_breaker_error_threshold: int = 5
    circuit_breaker_loss_threshold_usd: float = 100.0
    default_limit_buffer_bps: int = 50

    @field_validator("min_edge", "min_confidence", "max_kelly_fraction", "max_single_exposure_fraction")
    @classmethod
    def validate_probability(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("value must be between 0 and 1")
        return value


class RiskConfig(BaseModel):
    risk: RiskSettings


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql://trading:trading@localhost:5432/trading"
    redis_url: str = "redis://localhost:6379/0"
    live_trading: bool = False
    smoke_test_mode: bool = False
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


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def load_agents_config(path: Path = AGENTS_CONFIG_PATH) -> AgentsConfig:
    return AgentsConfig.model_validate(_load_yaml(path))


def load_risk_config(path: Path = RISK_CONFIG_PATH) -> RiskSettings:
    return RiskConfig.model_validate(_load_yaml(path)).risk


def update_agent_model(agent_name: str, model: str, path: Path = AGENTS_CONFIG_PATH) -> AgentsConfig:
    payload = _load_yaml(path)
    if agent_name not in payload.get("agents", {}):
        raise KeyError(f"unknown agent: {agent_name}")
    payload["agents"][agent_name]["model"] = model
    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(payload, file, sort_keys=False)
    return AgentsConfig.model_validate(payload)
