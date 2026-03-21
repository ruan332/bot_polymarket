from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ModelResponse(BaseModel):
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    cost_usd: float
    provider: str
    fallback_used: bool = False


class SignalPayload(BaseModel):
    version: str = "v1"
    event_type: Literal["signal.created"] = "signal.created"
    signal_id: str
    market_id: str
    token_id: str
    market_question: str
    direction: Literal["YES", "NO"]
    edge: float
    confidence: float
    price: float
    price_yes: float
    price_no: float
    volume_24h: float = 0.0
    asset_symbol: str
    asset_name: str
    crypto_tier: Literal["btc", "major", "small_cap"]
    market_kind: str = "direct_coin"
    question_type: str = "direction"
    strategy_id: str = "trend_follow_bayes"
    strategy_version: str = "v1"
    model_probability: float = 0.0
    market_probability: float = 0.0
    regime: Literal["trend", "mean_revert", "illiquid_choppy"] = "trend"
    expected_slippage_bps: float = 0.0
    expected_holding_minutes: int = 180
    thesis_tags: list[str] = Field(default_factory=list)
    thesis_hash: str = ""
    reasoning: str = ""
    features_summary: dict[str, Any] = Field(default_factory=dict)
    liquidity_summary: dict[str, Any] = Field(default_factory=dict)
    news_validation: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class NewsValidationPayload(BaseModel):
    version: str = "v1"
    event_type: Literal["signal.news_validated"] = "signal.news_validated"
    validation_id: str
    signal_id: str
    market_id: str
    asset_symbol: str
    asset_name: str
    crypto_tier: Literal["btc", "major", "small_cap"]
    validated: bool
    support_score: float
    conflict_score: float
    source_count: int
    provider_used: str = ""
    fallback_used: bool = False
    provider_attempts: list[dict[str, Any]] = Field(default_factory=list)
    primary_error_type: str | None = None
    primary_error_message: str | None = None
    freshness_minutes: int | None = None
    headlines: list[str] = Field(default_factory=list)
    reason: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewPayload(BaseModel):
    version: str = "v1"
    event_type: Literal["signal.reviewed"] = "signal.reviewed"
    signal_id: str
    approved: bool
    asset_symbol: str = ""
    crypto_tier: Literal["btc", "major", "small_cap"] | None = None
    corrected_price_limit: float | None = None
    kelly_size: int = 0
    risk_fraction: float = 0.0
    take_profit_price: float | None = None
    stop_loss_price: float | None = None
    time_stop_minutes: int | None = None
    exit_reason_if_blocked: str = ""
    notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    original_signal: SignalPayload
    news_validation: NewsValidationPayload | None = None


class PaperOrderPayload(BaseModel):
    version: str = "v1"
    event_type: Literal["order.paper_submitted"] = "order.paper_submitted"
    order_id: str
    signal_id: str
    market_id: str
    token_id: str
    market_question: str = ""
    asset_symbol: str = ""
    crypto_tier: Literal["btc", "major", "small_cap"] | None = None
    action: Literal["entry", "scale_in", "scale_out", "close"] = "entry"
    position_key: str = ""
    strategy_id: str = ""
    regime: str = ""
    take_profit_price: float | None = None
    stop_loss_price: float | None = None
    time_stop_minutes: int | None = None
    direction: Literal["YES", "NO"]
    size: int
    price_limit: float
    notional_usd: float
    realized_pnl_usd: float = 0.0
    exit_reason: str = ""
    status: Literal["simulated", "blocked"] = "simulated"
    reason: str = ""
    news_validation: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RiskEventPayload(BaseModel):
    version: str = "v1"
    event_type: Literal["risk.blocked"] = "risk.blocked"
    agent: str
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentHeartbeat(BaseModel):
    agent: str
    model: str
    running: bool
    config_version: int
    last_seen: datetime = Field(default_factory=lambda: datetime.now(UTC))
    meta: dict[str, Any] = Field(default_factory=dict)


class PortfolioSummary(BaseModel):
    available_balance: float = 0.0
    total_exposure: float = 0.0
    current_market_value: float = 0.0
    total_equity: float = 0.0
    total_pnl: float = 0.0
    open_positions: int = 0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0


class MarketSnapshotPayload(BaseModel):
    market_id: str
    question: str
    token_id_yes: str
    token_id_no: str
    price_yes: float
    price_no: float
    volume_24h: float = 0.0
    asset_symbol: str = ""
    asset_name: str = ""
    crypto_tier: str = ""
    market_kind: str = ""
    question_type: str = ""
    thesis_tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class EquitySnapshotPoint(BaseModel):
    created_at: datetime
    available_balance: float
    total_exposure: float
    current_market_value: float
    total_equity: float
    total_pnl: float
    realized_pnl: float
    unrealized_pnl: float
    source: str = "system"


class ModelSwapRequest(BaseModel):
    agent: Literal["claude", "news_validator", "codex", "claw"]
    model: str
    provider: str | None = None
    fallback_model: str | None = None
