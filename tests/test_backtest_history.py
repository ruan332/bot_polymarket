from __future__ import annotations

from pathlib import Path

from scripts.backtest_history import _prepare_output_path


def test_prepare_output_path_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "reports" / "backtest.json"

    prepared = _prepare_output_path(target)

    assert prepared == target
    assert target.parent.exists()
