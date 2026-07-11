"""Research Director graph: an explicit, inspectable, resumable state machine.

Every §37.13 node exists in ``NODE_ORDER``; nodes are async callables
``(state, ctx) -> (state, next_node_name)``. The driver persists state after
every node (checkpointing contract) and appends trace events, so a run can be
killed and resumed at ``state.current_node`` at any point. Nothing about the
loop hides inside a framework.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .state import DirectorState

NodeFn = Callable[[DirectorState, "GraphContext"], Awaitable[tuple[DirectorState, str | None]]]

NODE_ORDER = [
    "initialize_run",
    "resolve_companies",
    "load_or_create_time_windows",
    "load_focal_state",
    "assess_coverage",
    "identify_unresolved_questions",
    "propose_actions",
    "score_actions",
    "select_next_action",
    "execute_action",
    "normalize_and_deduplicate",
    "extract_and_classify",
    "validate_evidence",
    "update_coverage",
    "refresh_claims",
    "check_contradictions",
    "verify_temporal_changes",
    "build_matrices",
    "run_focal_mirror_check",
    "generate_opportunities",
    "critique_opportunities",
    "decide_continue_or_stop",
    "render_outputs",
    "await_followup",
    "process_feedback_or_retry",
]

TERMINAL_NODES = {"render_outputs_done", "awaiting_user", "stopped"}


@dataclass
class GraphContext:
    """Everything nodes need; provider objects stay behind these fields."""

    repository: Any
    trace: Any
    config: Any
    settings: Any
    gateway: Any = None
    tool_registry: Any = None
    http: Any = None
    scratch: dict[str, Any] = field(default_factory=dict)


class Graph:
    def __init__(self, nodes: dict[str, NodeFn]):
        missing = [n for n in NODE_ORDER if n not in nodes]
        if missing:
            raise ValueError(f"graph missing required nodes: {missing}")
        self.nodes = nodes

    async def run(self, state: DirectorState, ctx: GraphContext) -> DirectorState:
        """Drive until a terminal condition. Persists state after EVERY node."""
        while True:
            node_name = state.current_node
            if node_name in TERMINAL_NODES or state.is_complete:
                break
            fn = self.nodes.get(node_name)
            if fn is None:
                state.stop_reason = f"unknown_node:{node_name}"
                state.is_complete = True
                break

            if ctx.trace:
                ctx.trace.append("node_started", {"node": node_name, "iteration": state.iteration})
            try:
                result = fn(state, ctx)
                if inspect.isawaitable(result):
                    state, next_node = await result
                else:  # pragma: no cover - nodes should be async
                    state, next_node = result
            except Exception as exc:
                # A node bug must not lose the run: checkpoint and stop with reason.
                state.stop_reason = f"node_error:{node_name}:{type(exc).__name__}"
                state.is_complete = True
                if ctx.trace:
                    ctx.trace.append(
                        "node_failed", {"node": node_name, "error": f"{type(exc).__name__}: {exc}"}
                    )
                self._checkpoint(state, ctx)
                raise

            state.current_node = next_node or "decide_continue_or_stop"
            self._checkpoint(state, ctx)

            if state.pending_user_question:
                state.current_node = "awaiting_user"
                self._checkpoint(state, ctx)
                break
        return state

    def _checkpoint(self, state: DirectorState, ctx: GraphContext) -> None:
        if ctx.repository is not None:
            ctx.repository.update_run_state(
                state.run_id,
                status="complete" if state.is_complete else "running",
                current_node=state.current_node,
                state=state,
            )


def load_state(repository: Any, run_id: str) -> DirectorState:
    row = repository.get_run(run_id)
    if row is None:
        raise KeyError(f"run not found: {run_id}")
    return DirectorState.model_validate_json(row["state_json"])
