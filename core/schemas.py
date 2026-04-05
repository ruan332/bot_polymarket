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
    flow_analysis: dict[str, Any] | None = None
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
    review_mode: Literal["deterministic", "llm", "llm_fallback"] = "deterministic"
    exit_reason_if_blocked: str = ""
    notes: str = ""
    llm_notes: str = ""
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
    trade_group_id: str = ""
    cycle_slug: str = ""
    leg_role: str = ""
    take_profit_price: float | None = None
    stop_loss_price: float | None = None
    time_stop_minutes: int | None = None
    direction: Literal["YES", "NO"]
    size: int
    price_limit: float
    notional_usd: float
    entry_notional_target_usd: float | None = None
    entry_notional_actual_usd: float | None = None
    take_profit_target_usd: float | None = None
    realized_pnl_usd: float = 0.0
    exit_reason: str = ""
    execution_mode: Literal["deterministic", "llm", "llm_fallback"] = "deterministic"
    status: Literal["simulated", "simulated_pending", "blocked", "live_submitted", "live_filled"] = "simulated"
    reason: str = ""
    news_validation: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PairLegPlan(BaseModel):
    market_id: str
    token_id: str
    direction: Literal["YES", "NO"]
    leg_role: Literal["primary", "hedge"]
    size: int
    target_price: float
    reference_price: float
    current_ask: float
    current_bid: float = 0.0


class PairSignalPayload(BaseModel):
    version: str = "v1"
    event_type: Literal["pair_signal.created"] = "pair_signal.created"
    signal_id: str
    trade_group_id: str
    cycle_slug: str
    cycle_start: datetime
    market_id: str
    market_question: str
    asset_symbol: str
    asset_name: str
    crypto_tier: Literal["btc", "major", "small_cap"]
    strategy_id: str = "pair_15m"
    strategy_version: str = "v1"
    predictor_direction: Literal["up", "down"]
    predictor_signal: Literal["BUY_UP", "BUY_DOWN"]
    predictor_confidence: float
    side_count_state: dict[str, Any] = Field(default_factory=dict)
    primary_leg: PairLegPlan
    hedge_leg: PairLegPlan
    reasoning: str = ""
    flow_analysis: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PairReviewPayload(BaseModel):
    version: str = "v1"
    event_type: Literal["pair_signal.reviewed"] = "pair_signal.reviewed"
    signal_id: str
    trade_group_id: str
    asset_symbol: str
    crypto_tier: Literal["btc", "major", "small_cap"] | None = None
    strategy_id: str = "pair_15m"
    approved: bool
    review_mode: Literal["deterministic", "llm", "llm_fallback"] = "deterministic"
    notes: str = ""
    llm_notes: str = ""
    approved_primary_leg: PairLegPlan
    approved_hedge_leg: PairLegPlan
    original_signal: PairSignalPayload
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PairOrderPayload(BaseModel):
    version: str = "v1"
    event_type: Literal["pair_order.submitted"] = "pair_order.submitted"
    order_id: str
    signal_id: str
    trade_group_id: str
    cycle_slug: str
    market_id: str
    token_id: str
    market_question: str = ""
    asset_symbol: str = ""
    crypto_tier: Literal["btc", "major", "small_cap"] | None = None
    strategy_id: str = "pair_15m"
    position_key: str = ""
    leg_role: Literal["primary", "hedge"]
    direction: Literal["YES", "NO"]
    size: int
    price_limit: float
    reference_price: float
    notional_usd: float
    hedge_status: str = ""
    status: Literal["simulated", "simulated_pending", "blocked", "live_submitted", "live_filled"] = "simulated"
    reason: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PendingPairOrderPayload(BaseModel):
    version: str = "v1"
    event_type: Literal["pair_order.pending"] = "pair_order.pending"
    pending_order_id: str
    trade_group_id: str
    signal_id: str
    cycle_slug: str
    market_id: str
    market_question: str = ""
    asset_symbol: str = ""
    crypto_tier: Literal["btc", "major", "small_cap"] | None = None
    strategy_id: str = "pair_15m"
    position_key: str = ""
    token_id: str
    direction: Literal["YES", "NO"]
    leg_role: Literal["hedge"] = "hedge"
    size: int
    target_price: float
    reference_price: float
    exchange_order_id: str = ""
    submission_status: str = ""
    submission_created_at: datetime | None = None
    status: Literal["pending", "filled", "cancelled", "expired"] = "pending"
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SettlementEventPayload(BaseModel):
    version: str = "v1"
    event_type: Literal["settlement.processed"] = "settlement.processed"
    settlement_id: str
    market_id: str
    position_key: str
    token_id: str
    market_question: str = ""
    asset_symbol: str = ""
    strategy_id: str = ""
    trade_group_id: str = ""
    cycle_slug: str = ""
    leg_role: str = ""
    direction: Literal["YES", "NO"]
    size: int
    average_price: float
    payout_price: float
    payout_usd: float
    cost_basis_usd: float
    realized_pnl_usd: float
    status: Literal["dry_run", "settled", "skipped"] = "dry_run"
    reason: str = ""
    resolution: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PairCycleStatePayload(BaseModel):
    asset_symbol: str
    asset_name: str
    crypto_tier: Literal["btc", "major", "small_cap"]
    cycle_slug: str
    cycle_start: datetime
    market_id: str
    market_question: str
    token_id_yes: str
    token_id_no: str
    price_yes: float = 0.0
    price_no: float = 0.0
    status: Literal["active", "paused", "rolled"] = "active"
    side_counts: dict[str, int] = Field(default_factory=dict)
    max_buy_counts_per_side: int = 1
    last_signal_direction: Literal["YES", "NO"] | None = None
    last_signal_at: datetime | None = None
    last_quote_at: datetime | None = None
    predictor_state: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FlowAnalysisPayload(BaseModel):
    version: str = "v1"
    event_type: Literal["flow.analysis"] = "flow.analysis"
    flow_id: str
    signal_id: str | None = None
    trade_group_id: str | None = None
    market_id: str
    cycle_slug: str = ""
    market_question: str = ""
    asset_symbol: str = ""
    asset_name: str = ""
    crypto_tier: Literal["btc", "major", "small_cap"]
    window_minutes: int = 15
    dominant_direction: Literal["up", "down", "neutral"] = "neutral"
    dominance_score: float = 0.0
    confidence: float = 0.5
    up_trade_count: int = 0
    down_trade_count: int = 0
    up_notional: float = 0.0
    down_notional: float = 0.0
    total_trades: int = 0
    total_notional: float = 0.0
    freshness_seconds: float = 0.0
    source_used: Literal["ws", "data_api", "mixed"] = "ws"
    sample_count: int = 0
    last_trade_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    mode: Literal["paper", "live"] = "paper"
    balance_source: str = "paper_ledger"
    funder: str = ""
    live_balance: float | None = None
    live_allowance: float | None = None


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
    trigger_source: str = ""
    source: str = "system"


class ModelSwapRequest(BaseModel):
    agent: Literal["claude", "news_validator", "codex", "claw"]
    model: str
    provider: str | None = None
    fallback_model: str | None = None
