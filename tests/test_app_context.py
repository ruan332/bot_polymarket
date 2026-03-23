from __future__ import annotations

import pytest

from core.app_context import _retry_async


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
