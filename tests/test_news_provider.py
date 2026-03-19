from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.news_provider import NewsProvider, ProviderCallError


def make_context():
    return SimpleNamespace(
        settings=SimpleNamespace(
            smoke_test_mode=False,
            news_provider_primary="marketaux",
            news_provider_fallback="alphavantage",
            news_lookback_hours=24,
            news_http_timeout_seconds=15,
            news_fallback_on_quota=True,
            news_fallback_on_rate_limit=True,
            news_fallback_on_upstream_error=True,
            news_fallback_on_empty_result=False,
            marketaux_api_key="marketaux-key",
            marketaux_base_url="https://api.marketaux.com/v1/news/all",
            marketaux_language="en",
            marketaux_limit_per_request=3,
            alphavantage_api_key="alpha-key",
            alphavantage_base_url="https://www.alphavantage.co/query",
            alphavantage_news_limit=50,
        )
    )


@pytest.mark.asyncio
async def test_marketaux_success_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = NewsProvider(make_context())

    async def primary_fetch(**kwargs):
        return [
            {
                "title": "Bitcoin rises on fresh momentum",
                "description": "Buyers returned to BTC after a strong session.",
                "source": "marketaux.com",
                "url": "https://example.com/btc",
                "published_at": "2026-03-19T12:00:00Z",
            }
        ]

    monkeypatch.setattr(provider.adapters["marketaux"], "fetch_articles", primary_fetch)

    result = await provider.fetch_news(
        asset_symbol="BTC",
        asset_name="Bitcoin",
        thesis_tags=["btc", "direction"],
        market_question="Will BTC stay above 100k?",
    )

    assert result.provider_used == "marketaux"
    assert result.fallback_used is False
    assert result.article_count == 1
    assert result.articles[0].provider == "marketaux"
    assert result.articles[0].provider_status == "primary"


@pytest.mark.asyncio
async def test_marketaux_rate_limit_falls_back_to_alphavantage(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = NewsProvider(make_context())

    async def primary_fetch(**kwargs):
        raise ProviderCallError("marketaux", "rate_limited", "429 from Marketaux")

    async def fallback_fetch(**kwargs):
        return [
            {
                "title": "Bitcoin holds support",
                "summary": "Alpha Vantage returned a fallback article.",
                "source": "alphavantage",
                "url": "https://example.com/fallback",
                "time_published": "20260319T120000",
            }
        ]

    monkeypatch.setattr(provider.adapters["marketaux"], "fetch_articles", primary_fetch)
    monkeypatch.setattr(provider.adapters["alphavantage"], "fetch_articles", fallback_fetch)

    result = await provider.fetch_news(
        asset_symbol="BTC",
        asset_name="Bitcoin",
        thesis_tags=["btc", "direction"],
        market_question="Will BTC stay above 100k?",
    )

    assert result.provider_used == "alphavantage"
    assert result.fallback_used is True
    assert result.primary_error_type == "rate_limited"
    assert len(result.provider_attempts) == 2
    assert result.provider_attempts[0].provider == "marketaux"
    assert result.provider_attempts[1].provider == "alphavantage"
    assert result.articles[0].provider_status == "fallback"


@pytest.mark.asyncio
async def test_marketaux_upstream_error_falls_back_to_alphavantage(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = NewsProvider(make_context())

    async def primary_fetch(**kwargs):
        raise ProviderCallError("marketaux", "upstream_error", "503 maintenance")

    async def fallback_fetch(**kwargs):
        return [
            {
                "title": "Bitcoin news from fallback",
                "summary": "Fallback provider supplied a valid article.",
                "source": "alphavantage",
                "url": "https://example.com/upstream",
                "time_published": "20260319T120000",
            }
        ]

    monkeypatch.setattr(provider.adapters["marketaux"], "fetch_articles", primary_fetch)
    monkeypatch.setattr(provider.adapters["alphavantage"], "fetch_articles", fallback_fetch)

    result = await provider.fetch_news(
        asset_symbol="BTC",
        asset_name="Bitcoin",
        thesis_tags=["btc"],
        market_question="Will BTC stay above 100k?",
    )

    assert result.provider_used == "alphavantage"
    assert result.fallback_used is True
    assert result.primary_error_type == "upstream_error"
    assert result.article_count == 1


@pytest.mark.asyncio
async def test_marketaux_empty_result_does_not_trigger_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = NewsProvider(make_context())
    called = {"fallback": 0}

    async def primary_fetch(**kwargs):
        return []

    async def fallback_fetch(**kwargs):
        called["fallback"] += 1
        return []

    monkeypatch.setattr(provider.adapters["marketaux"], "fetch_articles", primary_fetch)
    monkeypatch.setattr(provider.adapters["alphavantage"], "fetch_articles", fallback_fetch)

    result = await provider.fetch_news(
        asset_symbol="BTC",
        asset_name="Bitcoin",
        thesis_tags=["btc"],
        market_question="Will BTC stay above 100k?",
    )

    assert result.provider_used == "marketaux"
    assert result.fallback_used is False
    assert result.article_count == 0
    assert called["fallback"] == 0


@pytest.mark.asyncio
async def test_marketaux_auth_error_does_not_trigger_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = NewsProvider(make_context())
    called = {"fallback": 0}

    async def primary_fetch(**kwargs):
        raise ProviderCallError("marketaux", "authentication_error", "invalid token")

    async def fallback_fetch(**kwargs):
        called["fallback"] += 1
        return []

    monkeypatch.setattr(provider.adapters["marketaux"], "fetch_articles", primary_fetch)
    monkeypatch.setattr(provider.adapters["alphavantage"], "fetch_articles", fallback_fetch)

    result = await provider.fetch_news(
        asset_symbol="BTC",
        asset_name="Bitcoin",
        thesis_tags=["btc"],
        market_question="Will BTC stay above 100k?",
    )

    assert result.provider_used == "marketaux"
    assert result.fallback_used is False
    assert result.primary_error_type == "authentication_error"
    assert called["fallback"] == 0
