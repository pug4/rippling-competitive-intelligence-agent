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


def _build_context(run_id: str) -> GraphContext:
    from .storage.repository import Repository
    from .tracing import TraceWriter

    settings = get_settings()
    config = get_config()
    repository = Repository.open(settings.db_path)
    run_dir = Path(settings.outputs_dir) / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    trace = TraceWriter(run_dir, run_id=run_id)
    return GraphContext(repository=repository, trace=trace, config=config, settings=settings)


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
    ctx = _build_context(run_id)
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
    ctx = _build_context(run_id)
    state = load_state(ctx.repository, run_id)
    state.pending_user_question = None
    if state.current_node in ("awaiting_user", "render_outputs_done", "stopped"):
        state.current_node = "assess_coverage"
    return asyncio.run(drive(state, ctx))
