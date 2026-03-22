from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from core.config import AppSettings, infer_provider_from_model, load_agents_config, load_crypto_config, load_risk_config, update_agent_model
from core.crypto import classify_crypto_market


def test_load_agents_config_reads_yaml() -> None:
    config = load_agents_config()
    assert "claude" in config.agents
    assert config.agents["codex"].model == "gpt-4o-mini"


def test_load_risk_config_reads_thresholds() -> None:
    risk = load_risk_config()
    assert risk.min_edge == pytest.approx(0.12)
    assert risk.max_kelly_fraction == pytest.approx(0.25)


def test_load_crypto_config_reads_tiers() -> None:
    crypto = load_crypto_config()
    assert crypto.enabled is True
    assert crypto.direct_coin_only is False
    assert crypto.market_kind_priority == ["direct_coin", "indirect_crypto"]
    assert "BTC" not in crypto.major_assets
    assert crypto.btc.min_edge <= crypto.small_cap.min_edge


def test_classify_crypto_market_accepts_direct_coin_markets() -> None:
    crypto = load_crypto_config()
    candidate = classify_crypto_market("Will BTC be above 100k?", "test market", crypto)
    assert candidate is not None
    assert candidate.asset_symbol == "BTC"
    assert candidate.crypto_tier == "btc"
    assert candidate.market_kind == "direct_coin"
    assert candidate.thesis_hash


def test_classify_crypto_market_accepts_indirect_crypto_markets() -> None:
    crypto = load_crypto_config()
    candidate = classify_crypto_market("Will a BTC ETF be approved?", "regulation market", crypto)
    assert candidate is not None
    assert candidate.asset_symbol == "BTC"
    assert candidate.market_kind == "indirect_crypto"


def test_classify_crypto_market_uses_synthetic_asset_for_broad_crypto_markets() -> None:
    crypto = load_crypto_config()
    candidate = classify_crypto_market("Will crypto regulation tighten this quarter?", "digital assets policy market", crypto)
    assert candidate is not None
    assert candidate.asset_symbol == "CRYPTO"
    assert candidate.market_kind == "indirect_crypto"


def test_classify_crypto_market_rejects_thematic_long_horizon_markets() -> None:
    crypto = load_crypto_config()
    candidate = classify_crypto_market("Will bitcoin hit $1m before GTA VI?", "long horizon theme market", crypto)
    assert candidate is None


def test_classify_crypto_market_rejects_weak_incidental_crypto_mentions() -> None:
    crypto = load_crypto_config()
    candidate = classify_crypto_market("Will Tesla stock rise if crypto stays volatile?", "equity market", crypto)
    assert candidate is None


def test_update_agent_model_is_atomic() -> None:
    temp_dir = Path(".tmp") / f"agent-config-{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = temp_dir / "agents.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "claude": {
                        "model": "claude-sonnet-4-20250514",
                        "provider": "anthropic",
                        "temperature": 0.1,
                        "max_tokens": 1000,
                        "fallback_model": "claude-3-5-haiku-20241022",
                        "daily_cost_limit_usd": 0.5,
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    updated = update_agent_model("claude", "openai/gpt-4o-mini", path=path)
    assert updated.agents["claude"].model == "gpt-4o-mini"
    assert updated.agents["claude"].provider == "openai"
    assert updated.agents["claude"].fallback_model == "gpt-4o-mini"
    reloaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert reloaded["agents"]["claude"]["model"] == "gpt-4o-mini"
    assert reloaded["agents"]["claude"]["provider"] == "openai"
    path.unlink(missing_ok=True)
    temp_dir.rmdir()


def test_infer_provider_from_model_handles_prefixed_and_plain_models() -> None:
    assert infer_provider_from_model("openai/gpt-4o-mini") == ("openai", "gpt-4o-mini")
    assert infer_provider_from_model("claude-sonnet-4-20250514") == ("anthropic", "claude-sonnet-4-20250514")


def test_app_settings_news_provider_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "NEWS_PROVIDER_PRIMARY",
        "NEWS_PROVIDER_FALLBACK",
        "NEWS_LOOKBACK_HOURS",
        "NEWS_HTTP_TIMEOUT_SECONDS",
        "NEWS_VALIDATION_ENABLED",
        "MARKETAUX_API_KEY",
        "ALPHAVANTAGE_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.news_provider_primary == "marketaux"
    assert settings.news_provider_fallback == "alphavantage"
    assert settings.news_lookback_hours == 24
    assert settings.news_validation_enabled is True
    assert settings.news_fallback_on_empty_result is False


def test_app_settings_news_provider_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEWS_PROVIDER_PRIMARY", "alphavantage")
    monkeypatch.setenv("NEWS_PROVIDER_FALLBACK", "marketaux")
    monkeypatch.setenv("NEWS_VALIDATION_ENABLED", "false")
    monkeypatch.setenv("MARKETAUX_API_KEY", "marketaux-key")
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "alpha-key")
    monkeypatch.setenv("NEWS_LOOKBACK_HOURS", "12")

    settings = AppSettings(_env_file=None)

    assert settings.news_provider_primary == "alphavantage"
    assert settings.news_provider_fallback == "marketaux"
    assert settings.news_validation_enabled is False
    assert settings.marketaux_api_key == "marketaux-key"
    assert settings.alphavantage_api_key == "alpha-key"
    assert settings.news_lookback_hours == 12
