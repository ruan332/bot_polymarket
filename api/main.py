from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException

from core.app_context import AppContext
from core.config import get_enabled_agent_names, update_agent_model
from core.cost_tracker import CostTracker
from core.discovery_service import DiscoveryService
from core.market_connector import MarketConnector
from core.schemas import ModelSwapRequest
from core.settlement import SettlementService


@asynccontextmanager
async def lifespan(_: FastAPI):
    context = await AppContext.create()
    app.state.context = context
    try:
        yield
    finally:
        await context.close()


app = FastAPI(title="Polymarket Multi-Agent API", lifespan=lifespan)
logger = logging.getLogger(__name__)


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
    enabled_agents = get_enabled_agent_names(context.settings, context.agents_config)
    models = {agent: context.agents_config.agents[agent].model for agent in enabled_agents}
    status = {
        row["agent"]: {
            "model": row["model"],
            "running": row["running"],
            "config_version": row["config_version"],
            "last_seen": row["last_seen"],
            "meta": row["meta"],
        }
        for row in rows
        if row["agent"] in models
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
    return [
        await CostTracker(agent_name, context).get_daily_summary()
        for agent_name in get_enabled_agent_names(context.settings, context.agents_config)
    ]


@app.get("/signals/recent")
async def recent_signals(
    limit: int = 20,
    asset: str | None = None,
    tier: str | None = None,
    strategy: str | None = None,
    cutoff_name: str | None = None,
) -> list[dict[str, object]]:
    return await get_context().repository.get_recent_signals(
        limit=limit,
        asset=asset,
        tier=tier,
        strategy=strategy,
        cutoff_name=cutoff_name,
    )


@app.get("/orders/recent")
async def recent_orders(
    limit: int = 20,
    asset: str | None = None,
    tier: str | None = None,
    strategy: str | None = None,
    cutoff_name: str | None = None,
) -> list[dict[str, object]]:
    return await get_context().repository.get_recent_orders(
        limit=limit,
        asset=asset,
        tier=tier,
        strategy=strategy,
        cutoff_name=cutoff_name,
    )


@app.get("/portfolio/summary")
async def portfolio_summary() -> dict[str, object]:
    context = get_context()
    summary = (await context.repository.get_portfolio_summary()).model_dump()

    connector = getattr(context, "market_connector", None)
    close_after = False
    if connector is None:
        connector = MarketConnector(context)
        close_after = True

    try:
        snapshot = await connector.get_collateral_snapshot(sync_allowance=False)
        if snapshot and snapshot.get("balance") is not None:
            summary["available_balance"] = float(snapshot["balance"])
            summary["live_balance"] = float(snapshot["balance"])
            summary["live_allowance"] = float(snapshot["allowance"]) if snapshot.get("allowance") is not None else None
            summary["balance_source"] = "polymarket_live"
            if not summary.get("funder"):
                summary["funder"] = str(snapshot.get("funder") or "")
        else:
            logger.info("portfolio_summary_live_snapshot_unavailable")
    except Exception as exc:
        logger.warning("portfolio_summary_live_snapshot_failed: %s", exc)
    finally:
        if close_after:
            await connector.close()

    return summary


@app.get("/portfolio/positions")
async def portfolio_positions() -> list[dict[str, object]]:
    return await get_context().repository.get_open_positions()


@app.get("/portfolio/equity-history")
async def portfolio_equity_history(limit: int = 100) -> list[dict[str, object]]:
    return await get_context().repository.get_equity_history(limit=limit)


@app.get("/metrics/overview")
async def metrics_overview(cutoff_name: str | None = None) -> dict[str, object]:
    return await get_context().repository.metrics_overview_since(cutoff_name=cutoff_name)


@app.get("/metrics/performance")
async def performance_report(
    hours: int = 24,
    asset: str | None = None,
    tier: str | None = None,
    strategy: str | None = None,
    cutoff_name: str | None = None,
) -> dict[str, object]:
    return await get_context().repository.get_performance_report(
        hours=hours,
        asset=asset,
        tier=tier,
        strategy=strategy,
        cutoff_name=cutoff_name,
    )


@app.get("/metrics/risk-breakdown")
async def risk_breakdown_report(
    hours: int = 24,
    asset: str | None = None,
    tier: str | None = None,
    strategy: str | None = None,
    cutoff_name: str | None = None,
) -> dict[str, object]:
    return await get_context().repository.get_risk_breakdown_report(
        hours=hours,
        asset=asset,
        tier=tier,
        strategy=strategy,
        cutoff_name=cutoff_name,
    )


@app.get("/risk-events/recent")
async def recent_risk_events(
    limit: int = 20,
    asset: str | None = None,
    tier: str | None = None,
    strategy: str | None = None,
    cutoff_name: str | None = None,
) -> list[dict[str, object]]:
    return await get_context().repository.get_recent_risk_events(
        limit=limit,
        asset=asset,
        tier=tier,
        strategy=strategy,
        cutoff_name=cutoff_name,
    )


@app.get("/metrics/pipeline/recent")
async def recent_pipeline_telemetry(limit: int = 30, cutoff_name: str | None = None) -> list[dict[str, object]]:
    return await get_context().repository.get_recent_pipeline_telemetry(limit=limit, cutoff_name=cutoff_name)


@app.get("/decisions/recent")
async def recent_decisions(
    limit: int = 20,
    asset: str | None = None,
    tier: str | None = None,
    strategy: str | None = None,
    cutoff_name: str | None = None,
) -> list[dict[str, object]]:
    return await get_context().repository.get_recent_decisions(
        limit=limit,
        asset=asset,
        tier=tier,
        strategy=strategy,
        cutoff_name=cutoff_name,
    )


@app.get("/live/bootstrap-status")
async def live_bootstrap_status(refresh: bool = False, sync_allowance: bool | None = None) -> dict[str, object]:
    context = get_context()
    should_sync_allowance = (
        context.settings.polymarket_sync_balance_allowance_on_startup
        if sync_allowance is None
        else sync_allowance
    )
    if refresh or not context.live_bootstrap_status:
        context.live_bootstrap_status = await context.refresh_live_bootstrap_status(
            sync_allowance=should_sync_allowance,
        )
    return context.live_bootstrap_status


@app.get("/analysis/cutoffs")
async def analysis_cutoffs() -> list[dict[str, object]]:
    return await get_context().repository.get_analysis_cutoffs()


@app.get("/analysis/flow/recent")
async def recent_flow_analyses(
    limit: int = 48,
    asset: str | None = None,
    market_id: str | None = None,
    strategy: str | None = None,
    cutoff_name: str | None = None,
) -> list[dict[str, object]]:
    return await get_context().repository.get_recent_flow_analyses(
        limit=limit,
        asset=asset,
        market_id=market_id,
        strategy=strategy,
        cutoff_name=cutoff_name,
    )


@app.post("/analysis/cutoffs/{cutoff_name}")
async def create_analysis_cutoff(cutoff_name: str) -> dict[str, object]:
    result = await get_context().repository.create_analysis_cutoff(
        cutoff_name,
        metadata={"source": "api"},
    )
    return {
        "cutoff_name": result["cutoff_name"],
        "created_at": result["created_at"].isoformat(),
        "metadata": result["metadata"],
    }


@app.get("/settlement/redeemables")
async def settlement_redeemables(limit: int = 20) -> list[dict[str, object]]:
    context = get_context()
    connector = MarketConnector(context)
    try:
        return await SettlementService(context, connector).preview_redeemable_positions(limit=limit)
    finally:
        await connector.close()


@app.post("/settlement/process")
async def settlement_process(dry_run: bool | None = None, limit: int = 20) -> dict[str, object]:
    context = get_context()
    connector = MarketConnector(context)
    try:
        return await SettlementService(context, connector).process_redeem_cycle(dry_run=dry_run, limit=limit)
    finally:
        await connector.close()


@app.get("/settlement/events/recent")
async def settlement_events_recent(limit: int = 20) -> list[dict[str, object]]:
    return await get_context().repository.get_recent_settlement_events(limit=limit)


@app.get("/discovery/funnel")
async def discovery_funnel(limit: int = 16) -> dict[str, object]:
    context = get_context()
    stored = await context.repository.get_latest_discovery_funnel(limit=limit)
    if stored is None:
        return {
            "run": None,
            "candidates": [],
            "latest_scan_stats": {},
            "stage_counts": [
                {"label": "universe", "count": 0},
                {"label": "crypto_classified", "count": 0},
                {"label": "deterministic_passed", "count": 0},
                {"label": "cheap_llm_passed", "count": 0},
                {"label": "claude_passed", "count": 0},
                {"label": "operable", "count": 0},
            ],
            "dropoff_counts": [],
            "rejected_breakdown": {},
            "cost_summary": {
                "research_cost_usd": 0.0,
                "research_calls": 0,
                "claude_cost_usd": 0.0,
                "claude_calls": 0,
                "total_cost_usd": 0.0,
            },
        }
    return stored


@app.post("/discovery/funnel/run")
async def discovery_funnel_run(limit: int = 24) -> dict[str, object]:
    context = get_context()
    service = DiscoveryService(context)
    try:
        return await service.run(limit=limit)
    finally:
        await service.close()
