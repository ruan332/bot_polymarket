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
    reasoning: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewPayload(BaseModel):
    version: str = "v1"
    event_type: Literal["signal.reviewed"] = "signal.reviewed"
    signal_id: str
    approved: bool
    corrected_price_limit: float | None = None
    kelly_size: int = 0
    notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    original_signal: SignalPayload


class PaperOrderPayload(BaseModel):
    version: str = "v1"
    event_type: Literal["order.paper_submitted"] = "order.paper_submitted"
    order_id: str
    signal_id: str
    market_id: str
    token_id: str
    direction: Literal["YES", "NO"]
    size: int
    price_limit: float
    notional_usd: float
    status: Literal["simulated", "blocked"] = "simulated"
    reason: str = ""
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
    available_balance: float
    total_exposure: float
    open_positions: int
    realized_pnl: float
    unrealized_pnl: float


class ModelSwapRequest(BaseModel):
    agent: Literal["claude", "codex", "claw"]
    model: str
