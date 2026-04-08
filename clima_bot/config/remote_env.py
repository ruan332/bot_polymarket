from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .settings import DEFAULT_ENV_PATH


RELEVANT_KEYS = [
    "ENVIRONMENT",
    "LIVE_TRADING",
    "SMOKE_TEST_MODE",
    "PAPER_BANKROLL_USD",
    "POLYMARKET_PRIVATE_KEY",
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_API_PASSPHRASE",
    "POLYMARKET_FUNDER",
    "POLYMARKET_SIGNATURE_TYPE",
    "POLYMARKET_CHAIN_ID",
]


@dataclass(slots=True)
class BootstrapResult:
    path: Path
    source: str
    created: bool
    overwritten: bool


def parse_env_text(text: str) -> dict[str, str]:
    payload: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key.strip()] = value.strip()
    return payload


def render_env_text(payload: dict[str, str]) -> str:
    ordered = [
        ("ENVIRONMENT", payload.get("ENVIRONMENT", "development")),
        ("LIVE_TRADING", payload.get("LIVE_TRADING", "false")),
        ("SMOKE_TEST_MODE", payload.get("SMOKE_TEST_MODE", "false")),
        ("PAPER_BANKROLL_USD", payload.get("PAPER_BANKROLL_USD", "1000.00")),
        ("POLYMARKET_PRIVATE_KEY", payload.get("POLYMARKET_PRIVATE_KEY", "")),
        ("POLYMARKET_API_KEY", payload.get("POLYMARKET_API_KEY", "")),
        ("POLYMARKET_API_SECRET", payload.get("POLYMARKET_API_SECRET", "")),
        ("POLYMARKET_API_PASSPHRASE", payload.get("POLYMARKET_API_PASSPHRASE", "")),
        ("POLYMARKET_FUNDER", payload.get("POLYMARKET_FUNDER", "")),
        ("POLYMARKET_SIGNATURE_TYPE", payload.get("POLYMARKET_SIGNATURE_TYPE", "0")),
        ("POLYMARKET_CHAIN_ID", payload.get("POLYMARKET_CHAIN_ID", "137")),
        ("CLIMA_BOT_REMOTE_ENV_HOST", payload.get("CLIMA_BOT_REMOTE_ENV_HOST", "bot-polymarket-vps")),
        ("CLIMA_BOT_REMOTE_ENV_PATH", payload.get("CLIMA_BOT_REMOTE_ENV_PATH", "/opt/polymarket-bot/.env")),
        ("CLIMA_BOT_CATEGORY", payload.get("CLIMA_BOT_CATEGORY", "WEATHER")),
        ("CLIMA_BOT_COPY_WALLETS", payload.get("CLIMA_BOT_COPY_WALLETS", "")),
        ("CLIMA_BOT_MAX_WALLETS", payload.get("CLIMA_BOT_MAX_WALLETS", "10")),
        ("CLIMA_BOT_COPY_TRADE_FRACTION", payload.get("CLIMA_BOT_COPY_TRADE_FRACTION", "0.08")),
        ("CLIMA_BOT_MIN_NOTIONAL_USD", payload.get("CLIMA_BOT_MIN_NOTIONAL_USD", "1.0")),
        ("CLIMA_BOT_MAX_NOTIONAL_USD", payload.get("CLIMA_BOT_MAX_NOTIONAL_USD", "2.5")),
        ("CLIMA_BOT_POLL_INTERVAL_SECONDS", payload.get("CLIMA_BOT_POLL_INTERVAL_SECONDS", "20")),
        ("CLIMA_BOT_STORAGE_PATH", payload.get("CLIMA_BOT_STORAGE_PATH", "clima_bot/data/clima_bot.sqlite3")),
        ("CLIMA_BOT_AUTO_SYNC", payload.get("CLIMA_BOT_AUTO_SYNC", "true")),
    ]
    return "\n".join(f"{key}={value}" for key, value in ordered) + "\n"


def fetch_remote_env_text(host: str, remote_path: str, *, timeout_seconds: int = 20) -> str:
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={max(timeout_seconds, 1)}",
        host,
        f"cat {shlex.quote(remote_path)}",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds + 5, check=False)
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip() or "remote env fetch failed"
        raise RuntimeError(error)
    return result.stdout


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    existing = parse_env_text(path.read_text(encoding="utf-8")) if path.exists() else {}
    existing.update({key: value for key, value in updates.items() if value is not None})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_env_text(existing), encoding="utf-8")


def bootstrap_env_file(
    *,
    destination: Path | None = None,
    overwrite: bool = False,
    remote_host: str | None = None,
    remote_path: str | None = None,
    remote_text: str | None = None,
    fallback_text: str | None = None,
    fallback_env_path: Path | None = None,
) -> BootstrapResult:
    target = destination or DEFAULT_ENV_PATH
    if target.exists() and not overwrite:
        return BootstrapResult(path=target, source="existing", created=False, overwritten=False)

    source_name = "fallback"
    env_text = remote_text
    if env_text is None and remote_host and remote_path:
        env_text = fetch_remote_env_text(remote_host, remote_path)
        source_name = "remote"
    if env_text is None and fallback_text is not None:
        env_text = fallback_text
    if env_text is None and fallback_env_path and fallback_env_path.exists():
        env_text = fallback_env_path.read_text(encoding="utf-8")
    if env_text is None:
        env_text = ""

    base_payload = parse_env_text(env_text)
    payload = {key: base_payload.get(key, "") for key in RELEVANT_KEYS}
    payload["CLIMA_BOT_REMOTE_ENV_HOST"] = remote_host or payload.get("CLIMA_BOT_REMOTE_ENV_HOST", "bot-polymarket-vps")
    payload["CLIMA_BOT_REMOTE_ENV_PATH"] = remote_path or payload.get("CLIMA_BOT_REMOTE_ENV_PATH", "/opt/polymarket-bot/.env")
    payload.setdefault("ENVIRONMENT", os.getenv("ENVIRONMENT", "development"))
    payload.setdefault("LIVE_TRADING", os.getenv("LIVE_TRADING", "false"))
    payload.setdefault("SMOKE_TEST_MODE", os.getenv("SMOKE_TEST_MODE", "false"))
    payload.setdefault("PAPER_BANKROLL_USD", os.getenv("PAPER_BANKROLL_USD", "1000.00"))
    payload["CLIMA_BOT_STORAGE_PATH"] = str(target.parent / "data" / "clima_bot.sqlite3")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_env_text(payload), encoding="utf-8")
    return BootstrapResult(path=target, source=source_name, created=True, overwritten=overwrite)
