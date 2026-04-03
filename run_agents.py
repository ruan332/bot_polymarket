import asyncio
from contextlib import suppress

from agents.claude_agent import ClaudeAgent
from agents.claw_agent import ClawAgent
from agents.codex_agent import CodexAgent
from agents.news_validator_agent import NewsValidatorAgent
from core.app_context import AppContext


async def main() -> None:
    context = await AppContext.create()
    claude = ClaudeAgent(context)
    codex = CodexAgent(context)
    claw = ClawAgent(context)
    news_validator = NewsValidatorAgent(context) if context.settings.news_validation_enabled else None
    agents = [claude, codex, claw]
    if news_validator is not None:
        agents.append(news_validator)

    tasks: list[asyncio.Task[None]] = []
    try:
        print("Iniciando agentes...")
        print(f"  Claude -> {claude.provider.model}")
        if news_validator is not None:
            print(f"  News   -> {news_validator.provider.model}")
        else:
            print("  News   -> disabled")
        print(f"  Codex  -> {codex.provider.model}")
        print(f"  Claw   -> {claw.provider.model}")
        claude_interval = 1 if context.settings.copytrade_enabled else 10
        tasks = [
            asyncio.create_task(claude.run_loop(interval_seconds=claude_interval), name="claude.run_loop"),
            asyncio.create_task(codex.run_loop(interval_seconds=2), name="codex.run_loop"),
            asyncio.create_task(claw.run_loop(interval_seconds=2), name="claw.run_loop"),
        ]
        if news_validator is not None:
            tasks.append(asyncio.create_task(news_validator.run_loop(interval_seconds=3), name="news_validator.run_loop"))
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        for agent in agents:
            with suppress(Exception, asyncio.CancelledError):
                await asyncio.shield(agent.close())

        with suppress(Exception, asyncio.CancelledError):
            await asyncio.shield(context.close())


if __name__ == "__main__":
    asyncio.run(main())
