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


def test_app_settings_copytrade_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "COPYTRADE_MARKETS",
        "COPYTRADE_SHARES",
        "COPYTRADE_MAX_BUY_COUNTS_PER_SIDE",
        "COPYTRADE_WAIT_FOR_NEXT_MARKET_START",
        "COPYTRADE_PRICE_BUFFER",
        "COPYTRADE_SECOND_LEG_BASE_PRICE",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.copytrade_markets == []
    assert settings.copytrade_enabled is False
    assert settings.copytrade_shares == 2
    assert settings.copytrade_price_buffer == pytest.approx(0.01)
    assert settings.copytrade_second_leg_base_price == pytest.approx(0.98)
    assert settings.copytrade_signal_confidence_threshold == pytest.approx(0.68)
    assert settings.copytrade_noise_threshold == pytest.approx(0.04)
    assert settings.copytrade_min_history_points == 10
    assert settings.copytrade_signal_cooldown_minutes == 45


def test_app_settings_momentum_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "MOMENTUM_ENABLED",
        "MOMENTUM_MARKETS",
        "MOMENTUM_MIN_EDGE",
        "MOMENTUM_MIN_VOLUME_24H",
        "MOMENTUM_SIGNAL_CONFIDENCE_THRESHOLD",
        "MOMENTUM_MIN_HISTORY_POINTS",
        "MOMENTUM_COOLDOWN_MINUTES",
        "MOMENTUM_MAX_POSITIONS",
        "MOMENTUM_WAIT_FOR_NEXT_MARKET_START",
        "MOMENTUM_ENTRY_NOTIONAL_USD",
        "MOMENTUM_TAKE_PROFIT_USD",
        "MOMENTUM_SIZING_MODE",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.momentum_enabled is False
    assert settings.momentum_markets == []
    assert settings.momentum_trading_enabled is False
    assert settings.momentum_min_edge == pytest.approx(0.068)
    assert settings.momentum_min_volume_24h == pytest.approx(500.0)
    assert settings.momentum_signal_confidence_threshold == pytest.approx(0.58)
    assert settings.momentum_min_history_points == 6
    assert settings.momentum_cooldown_minutes == 20
    assert settings.momentum_max_positions == 2
    assert settings.momentum_wait_for_next_market_start is False
    assert settings.momentum_entry_notional_usd == pytest.approx(1.0)
    assert settings.momentum_take_profit_usd == pytest.approx(1.0)
    assert settings.momentum_sizing_mode == "fixed_notional"


def test_app_settings_copytrade_env_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPYTRADE_MARKETS", "btc, eth ")
    monkeypatch.setenv("COPYTRADE_SHARES", "3")
    monkeypatch.setenv("COPYTRADE_MAX_BUY_COUNTS_PER_SIDE", "2")
    monkeypatch.setenv("COPYTRADE_WAIT_FOR_NEXT_MARKET_START", "true")
    monkeypatch.setenv("COPYTRADE_PRICE_BUFFER", "0.015")
    monkeypatch.setenv("COPYTRADE_SIGNAL_COOLDOWN_MINUTES", "45")

    settings = AppSettings(_env_file=None)

    assert settings.copytrade_markets == ["BTC", "ETH"]
    assert settings.copytrade_enabled is True
    assert settings.copytrade_shares == 3
    assert settings.copytrade_max_buy_counts_per_side == 2
    assert settings.copytrade_wait_for_next_market_start is True
    assert settings.copytrade_price_buffer == pytest.approx(0.015)
    assert settings.copytrade_signal_cooldown_minutes == 45


def test_app_settings_momentum_env_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOMENTUM_ENABLED", "true")
    monkeypatch.setenv("MOMENTUM_MARKETS", "btc, eth ")
    monkeypatch.setenv("MOMENTUM_MIN_EDGE", "0.09")
    monkeypatch.setenv("MOMENTUM_MIN_VOLUME_24H", "750")
    monkeypatch.setenv("MOMENTUM_SIGNAL_CONFIDENCE_THRESHOLD", "0.68")
    monkeypatch.setenv("MOMENTUM_MIN_HISTORY_POINTS", "8")
    monkeypatch.setenv("MOMENTUM_COOLDOWN_MINUTES", "30")
    monkeypatch.setenv("MOMENTUM_MAX_POSITIONS", "3")
    monkeypatch.setenv("MOMENTUM_WAIT_FOR_NEXT_MARKET_START", "true")
    monkeypatch.setenv("MOMENTUM_ENTRY_NOTIONAL_USD", "1.25")
    monkeypatch.setenv("MOMENTUM_TAKE_PROFIT_USD", "1.75")
    monkeypatch.setenv("MOMENTUM_SIZING_MODE", "kelly")

    settings = AppSettings(_env_file=None)

    assert settings.momentum_enabled is True
    assert settings.momentum_markets == ["BTC", "ETH"]
    assert settings.momentum_trading_enabled is True
    assert settings.momentum_min_edge == pytest.approx(0.09)
    assert settings.momentum_min_volume_24h == pytest.approx(750.0)
    assert settings.momentum_signal_confidence_threshold == pytest.approx(0.68)
    assert settings.momentum_min_history_points == 8
    assert settings.momentum_cooldown_minutes == 30
    assert settings.momentum_max_positions == 3
    assert settings.momentum_wait_for_next_market_start is True
    assert settings.momentum_entry_notional_usd == pytest.approx(1.25)
    assert settings.momentum_take_profit_usd == pytest.approx(1.75)
    assert settings.momentum_sizing_mode == "kelly"


def test_app_settings_live_bootstrap_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("POLYMARKET_LIVE_MIN_USDC_BALANCE", "POLYMARKET_SYNC_BALANCE_ALLOWANCE_ON_STARTUP"):
        monkeypatch.delenv(key, raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.polymarket_live_min_usdc_balance == pytest.approx(5.0)
    assert settings.polymarket_sync_balance_allowance_on_startup is False
