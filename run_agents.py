import asyncio

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

    try:
        print("Iniciando agentes...")
        print(f"  Claude -> {claude.provider.model}")
        if news_validator is not None:
            print(f"  News   -> {news_validator.provider.model}")
        else:
            print("  News   -> disabled")
        print(f"  Codex  -> {codex.provider.model}")
        print(f"  Claw   -> {claw.provider.model}")
        tasks = [
            claude.run_loop(interval_seconds=10),
            codex.run_loop(interval_seconds=2),
            claw.run_loop(interval_seconds=2),
        ]
        if news_validator is not None:
            tasks.append(news_validator.run_loop(interval_seconds=3))
        await asyncio.gather(*tasks)
    finally:
        await claude.close()
        if news_validator is not None:
            await news_validator.close()
        await codex.close()
        await claw.close()
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
