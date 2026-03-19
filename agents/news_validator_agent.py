from __future__ import annotations

from uuid import uuid4

from agents.base import BaseAgent
from core.app_context import AppContext
from core.exceptions import InvalidModelResponseError
from core.news_provider import NewsFetchResult, NewsProvider
from core.schemas import NewsValidationPayload, SignalPayload
from core.utils import parse_json_object, sanitize_text


SYSTEM_PROMPT = """
Você valida sinais de trading com base em notícias recentes.
Analise as manchetes e responda APENAS com JSON válido:
{"support_score": 0.7, "conflict_score": 0.1, "reason": "...", "headline_summaries": ["..."]}
"""


class NewsValidatorAgent(BaseAgent):
    def __init__(self, context: AppContext):
        super().__init__("news_validator", context)
        self.news_provider = NewsProvider(context)
        self.consumer = f"news-{uuid4().hex[:8]}"

    async def tick(self) -> None:
        await self.context.bus.ensure_group("signals:candidates", "news_validators")
        events = await self.context.bus.read_group(
            "signals:candidates",
            "news_validators",
            self.consumer,
            block_ms=250,
            count=1,
        )
        for event_id, payload in events:
            signal = SignalPayload.model_validate(payload)
            try:
                validation = await self.validate_signal(signal)
                summary = self._validation_summary(validation)
                await self.context.repository.record_news_validation(validation)
                await self.context.repository.attach_news_validation(signal.signal_id, summary)
                if validation.validated:
                    enriched = signal.model_copy(update={"news_validation": summary})
                    await self.context.bus.publish_event("signals:validated", enriched.model_dump(mode="json"))
                else:
                    await self.context.repository.record_risk_event(
                        event_id=str(uuid4()),
                        agent=self.name,
                        reason=validation.reason or "news validation rejected signal",
                        payload={
                            "signal_id": signal.signal_id,
                            "market_id": signal.market_id,
                            "asset_symbol": signal.asset_symbol,
                            "crypto_tier": signal.crypto_tier,
                            "news_validation": validation.model_dump(mode="json"),
                        },
                    )
            finally:
                await self.context.bus.ack("signals:candidates", "news_validators", event_id)

    async def validate_signal(self, signal: SignalPayload) -> NewsValidationPayload:
        news_result = await self.news_provider.fetch_news(
            asset_symbol=signal.asset_symbol,
            asset_name=signal.asset_name,
            thesis_tags=signal.thesis_tags,
            market_question=signal.market_question,
        )
        headlines = news_result.articles
        if not headlines:
            return self._build_validation(
                signal,
                validated=False,
                support_score=0.0,
                conflict_score=0.0,
                source_count=0,
                freshness_minutes=None,
                headlines=[],
                reason=news_result.primary_error_type or "no_news_found",
                news_result=news_result,
            )

        formatted_headlines = "\n".join(
            f"- [{item.source}] {sanitize_text(item.title, 140)} :: {sanitize_text(item.summary, 180)}"
            for item in headlines[:8]
        )
        prompt = f"""
Ativo: {signal.asset_symbol} ({signal.asset_name})
Tier: {signal.crypto_tier}
Mercado: {sanitize_text(signal.market_question, 200)}
Direcao do sinal: {signal.direction}
Reasoning do scanner: {sanitize_text(signal.reasoning, 240)}
Headlines:
{formatted_headlines}
"""
        response = await self.provider.call(prompt=prompt, system=SYSTEM_PROMPT)
        await self.cost_tracker.record(response, prompt_type="validate_news")

        try:
            payload = parse_json_object(response.content)
        except Exception as exc:
            raise InvalidModelResponseError(f"news validator returned invalid JSON: {exc}") from exc

        support_score = float(payload.get("support_score", 0.0))
        conflict_score = float(payload.get("conflict_score", 0.0))
        source_count = len({item.source for item in headlines if item.source})
        tier_cfg = self.context.crypto_config.tier(signal.crypto_tier)
        validated = (
            source_count >= tier_cfg.min_news_sources
            and support_score >= tier_cfg.min_news_support_score
            and conflict_score <= tier_cfg.max_news_conflict_score
        )
        freshest = [item.freshness_minutes for item in headlines if item.freshness_minutes is not None]
        headline_summaries = [str(item) for item in payload.get("headline_summaries", []) if str(item).strip()]
        if not headline_summaries:
            headline_summaries = [sanitize_text(item.title, 120) for item in headlines[:5]]
        return self._build_validation(
            signal,
            validated=validated,
            support_score=support_score,
            conflict_score=conflict_score,
            source_count=source_count,
            freshness_minutes=min(freshest) if freshest else None,
            headlines=headline_summaries,
            reason=str(payload.get("reason", "")) or ("news_validation_passed" if validated else "news_validation_rejected"),
            news_result=news_result,
        )

    def _build_validation(
        self,
        signal: SignalPayload,
        *,
        validated: bool,
        support_score: float,
        conflict_score: float,
        source_count: int,
        freshness_minutes: int | None,
        headlines: list[str],
        reason: str,
        news_result: NewsFetchResult,
    ) -> NewsValidationPayload:
        return NewsValidationPayload(
            validation_id=str(uuid4()),
            signal_id=signal.signal_id,
            market_id=signal.market_id,
            asset_symbol=signal.asset_symbol,
            asset_name=signal.asset_name,
            crypto_tier=signal.crypto_tier,
            validated=validated,
            support_score=support_score,
            conflict_score=conflict_score,
            source_count=source_count,
            provider_used=news_result.provider_used,
            fallback_used=news_result.fallback_used,
            provider_attempts=[attempt.model_dump(mode="json") for attempt in news_result.provider_attempts],
            primary_error_type=news_result.primary_error_type,
            primary_error_message=news_result.primary_error_message,
            freshness_minutes=freshness_minutes,
            headlines=headlines,
            reason=reason,
            metadata={"thesis_hash": signal.thesis_hash},
        )

    @staticmethod
    def _validation_summary(validation: NewsValidationPayload) -> dict[str, object]:
        return {
            "validated": validation.validated,
            "support_score": validation.support_score,
            "conflict_score": validation.conflict_score,
            "source_count": validation.source_count,
            "provider_used": validation.provider_used,
            "fallback_used": validation.fallback_used,
            "provider_attempts": validation.provider_attempts,
            "primary_error_type": validation.primary_error_type,
            "primary_error_message": validation.primary_error_message,
            "freshness_minutes": validation.freshness_minutes,
            "headlines": validation.headlines,
            "reason": validation.reason,
        }

    async def close(self) -> None:
        await super().close()
        await self.news_provider.close()
