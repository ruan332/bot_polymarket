from pathlib import Path

import pytest
import yaml

from core.config import load_agents_config, load_risk_config, update_agent_model


def test_load_agents_config_reads_yaml() -> None:
    config = load_agents_config()
    assert "claude" in config.agents
    assert config.agents["codex"].model == "gpt-4o-mini"


def test_load_risk_config_reads_thresholds() -> None:
    risk = load_risk_config()
    assert risk.min_edge == pytest.approx(0.19)
    assert risk.max_kelly_fraction == pytest.approx(0.25)


def test_update_agent_model_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "claude": {
                        "model": "claude-sonnet-4-6",
                        "provider": "anthropic",
                        "temperature": 0.1,
                        "max_tokens": 1000,
                        "fallback_model": "claude-haiku-4-5",
                        "daily_cost_limit_usd": 0.5,
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    updated = update_agent_model("claude", "claude-haiku-4-5", path=path)
    assert updated.agents["claude"].model == "claude-haiku-4-5"
    reloaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert reloaded["agents"]["claude"]["model"] == "claude-haiku-4-5"
