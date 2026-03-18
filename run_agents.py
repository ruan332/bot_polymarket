import asyncio

from agents.claude_agent import ClaudeAgent
from agents.claw_agent import ClawAgent
from agents.codex_agent import CodexAgent
from core.app_context import AppContext


async def main() -> None:
    context = await AppContext.create()
    claude = ClaudeAgent(context)
    codex = CodexAgent(context)
    claw = ClawAgent(context)

    try:
        print("Iniciando agentes...")
        print(f"  Claude -> {claude.provider.model}")
        print(f"  Codex  -> {codex.provider.model}")
        print(f"  Claw   -> {claw.provider.model}")
        await asyncio.gather(
            claude.run_loop(interval_seconds=10),
            codex.run_loop(interval_seconds=2),
            claw.run_loop(interval_seconds=2),
        )
    finally:
        await claude.close()
        await codex.close()
        await claw.close()
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
