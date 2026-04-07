from __future__ import annotations

import asyncio
from types import SimpleNamespace

from core.discovery_service import DiscoveryService
from core.schemas import ModelResponse


class DummyTracker:
    async def record(self, response: ModelResponse, prompt_type: str) -> None:  # pragma: no cover - no-op
        return None


class DummyRepository:
    def __init__(self) -> None:
        self.latest_run: dict[str, object] | None = None
        self.latest_candidates: list[dict[str, object]] = []

    async def record_discovery_run(self, payload: dict[str, object]) -> dict[str, object]:
        self.latest_run = payload
        return payload

    async def record_discovery_candidates(self, candidates: list[dict[str, object]]) -> None:
        self.latest_candidates = candidates

    async def get_latest_discovery_funnel(self, *, limit: int = 12) -> dict[str, object] | None:
        if self.latest_run is None:
            return None
        return {
            "run": self.latest_run,
            "candidates": self.latest_candidates[:limit],
        }


class DummyConnector:
    def __init__(self, markets: list[dict[str, object]], scan_stats: dict[str, object]) -> None:
        self.markets = markets
        self.last_scan_stats = scan_stats

    async def close(self) -> None:  # pragma: no cover - no-op
        return None

    async def get_active_markets(self, limit: int, crypto_only: bool = False) -> list[dict[str, object]]:
        return self.markets[:limit]


class DummyStrategy:
    def __init__(self, decisions: dict[str, object]) -> None:
        self.decisions = decisions

    async def analyze_market(self, market: dict[str, object]) -> object | None:
        return self.decisions.get(str(market["id"]))


class DummyProvider:
    def __init__(self, model: str, provider: str, responses: list[dict[str, object]]) -> None:
        self.model = model
        self.provider = provider
        self._responses = responses
        self.calls = 0

    async def call(self, prompt: str, system: str | None = None) -> ModelResponse:
        payload = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return ModelResponse(
            content=payload["content"],
            input_tokens=payload.get("input_tokens", 20),
            output_tokens=payload.get("output_tokens", 10),
            model=self.model,
            cost_usd=payload.get("cost_usd", 0.001),
            provider=self.provider,
            fallback_used=payload.get("fallback_used", False),
        )


def _make_candidate_decision(
    edge: float,
    confidence: float,
    direction: str = "YES",
    *,
    features_summary: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        strategy_id="trend_follow_bayes",
        direction=direction,
        edge=edge,
        confidence=confidence,
        features_summary=features_summary or {},
    )


