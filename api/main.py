from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from core.app_context import AppContext
from core.config import update_agent_model
from core.cost_tracker import CostTracker
from core.schemas import ModelSwapRequest


@asynccontextmanager
async def lifespan(_: FastAPI):
    context = await AppContext.create()
    app.state.context = context
    try:
        yield
    finally:
        await context.close()


app = FastAPI(title="Polymarket Multi-Agent API", lifespan=lifespan)


def get_context() -> AppContext:
    return app.state.context


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/agents/swap-model")
async def swap_model(req: ModelSwapRequest) -> dict[str, str | int]:
    context = get_context()
    try:
        updated = update_agent_model(req.agent, req.model, provider=req.provider, fallback_model=req.fallback_model)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await context.reload_configs()
    version = await context.bus.set_agent_runtime_override(req.agent, updated.agents[req.agent])
    await context.bus.publish_event(
        "events:control",
        {
            "event_type": "agent.reload_requested",
            "version": "v1",
            "agent": req.agent,
            "model": updated.agents[req.agent].model,
            "provider": updated.agents[req.agent].provider,
        },
    )
    return {
        "status": "ok",
        "agent": req.agent,
        "new_model": updated.agents[req.agent].model,
        "provider": updated.agents[req.agent].provider,
        "config_version": version,
    }


@app.get("/agents/status")
async def agents_status() -> dict[str, object]:
    context = get_context()
    rows = await context.repository.get_agent_status()
    models = {agent: cfg.model for agent, cfg in context.agents_config.agents.items()}
    status = {
        row["agent"]: {
            "model": row["model"],
            "running": row["running"],
            "config_version": row["config_version"],
            "last_seen": row["last_seen"],
            "meta": row["meta"],
        }
        for row in rows
    }
    for agent_name, model in models.items():
        status.setdefault(
            agent_name,
            {"model": model, "running": False, "config_version": 0, "last_seen": None, "meta": {}},
        )
    return status


@app.get("/costs/daily")
async def daily_costs() -> list[dict[str, object]]:
    context = get_context()
    return [await CostTracker(agent_name, context).get_daily_summary() for agent_name in context.agents_config.agents]


@app.get("/signals/recent")
async def recent_signals(limit: int = 20) -> list[dict[str, object]]:
    return await get_context().repository.get_recent_signals(limit=limit)


@app.get("/orders/recent")
async def recent_orders(limit: int = 20) -> list[dict[str, object]]:
    return await get_context().repository.get_recent_orders(limit=limit)


@app.get("/portfolio/summary")
async def portfolio_summary() -> dict[str, object]:
    return (await get_context().repository.get_portfolio_summary()).model_dump()


@app.get("/portfolio/positions")
async def portfolio_positions() -> list[dict[str, object]]:
    return await get_context().repository.get_open_positions()


@app.get("/portfolio/equity-history")
async def portfolio_equity_history(limit: int = 100) -> list[dict[str, object]]:
    return await get_context().repository.get_equity_history(limit=limit)


@app.get("/metrics/overview")
async def metrics_overview() -> dict[str, object]:
    return await get_context().repository.metrics_overview()


@app.get("/risk-events/recent")
async def recent_risk_events(limit: int = 20) -> list[dict[str, object]]:
    return await get_context().repository.get_recent_risk_events(limit=limit)


@app.get("/decisions/recent")
async def recent_decisions(limit: int = 20) -> list[dict[str, object]]:
    return await get_context().repository.get_recent_decisions(limit=limit)
