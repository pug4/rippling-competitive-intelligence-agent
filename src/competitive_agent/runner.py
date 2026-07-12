"""Run assembly: wire settings, storage, trace, gateway, and tools into a
GraphContext and drive the Research Director for one run."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .config import ExecutionMode, get_config, get_settings
from .graph import Graph, GraphContext, load_state
from .nodes import build_default_nodes
from .schemas.common import new_id
from .state import DirectorState


def _build_registry():
    from .tools.ads import GoogleAdsTool, LinkedInAdsTool, MetaAdsTool
    from .tools.events import EventsTool
    from .tools.exa_search import ExaSearchTool
    from .tools.jobs import JobsTool
    from .tools.ooh import OOHTool
    from .tools.registry import ToolRegistry
    from .tools.reviews import ReviewsTool
    from .tools.similarweb import SimilarwebTool
    from .tools.wayback import WaybackTool
    from .tools.webpage import WebpageFetchTool, WebsiteMapTool

    registry = ToolRegistry()
    # Level-A report-critical adapters first; Level-B optional adapters after.
    # Each Level-B tool is gated by its own source flag and is non-blocking.
    for tool in (
        WebsiteMapTool(), WebpageFetchTool(), WaybackTool(), ExaSearchTool(),
        SimilarwebTool(), ReviewsTool(), JobsTool(), EventsTool(), OOHTool(),
        GoogleAdsTool(), MetaAdsTool(), LinkedInAdsTool(),
    ):
        registry.register(tool)
    return registry


def _build_context(run_id: str, execution_mode: str = "fixture") -> GraphContext:
    from .model_gateway import build_gateway
    from .storage.repository import Repository
    from .tools.http import SharedHttp
    from .tracing import TraceWriter

    settings = get_settings()
    config = get_config()
    repository = Repository.open(settings.db_path)
    run_dir = Path(settings.outputs_dir) / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    trace = TraceWriter(run_dir, run_id=run_id)

    gateway = build_gateway(execution_mode, settings, config)  # type: ignore[arg-type]
    # Live network only for live/cached; fixture mode never opens a client.
    http = SharedHttp.from_settings(settings) if execution_mode in ("live", "cached") else None
    return GraphContext(
        repository=repository,
        trace=trace,
        config=config,
        settings=settings,
        gateway=gateway,
        tool_registry=_build_registry(),
        http=http,
    )


def create_run(
    company_input: str,
    *,
    mode: str = "snapshot",
    execution_mode: ExecutionMode | None = None,
    compare_to: str | None = None,
    lookback_days: int | None = None,
    user_focus: list[str] | None = None,
    parent_run_id: str | None = None,
    retry_mode: str | None = None,
) -> tuple[DirectorState, GraphContext]:
    settings = get_settings()
    config = get_config()
    budgets = config.budgets
    run_id = new_id("RUN")
    state = DirectorState(
        run_id=run_id,
        parent_run_id=parent_run_id,
        company_input=company_input,
        compare_to=compare_to,
        mode=mode,  # type: ignore[arg-type]
        execution_mode=execution_mode or settings.default_run_mode,
        lookback_days=lookback_days or settings.default_lookback_days,
        user_focus=user_focus or [],
        retry_mode=retry_mode,
        budget_usd=float(budgets.get("research_budget_usd", 5.0)),
        max_runtime_seconds=int(budgets.get("max_runtime_seconds", 600)),
        max_iterations=int(budgets.get("max_iterations", 40)),
        max_tool_calls=int(budgets.get("max_tool_calls", 120)),
    )
    ctx = _build_context(run_id, execution_mode=state.execution_mode)
    ctx.repository.create_run(
        run_id=run_id,
        company=company_input,
        mode=mode,
        status="created",
        parent_run_id=parent_run_id,
        retry_mode=retry_mode,
        execution_mode=state.execution_mode,
    )
    ctx.repository.update_run_state(run_id, current_node=state.current_node, state=state)
    return state, ctx


async def drive(state: DirectorState, ctx: GraphContext) -> DirectorState:
    graph = Graph(build_default_nodes())
    return await graph.run(state, ctx)


def run_analysis(company_input: str, **kwargs: Any) -> DirectorState:
    state, ctx = create_run(company_input, **kwargs)
    return asyncio.run(drive(state, ctx))


def resume_run(run_id: str) -> DirectorState:
    from .storage.repository import Repository

    settings = get_settings()
    row = Repository.open(settings.db_path).get_run(run_id)
    if row is None:
        raise KeyError(f"run not found: {run_id}")
    execution_mode = row["execution_mode"] or "fixture"
    ctx = _build_context(run_id, execution_mode=execution_mode)
    state = load_state(ctx.repository, run_id)
    state.pending_user_question = None
    if state.current_node in ("awaiting_user", "render_outputs_done", "stopped"):
        state.current_node = "assess_coverage"
    return asyncio.run(drive(state, ctx))