def test_discovery_service_runs_cheap_llm_then_final_claude_only_on_shortlist() -> None:
    async def run_case() -> None:
        markets = [
            {
                "id": "m1",
                "question": "Will BTC be above 100k?",
                "asset_symbol": "BTC",
                "asset_name": "Bitcoin",
                "crypto_tier": "btc",
                "market_kind": "direct_coin",
                "volume_24h": 12000,
                "price_yes": 0.48,
                "price_no": 0.52,
                "orderbook_summary_yes": {"spread_bps": 40, "bid_depth": 150, "ask_depth": 120},
                "orderbook_summary_no": {"spread_bps": 42, "bid_depth": 140, "ask_depth": 110},
                "thesis_tags": ["btc", "macro"],
                "end_date": "2026-12-31T00:00:00Z",
            },
            {
                "id": "m2",
                "question": "Will ETH outperform BTC this quarter?",
                "asset_symbol": "ETH",
                "asset_name": "Ethereum",
                "crypto_tier": "major",
                "market_kind": "direct_coin",
                "volume_24h": 15000,
                "price_yes": 0.55,
                "price_no": 0.45,
                "orderbook_summary_yes": {"spread_bps": 48, "bid_depth": 130, "ask_depth": 110},
                "orderbook_summary_no": {"spread_bps": 50, "bid_depth": 125, "ask_depth": 100},
                "thesis_tags": ["eth", "defi"],
                "end_date": "2026-12-31T00:00:00Z",
            },
            {
                "id": "m3",
                "question": "Will DOGE hit a new ATH?",
                "asset_symbol": "DOGE",
                "asset_name": "Dogecoin",
                "crypto_tier": "major",
                "market_kind": "direct_coin",
                "volume_24h": 2000,
                "price_yes": 0.22,
                "price_no": 0.78,
                "orderbook_summary_yes": {"spread_bps": 60, "bid_depth": 20, "ask_depth": 18},
                "orderbook_summary_no": {"spread_bps": 58, "bid_depth": 18, "ask_depth": 15},
                "thesis_tags": ["doge"],
                "end_date": "2026-12-31T00:00:00Z",
            },
        ]
        decisions = {
            "m1": _make_candidate_decision(edge=0.31, confidence=0.80),
            "m2": _make_candidate_decision(edge=0.26, confidence=0.72),
            "m3": None,
        }
        repository = DummyRepository()
        context = SimpleNamespace(
            repository=repository,
            crypto_config=SimpleNamespace(
                enabled=True,
                indirect_min_edge_buffer=0.06,
                indirect_min_confidence_buffer=0.05,
                indirect_min_volume_multiplier=1.5,
                tier=lambda tier_name: SimpleNamespace(min_edge=0.20, min_confidence=0.60, min_volume_24h=5000.0, max_position_usd=100.0),
            ),
            risk_config=SimpleNamespace(max_spread_bps=250),
            agents_config=SimpleNamespace(
                agents={
                    "news_validator": SimpleNamespace(
                        model="gpt-4o-mini",
                        provider="openai",
                        temperature=0.0,
                        max_tokens=512,
                        fallback_model="gpt-4o-mini",
                        daily_cost_limit_usd=1.0,
                    ),
                    "claude": SimpleNamespace(
                        model="claude-sonnet-4-20250514",
                        provider="anthropic",
                        temperature=0.0,
                        max_tokens=512,
                        fallback_model="claude-3-5-haiku-20241022",
                        daily_cost_limit_usd=1.0,
                        scan_limit=24,
                    ),
                }
            ),
        )
        service = DiscoveryService(
            context,  # type: ignore[arg-type]
            connector=DummyConnector(markets, {"crypto_classified": 3, "selected_for_scan": 3}),
            strategy=DummyStrategy(decisions),
            research_provider=DummyProvider(
                model="gpt-4o-mini",
                provider="openai",
                responses=[
                    {"content": '{"recommendation":"promote","score":0.91,"summary":"strong fit","why":"high quality setup","risks":["liquidity"],"follow_up":"send to Claude"}'},
                    {"content": '{"recommendation":"watch","score":0.54,"summary":"okay but not strong","why":"watch only","risks":["thesis fatigue"],"follow_up":"wait"}'},
                ],
            ),
            final_provider=DummyProvider(
                model="claude-sonnet-4-20250514",
                provider="anthropic",
                responses=[
                    {"content": '{"operable":true,"final_score":0.88,"strategy_fit":"trend_follow_bayes","summary":"operable","blocked_reasons":[],"operator_note":"include in operation"}'},
                ],
            ),
            research_cost_tracker=DummyTracker(),
            final_cost_tracker=DummyTracker(),
        )

        result = await service.run(limit=3)

        assert result["run"]["universe_count"] == 3
        assert result["run"]["deterministic_passed_count"] == 2
        assert result["run"]["research_passed_count"] == 1
        assert result["run"]["claude_passed_count"] == 1
        assert result["run"]["operable_count"] == 1
        assert service.research_provider.calls == 2
        assert service.final_provider.calls == 1
        assert repository.latest_run is not None
        assert len(repository.latest_candidates) == 3
        assert result["stage_counts"][0]["count"] == 3

    asyncio.run(run_case())


