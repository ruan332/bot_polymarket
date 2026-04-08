from __future__ import annotations

import asyncio
from pathlib import Path

from clima_bot.config.remote_env import bootstrap_env_file
from clima_bot.config.settings import DEFAULT_ENV_PATH
from clima_bot.runtime.engine import ClimaBotEngine


async def _run() -> None:
    if not DEFAULT_ENV_PATH.exists():
        bootstrap_env_file(destination=DEFAULT_ENV_PATH, fallback_env_path=Path(".env"))

    engine = ClimaBotEngine()
    try:
        try:
            from clima_bot.ui.app import ClimaBotApp

            app = ClimaBotApp(engine)
            await app.run_async()
        except Exception:
            from clima_bot.ui.fallback import ClimaBotFallbackUI

            print("Textual nao encontrado. Abrindo CLIMA BOT em modo terminal simples.")
            fallback = ClimaBotFallbackUI(engine)
            await fallback.run()
    finally:
        await engine.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
