from __future__ import annotations

import pytest

from types import SimpleNamespace

from core.app_context import _apply_runtime_risk_overrides, _retry_async


@pytest.mark.asyncio
async def test_retry_async_retries_until_success() -> None:
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("not ready")
        return "ok"

    result = await _retry_async("dependency", 3, 0.0, flaky)

    assert result == "ok"
    assert attempts == 3


@pytest.mark.asyncio
async def test_retry_async_raises_after_last_attempt() -> None:
    attempts = 0

    async def always_fail() -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("still down")

    with pytest.raises(RuntimeError, match="dependency failed after 2 attempts"):
        await _retry_async("dependency", 2, 0.0, always_fail)

    assert attempts == 2


def test_apply_runtime_risk_overrides_syncs_env_controls() -> None:
    settings = SimpleNamespace(max_daily_spend_usd=25.0, max_single_position_usd=80.0)
    risk = SimpleNamespace(max_daily_spend_usd=5.0, max_single_position_usd=100.0)

    _apply_runtime_risk_overrides(settings, risk)

    assert risk.max_daily_spend_usd == pytest.approx(25.0)
    assert risk.max_single_position_usd == pytest.approx(80.0)


def test_apply_runtime_risk_overrides_preserves_yaml_when_env_is_disabled() -> None:
    settings = SimpleNamespace(max_daily_spend_usd=0.0, max_single_position_usd=0.0)
    risk = SimpleNamespace(max_daily_spend_usd=5.0, max_single_position_usd=100.0)

    _apply_runtime_risk_overrides(settings, risk)

    assert risk.max_daily_spend_usd == pytest.approx(5.0)
    assert risk.max_single_position_usd == pytest.approx(100.0)