def test_discovery_service_prioritizes_more_executable_volatile_setups() -> None:
    async def run_case() -> None:
        markets = [
            {
                "id": "calm",
                "question": "Will BTC stay range bound?",
                "asset_symbol": "BTC",
                "asset_name": "Bitcoin",
                "crypto_tier": "btc",
                "market_kind": "direct_coin",
                "volume_24h": 18000,
                "price_yes": 0.51,
                "price_no": 0.49,
                "orderbook_summary_yes": {"spread_bps": 32, "bid_depth": 260, "ask_depth": 250},
                "orderbook_summary_no": {"spread_bps": 34, "bid_depth": 250, "ask_depth": 240},
                "thesis_tags": ["btc", "range"],
                "end_date": "2026-12-31T00:00:00Z",
            },
            {
                "id": "volatile",
                "question": "Will BTC breakout higher this week?",
                "asset_symbol": "BTC",
                "asset_name": "Bitcoin",
                "crypto_tier": "btc",
                "market_kind": "direct_coin",
                "volume_24h": 18000,
                "price_yes": 0.52,
                "price_no": 0.48,
                "orderbook_summary_yes": {"spread_bps": 32, "bid_depth": 260, "ask_depth": 250},
                "orderbook_summary_no": {"spread_bps": 34, "bid_depth": 250, "ask_depth": 240},
                "thesis_tags": ["btc", "breakout"],
                "end_date": "2026-12-31T00:00:00Z",
            },
            {
                "id": "wide_spread",
                "question": "Will ETH spike today?",
                "asset_symbol": "ETH",
                "asset_name": "Ethereum",
                "crypto_tier": "major",
                "market_kind": "direct_coin",
                "volume_24h": 22000,
                "price_yes": 0.58,
                "price_no": 0.42,
                "orderbook_summary_yes": {"spread_bps": 140, "bid_depth": 220, "ask_depth": 210},
                "orderbook_summary_no": {"spread_bps": 138, "bid_depth": 200, "ask_depth": 190},
                "thesis_tags": ["eth", "spike"],
                "end_date": "2026-12-31T00:00:00Z",
            },
        ]
        decisions = {
            "calm": _make_candidate_decision(
                edge=0.24,
                confidence=0.73,
                features_summary={"momentum_short": 0.01, "momentum_medium": 0.01},
            ),
            "volatile": _make_candidate_decision(
                edge=0.24,
                confidence=0.73,
                features_summary={"momentum_short": 0.05, "momentum_medium": 0.06},
            ),
            "wide_spread": _make_candidate_decision(
                edge=0.22,
                confidence=0.67,
                features_summary={"momentum_short": 0.03, "momentum_medium": 0.04},
            ),
        }
        repository = DummyRepository()
        context = SimpleNamespace(
            repository=repository,
            crypto_config=SimpleNamespace(
                enabled=True,
                indirect_min_edge_buffer=0.06,
                indirect_min_confidence_buffer=0.05,
                indirect_min_volume_multiplier=1.5,
                tier=lambda tier_name: SimpleNamespace(min_edge=0.20, min_confidence=0.60, min_volume_24h=5000.0, max_position_usd=100.0),
            ),
            risk_config=SimpleNamespace(max_spread_bps=250),
            agents_config=SimpleNamespace(
                agents={
                    "news_validator": SimpleNamespace(
                        model="gpt-4o-mini",
                        provider="openai",
                        temperature=0.0,
                        max_tokens=512,
                        fallback_model="gpt-4o-mini",
                        daily_cost_limit_usd=1.0,
                    ),
                    "claude": SimpleNamespace(
                        model="claude-sonnet-4-20250514",
                        provider="anthropic",
                        temperature=0.0,
                        max_tokens=512,
                        fallback_model="claude-3-5-haiku-20241022",
                        daily_cost_limit_usd=1.0,
                        scan_limit=24,
                    ),
                }
            ),
        )
        service = DiscoveryService(
            context,  # type: ignore[arg-type]
            connector=DummyConnector(markets, {"crypto_classified": 3, "selected_for_scan": 3}),
            strategy=DummyStrategy(decisions),
            research_provider=DummyProvider(
                model="gpt-4o-mini",
                provider="openai",
                responses=[
                    {"content": '{"recommendation":"promote","score":0.91,"summary":"strong fit","why":"high quality setup","risks":["liquidity"],"follow_up":"send to Claude"}'},
                    {"content": '{"recommendation":"promote","score":0.88,"summary":"good fit","why":"volatile and executable","risks":["noise"],"follow_up":"keep"}'},
                    {"content": '{"recommendation":"promote","score":0.86,"summary":"acceptable","why":"edge but wide spread","risks":["spread"],"follow_up":"monitor"}'},
                ],
            ),
            final_provider=DummyProvider(
                model="claude-sonnet-4-20250514",
                provider="anthropic",
                responses=[
                    {"content": '{"operable":true,"final_score":0.88,"strategy_fit":"trend_follow_bayes","summary":"operable","blocked_reasons":[],"operator_note":"include in operation"}'},
                    {"content": '{"operable":true,"final_score":0.85,"strategy_fit":"trend_follow_bayes","summary":"operable","blocked_reasons":[],"operator_note":"include in operation"}'},
                    {"content": '{"operable":true,"final_score":0.82,"strategy_fit":"trend_follow_bayes","summary":"operable","blocked_reasons":[],"operator_note":"include in operation"}'},
                ],
            ),
            research_cost_tracker=DummyTracker(),
            final_cost_tracker=DummyTracker(),
        )

        result = await service.run(limit=3)

        assert result["run"]["universe_count"] == 3
        assert repository.latest_candidates[0]["market_id"] == "volatile"
        assert repository.latest_candidates[0]["score"] > repository.latest_candidates[1]["score"]
        assert repository.latest_candidates[1]["score"] >= repository.latest_candidates[2]["score"]

    asyncio.run(run_case())


