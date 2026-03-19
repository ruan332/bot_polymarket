from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import TYPE_CHECKING, Any, Literal

import aiohttp
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from core.app_context import AppContext


class NewsArticle(BaseModel):
    title: str
    summary: str = ""
    source: str = "unknown"
    url: str = ""
    published_at: str = ""
    freshness_minutes: int | None = None
    provider: str
    provider_status: Literal["primary", "fallback", "smoke"]
    provider_latency_ms: int | None = None


class NewsProviderAttempt(BaseModel):
    provider: str
    role: Literal["primary", "fallback"]
    succeeded: bool
    article_count: int = 0
    latency_ms: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    status_code: int | None = None


class NewsFetchResult(BaseModel):
    articles: list[NewsArticle] = Field(default_factory=list)
    provider_used: str = ""
    fallback_used: bool = False
    provider_attempts: list[NewsProviderAttempt] = Field(default_factory=list)
    primary_error_type: str | None = None
    primary_error_message: str | None = None
    article_count: int = 0


class ProviderCallError(RuntimeError):
    def __init__(
        self,
        provider: str,
        error_type: str,
        message: str,
        *,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.error_type = error_type
        self.message = message
        self.status_code = status_code


@dataclass(slots=True)
class AttemptOutcome:
    articles: list[NewsArticle]
    attempt: NewsProviderAttempt
    should_fallback: bool = False


class BaseNewsAdapter:
    provider_name = "base"

    def __init__(self, context: AppContext, owner: "NewsProvider"):
        self.context = context
        self.owner = owner

    async def fetch_articles(
        self,
        *,
        asset_symbol: str,
        asset_name: str,
        thesis_tags: list[str],
        market_question: str,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def _request_json(
        self,
        *,
        url: str,
        params: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        session = await self.owner._client()
        try:
            async with session.get(url, params=params, headers=headers) as response:
                payload = await response.json(content_type=None)
                return response.status, payload
        except aiohttp.ClientConnectionError as exc:
            raise ProviderCallError(self.provider_name, "connection_error", str(exc)) from exc
        except aiohttp.ClientError as exc:
            raise ProviderCallError(self.provider_name, "upstream_error", str(exc)) from exc
        except TimeoutError as exc:
            raise ProviderCallError(self.provider_name, "timeout", str(exc)) from exc

    @staticmethod
    def _normalize_item(
        item: dict[str, Any],
        *,
        provider: str,
        provider_status: Literal["primary", "fallback", "smoke"],
        provider_latency_ms: int | None,
        published_at_keys: tuple[str, ...],
        title_keys: tuple[str, ...] = ("title", "headline"),
        summary_keys: tuple[str, ...] = ("summary", "description", "snippet"),
        source_keys: tuple[str, ...] = ("source", "publisher"),
        url_keys: tuple[str, ...] = ("url", "link"),
    ) -> NewsArticle | None:
        title = BaseNewsAdapter._coalesce(item, title_keys)
        if not title:
            return None
        published_at = BaseNewsAdapter._coalesce(item, published_at_keys)
        normalized_published_at, freshness_minutes = BaseNewsAdapter._parse_published_at(published_at)
        return NewsArticle(
            title=title,
            summary=BaseNewsAdapter._coalesce(item, summary_keys),
            source=BaseNewsAdapter._coalesce(item, source_keys) or "unknown",
            url=BaseNewsAdapter._coalesce(item, url_keys),
            published_at=normalized_published_at,
            freshness_minutes=freshness_minutes,
            provider=provider,
            provider_status=provider_status,
            provider_latency_ms=provider_latency_ms,
        )

    @staticmethod
    def _coalesce(item: dict[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            value = item.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _parse_published_at(value: Any) -> tuple[str, int | None]:
        if value is None:
            return "", None
        raw = str(value).strip()
        if not raw:
            return "", None

        parsed: datetime | None = None
        try:
            if raw.endswith("Z") or "+" in raw:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            elif len(raw) == 15 and "T" in raw:
                parsed = datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
            elif len(raw) == 13 and "T" in raw:
                parsed = datetime.strptime(raw, "%Y%m%dT%H%M").replace(tzinfo=UTC)
            else:
                parsed = datetime.fromisoformat(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
        except Exception:
            return raw, None

        normalized = parsed.astimezone(UTC).isoformat()
        freshness_minutes = int((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() // 60)
        return normalized, max(freshness_minutes, 0)


class MarketauxNewsAdapter(BaseNewsAdapter):
    provider_name = "marketaux"

    async def fetch_articles(
        self,
        *,
        asset_symbol: str,
        asset_name: str,
        thesis_tags: list[str],
        market_question: str,
    ) -> list[dict[str, Any]]:
        settings = self.context.settings
        if not settings.marketaux_api_key:
            raise ProviderCallError(self.provider_name, "configuration_error", "MARKETAUX_API_KEY is not configured")

        published_after = (datetime.now(UTC) - timedelta(hours=max(settings.news_lookback_hours, 1))).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        search_query = self._build_search_query(asset_symbol, asset_name, thesis_tags, market_question)
        params = {
            "api_token": settings.marketaux_api_key,
            "symbols": asset_symbol.upper(),
            "filter_entities": "true",
            "must_have_entities": "true",
            "group_similar": "true",
            "language": settings.marketaux_language,
            "limit": max(settings.marketaux_limit_per_request, 1),
            "published_after": published_after,
        }
        if search_query:
            params["search"] = search_query

        status_code, payload = await self._request_json(url=settings.marketaux_base_url, params=params)
        if status_code >= 400:
            raise self._build_error(payload, status_code)

        items = payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            raise ProviderCallError(self.provider_name, "payload_error", "unexpected Marketaux payload structure")
        return [item for item in items if isinstance(item, dict)]

    def _build_search_query(
        self,
        asset_symbol: str,
        asset_name: str,
        thesis_tags: list[str],
        market_question: str,
    ) -> str:
        tokens: list[str] = []
        if asset_symbol:
            tokens.append(asset_symbol)
        if asset_name:
            tokens.append(f"\"{asset_name}\"")
        tokens.extend(tag for tag in thesis_tags if tag and len(tag) > 2)
        if market_question:
            tokens.append(f"\"{market_question[:120]}\"")
        deduped: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            normalized = token.strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(normalized)
        return " ".join(deduped[:6])

    def _build_error(self, payload: Any, status_code: int) -> ProviderCallError:
        error = payload.get("error") if isinstance(payload, dict) else {}
        code = str(error.get("code") or "").strip().lower()
        message = str(error.get("message") or f"Marketaux returned HTTP {status_code}").strip()
        if status_code == 401:
            error_type = "authentication_error"
        elif status_code == 403:
            error_type = "authorization_error"
        elif status_code == 402 or code == "usage_limit_reached":
            error_type = "quota_exceeded"
        elif status_code == 429 or code == "rate_limit_reached":
            error_type = "rate_limited"
        elif status_code >= 500:
            error_type = "upstream_error"
        elif status_code == 400 or code == "malformed_parameters":
            error_type = "payload_error"
        else:
            error_type = "upstream_error"
        return ProviderCallError(self.provider_name, error_type, message, status_code=status_code)


class AlphaVantageNewsAdapter(BaseNewsAdapter):
    provider_name = "alphavantage"

    async def fetch_articles(
        self,
        *,
        asset_symbol: str,
        asset_name: str,
        thesis_tags: list[str],
        market_question: str,
    ) -> list[dict[str, Any]]:
        settings = self.context.settings
        if not settings.alphavantage_api_key:
            raise ProviderCallError(
                self.provider_name,
                "configuration_error",
                "ALPHAVANTAGE_API_KEY is not configured",
            )

        published_after = (datetime.now(UTC) - timedelta(hours=max(settings.news_lookback_hours, 1))).strftime(
            "%Y%m%dT%H%M"
        )
        params = {
            "function": "NEWS_SENTIMENT",
            "tickers": f"CRYPTO:{asset_symbol.upper()}",
            "time_from": published_after,
            "sort": "LATEST",
            "limit": max(settings.alphavantage_news_limit, 1),
            "apikey": settings.alphavantage_api_key,
        }
        topic = self._topic_from_signal(thesis_tags, market_question)
        if topic:
            params["topics"] = topic

        status_code, payload = await self._request_json(url=settings.alphavantage_base_url, params=params)
        if status_code >= 500:
            raise ProviderCallError(self.provider_name, "upstream_error", f"HTTP {status_code}", status_code=status_code)
        if status_code == 401:
            raise ProviderCallError(
                self.provider_name,
                "authentication_error",
                f"HTTP {status_code}",
                status_code=status_code,
            )
        if status_code == 403:
            raise ProviderCallError(
                self.provider_name,
                "authorization_error",
                f"HTTP {status_code}",
                status_code=status_code,
            )
        if status_code == 429:
            raise ProviderCallError(self.provider_name, "rate_limited", "HTTP 429", status_code=status_code)
        if status_code >= 400:
            raise ProviderCallError(self.provider_name, "payload_error", f"HTTP {status_code}", status_code=status_code)

        if isinstance(payload, dict):
            if "feed" in payload and isinstance(payload["feed"], list):
                return [item for item in payload["feed"] if isinstance(item, dict)]
            error_type, message = self._detect_payload_error(payload)
            if error_type:
                raise ProviderCallError(self.provider_name, error_type, message)

        raise ProviderCallError(self.provider_name, "payload_error", "unexpected Alpha Vantage payload structure")

    @staticmethod
    def _topic_from_signal(thesis_tags: list[str], market_question: str) -> str:
        tags = {str(tag).strip().lower() for tag in thesis_tags if str(tag).strip()}
        question = market_question.lower()
        if {"blockchain", "btc", "eth", "sol", "crypto", "xrp", "doge"} & tags:
            return "blockchain"
        if any(token in question for token in ("bitcoin", "ethereum", "solana", "crypto")):
            return "blockchain"
        return "financial_markets"

    @staticmethod
    def _detect_payload_error(payload: dict[str, Any]) -> tuple[str | None, str]:
        message = str(
            payload.get("Error Message")
            or payload.get("Note")
            or payload.get("Information")
            or payload.get("information")
            or ""
        ).strip()
        if not message:
            return None, ""
        lowered = message.lower()
        if "frequency" in lowered or "rate limit" in lowered or "per minute" in lowered:
            return "rate_limited", message
        if "premium" in lowered or "quota" in lowered or "limit" in lowered:
            return "quota_exceeded", message
        if "api key" in lowered or "claim your free api key" in lowered:
            return "authentication_error", message
        if "invalid api call" in lowered or "invalid" in lowered:
            return "payload_error", message
        return "upstream_error", message


class NewsProvider:
    def __init__(self, context: AppContext):
        self.context = context
        self.session: aiohttp.ClientSession | None = None
        self.adapters: dict[str, BaseNewsAdapter] = {
            "marketaux": MarketauxNewsAdapter(context, self),
            "alphavantage": AlphaVantageNewsAdapter(context, self),
        }

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def fetch_news(
        self,
        *,
        asset_symbol: str,
        asset_name: str,
        thesis_tags: list[str],
        market_question: str,
    ) -> NewsFetchResult:
        if self.context.settings.smoke_test_mode:
            return self._smoke_news(asset_symbol, asset_name)

        primary_name = self.context.settings.news_provider_primary
        fallback_name = self.context.settings.news_provider_fallback
        primary = self.adapters[primary_name]
        attempts: list[NewsProviderAttempt] = []

        primary_outcome = await self._attempt_provider(
            adapter=primary,
            role="primary",
            asset_symbol=asset_symbol,
            asset_name=asset_name,
            thesis_tags=thesis_tags,
            market_question=market_question,
        )
        attempts.append(primary_outcome.attempt)
        if primary_outcome.articles:
            return self._result(
                articles=primary_outcome.articles,
                provider_used=primary_name,
                fallback_used=False,
                attempts=attempts,
            )

        if not primary_outcome.should_fallback or fallback_name == primary_name:
            return self._result(
                articles=[],
                provider_used=primary_name,
                fallback_used=False,
                attempts=attempts,
                primary_error_type=primary_outcome.attempt.error_type,
                primary_error_message=primary_outcome.attempt.error_message,
            )

        fallback = self.adapters[fallback_name]
        fallback_outcome = await self._attempt_provider(
            adapter=fallback,
            role="fallback",
            asset_symbol=asset_symbol,
            asset_name=asset_name,
            thesis_tags=thesis_tags,
            market_question=market_question,
        )
        attempts.append(fallback_outcome.attempt)
        if fallback_outcome.articles:
            return self._result(
                articles=fallback_outcome.articles,
                provider_used=fallback_name,
                fallback_used=True,
                attempts=attempts,
                primary_error_type=primary_outcome.attempt.error_type,
                primary_error_message=primary_outcome.attempt.error_message,
            )

        return self._result(
            articles=[],
            provider_used=primary_name,
            fallback_used=False,
            attempts=attempts,
            primary_error_type=primary_outcome.attempt.error_type,
            primary_error_message=primary_outcome.attempt.error_message,
        )

    async def _client(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=max(self.context.settings.news_http_timeout_seconds, 1))
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session

    async def _attempt_provider(
        self,
        *,
        adapter: BaseNewsAdapter,
        role: Literal["primary", "fallback"],
        asset_symbol: str,
        asset_name: str,
        thesis_tags: list[str],
        market_question: str,
    ) -> AttemptOutcome:
        started = monotonic()
        try:
            raw_articles = await adapter.fetch_articles(
                asset_symbol=asset_symbol,
                asset_name=asset_name,
                thesis_tags=thesis_tags,
                market_question=market_question,
            )
            latency_ms = int((monotonic() - started) * 1000)
            articles = self._normalize_articles(adapter.provider_name, role, latency_ms, raw_articles)
            attempt = NewsProviderAttempt(
                provider=adapter.provider_name,
                role=role,
                succeeded=True,
                article_count=len(articles),
                latency_ms=latency_ms,
            )
            should_fallback = False
            if not articles and role == "primary" and self.context.settings.news_fallback_on_empty_result:
                should_fallback = True
            return AttemptOutcome(articles=articles, attempt=attempt, should_fallback=should_fallback)
        except ProviderCallError as exc:
            latency_ms = int((monotonic() - started) * 1000)
            attempt = NewsProviderAttempt(
                provider=adapter.provider_name,
                role=role,
                succeeded=False,
                article_count=0,
                latency_ms=latency_ms,
                error_type=exc.error_type,
                error_message=exc.message,
                status_code=exc.status_code,
            )
            return AttemptOutcome(
                articles=[],
                attempt=attempt,
                should_fallback=role == "primary" and self._should_fallback(exc.error_type),
            )

    def _normalize_articles(
        self,
        provider: str,
        role: Literal["primary", "fallback"],
        latency_ms: int,
        items: list[dict[str, Any]],
    ) -> list[NewsArticle]:
        normalized: list[NewsArticle] = []
        for item in items:
            if provider == "marketaux":
                article = BaseNewsAdapter._normalize_item(
                    item,
                    provider=provider,
                    provider_status=role,
                    provider_latency_ms=latency_ms,
                    published_at_keys=("published_at",),
                    summary_keys=("description", "summary", "snippet"),
                )
            else:
                article = BaseNewsAdapter._normalize_item(
                    item,
                    provider=provider,
                    provider_status=role,
                    provider_latency_ms=latency_ms,
                    published_at_keys=("time_published", "published_at"),
                )
            if article is not None:
                normalized.append(article)
        return normalized

    def _should_fallback(self, error_type: str) -> bool:
        settings = self.context.settings
        if error_type == "quota_exceeded":
            return settings.news_fallback_on_quota
        if error_type == "rate_limited":
            return settings.news_fallback_on_rate_limit
        if error_type in {"upstream_error", "connection_error", "timeout"}:
            return settings.news_fallback_on_upstream_error
        return False

    @staticmethod
    def _result(
        *,
        articles: list[NewsArticle],
        provider_used: str,
        fallback_used: bool,
        attempts: list[NewsProviderAttempt],
        primary_error_type: str | None = None,
        primary_error_message: str | None = None,
    ) -> NewsFetchResult:
        return NewsFetchResult(
            articles=articles,
            provider_used=provider_used,
            fallback_used=fallback_used,
            provider_attempts=attempts,
            primary_error_type=primary_error_type,
            primary_error_message=primary_error_message,
            article_count=len(articles),
        )

    @staticmethod
    def _smoke_news(asset_symbol: str, asset_name: str) -> NewsFetchResult:
        published_at = datetime.now(UTC).isoformat()
        return NewsFetchResult(
            articles=[
                NewsArticle(
                    title=f"{asset_name} gains momentum after strong market session",
                    summary=f"Traders cite improving sentiment around {asset_symbol}.",
                    source="smoke-wire",
                    url="",
                    published_at=published_at,
                    freshness_minutes=5,
                    provider="smoke",
                    provider_status="smoke",
                    provider_latency_ms=0,
                ),
                NewsArticle(
                    title=f"Analysts report sustained demand for {asset_symbol}",
                    summary=f"Multiple desks note constructive order flow for {asset_symbol}.",
                    source="smoke-desk",
                    url="",
                    published_at=published_at,
                    freshness_minutes=10,
                    provider="smoke",
                    provider_status="smoke",
                    provider_latency_ms=0,
                ),
            ],
            provider_used="smoke",
            fallback_used=False,
            provider_attempts=[],
            article_count=2,
        )
