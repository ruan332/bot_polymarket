from pathlib import Path

from clima_bot.storage.repository import ClimaBotRepository


def test_repository_tracks_wallet_lifecycle(tmp_path: Path) -> None:
    repo = ClimaBotRepository(tmp_path / "clima.sqlite3")
    repo.init_schema()
    try:
        repo.upsert_wallet(
            {
                "proxy_wallet": "0xabc",
                "user_name": "alpha",
                "score": 91,
                "approved": True,
                "active": True,
                "paused": False,
                "profile": {"proxy_wallet": "0xabc"},
                "metrics": {"pnl_30d": 10},
                "selection": {"score": 91},
            }
        )
        wallet = repo.get_wallet("0xabc")
        assert wallet is not None
        assert wallet["active"] is True

        repo.set_wallet_paused("0xabc", True)
        paused = repo.get_wallet("0xabc")
        assert paused is not None
        assert paused["paused"] is True
        assert paused["active"] is False

        repo.remove_wallet("0xabc")
        assert repo.get_wallet("0xabc") is None
    finally:
        repo.close()