def test_discovery_service_excludes_extreme_spreads_before_shortlist() -> None:
    async def run_case() -> None:
        markets = [
            {
                "id": "normal",
                "question": "Will BTC breakout this week?",
                "asset_symbol": "BTC",
                "asset_name": "Bitcoin",
                "crypto_tier": "btc",
                "market_kind": "direct_coin",
                "volume_24h": 20000,
                "price_yes": 0.52,
                "price_no": 0.48,
                "orderbook_summary_yes": {"spread_bps": 45, "bid_depth": 260, "ask_depth": 250},
                "orderbook_summary_no": {"spread_bps": 47, "bid_depth": 250, "ask_depth": 240},
                "thesis_tags": ["btc", "breakout"],
                "end_date": "2026-12-31T00:00:00Z",
            },
            {
                "id": "extreme",
                "question": "Will ETH spike hard today?",
                "asset_symbol": "ETH",
                "asset_name": "Ethereum",
                "crypto_tier": "major",
                "market_kind": "direct_coin",
                "volume_24h": 26000,
                "price_yes": 0.60,
                "price_no": 0.40,
                "orderbook_summary_yes": {"spread_bps": 560, "bid_depth": 220, "ask_depth": 210},
                "orderbook_summary_no": {"spread_bps": 558, "bid_depth": 200, "ask_depth": 190},
                "thesis_tags": ["eth", "spike"],
                "end_date": "2026-12-31T00:00:00Z",
            },
        ]
        decisions = {
            "normal": _make_candidate_decision(
                edge=0.29,
                confidence=0.77,
                features_summary={"momentum_short": 0.05, "momentum_medium": 0.06},
            ),
            "extreme": _make_candidate_decision(
                edge=0.32,
                confidence=0.81,
                features_summary={"momentum_short": 0.08, "momentum_medium": 0.09},
            ),
        }
        repository = DummyRepository()
        context = SimpleNamespace(
            repository=repository,
            crypto_config=SimpleNamespace(
                enabled=True,
                indirect_min_edge_buffer=0.06,
                indirect_min_confidence_buffer=0.05,
                indirect_min_volume_multiplier=1.5,
                tier=lambda tier_name: SimpleNamespace(min_edge=0.20, min_confidence=0.60, min_volume_24h=5000.0, max_position_usd=100.0),
            ),
            risk_config=SimpleNamespace(max_spread_bps=250),
            agents_config=SimpleNamespace(
                agents={
                    "news_validator": SimpleNamespace(
                        model="gpt-4o-mini",
                        provider="openai",
                        temperature=0.0,
                        max_tokens=512,
                        fallback_model="gpt-4o-mini",
                        daily_cost_limit_usd=1.0,
                    ),
                    "claude": SimpleNamespace(
                        model="claude-sonnet-4-20250514",
                        provider="anthropic",
                        temperature=0.0,
                        max_tokens=512,
                        fallback_model="claude-3-5-haiku-20241022",
                        daily_cost_limit_usd=1.0,
                        scan_limit=24,
                    ),
                }
            ),
        )
        service = DiscoveryService(
            context,  # type: ignore[arg-type]
            connector=DummyConnector(markets, {"crypto_classified": 2, "selected_for_scan": 2}),
            strategy=DummyStrategy(decisions),
            research_provider=DummyProvider(
                model="gpt-4o-mini",
                provider="openai",
                responses=[
                    {"content": '{"recommendation":"promote","score":0.91,"summary":"strong fit","why":"high quality setup","risks":["liquidity"],"follow_up":"send to Claude"}'},
                ],
            ),
            final_provider=DummyProvider(
                model="claude-sonnet-4-20250514",
                provider="anthropic",
                responses=[
                    {"content": '{"operable":true,"final_score":0.88,"strategy_fit":"trend_follow_bayes","summary":"operable","blocked_reasons":[],"operator_note":"include in operation"}'},
                ],
            ),
            research_cost_tracker=DummyTracker(),
            final_cost_tracker=DummyTracker(),
        )

        result = await service.run(limit=2)

        assert result["run"]["universe_count"] == 2
        assert result["run"]["deterministic_passed_count"] == 1
        assert service.research_provider.calls == 1
        assert service.final_provider.calls == 1
        assert "spread_extreme_before_shortlist" in result["run"]["rejected_breakdown"]
        assert repository.latest_candidates[0]["market_id"] == "normal"
        assert repository.latest_candidates[1]["market_id"] == "extreme"

    asyncio.run(run_case())
