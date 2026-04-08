from pathlib import Path

from clima_bot.config.remote_env import bootstrap_env_file, parse_env_text, update_env_file


def test_bootstrap_env_file_preserves_existing_file(tmp_path: Path) -> None:
    target = tmp_path / ".env.clima_bot"
    target.write_text("ENVIRONMENT=development\n", encoding="utf-8")

    result = bootstrap_env_file(destination=target, remote_text="ENVIRONMENT=production\n", overwrite=False)

    assert result.created is False
    assert target.read_text(encoding="utf-8") == "ENVIRONMENT=development\n"


def test_bootstrap_env_file_renders_relevant_payload(tmp_path: Path) -> None:
    target = tmp_path / ".env.clima_bot"

    result = bootstrap_env_file(
        destination=target,
        remote_text="\n".join(
            [
                "ENVIRONMENT=production",
                "LIVE_TRADING=true",
                "PAPER_BANKROLL_USD=250.00",
                "POLYMARKET_PRIVATE_KEY=abc",
            ]
        ),
        overwrite=True,
    )

    payload = parse_env_text(target.read_text(encoding="utf-8"))
    assert result.created is True
    assert payload["ENVIRONMENT"] == "production"
    assert payload["LIVE_TRADING"] == "true"
    assert payload["POLYMARKET_PRIVATE_KEY"] == "abc"
    assert "CLIMA_BOT_STORAGE_PATH" in payload


def test_update_env_file_merges_values(tmp_path: Path) -> None:
    target = tmp_path / ".env.clima_bot"
    target.write_text("ENVIRONMENT=development\nCLIMA_BOT_MAX_WALLETS=10\n", encoding="utf-8")

    update_env_file(target, {"CLIMA_BOT_MAX_WALLETS": "8", "CLIMA_BOT_MIN_NOTIONAL_USD": "2.0"})

    payload = parse_env_text(target.read_text(encoding="utf-8"))
    assert payload["CLIMA_BOT_MAX_WALLETS"] == "8"
    assert payload["CLIMA_BOT_MIN_NOTIONAL_USD"] == "2.0"
