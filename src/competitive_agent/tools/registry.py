"""Tool registry: routes ResearchActions to registered adapters."""

from __future__ import annotations

from typing import Any

from ..schemas.source import ResearchAction, ToolResult
from .base import BaseTool, ToolContext


class ToolRegistry:
    """Holds every registered adapter and resolves actions to tools.

    Resolution order: an explicit ``action.source_name`` match wins (if that
    tool supports the action); otherwise the first registered tool whose
    ``supports()`` accepts the action. Registration order is therefore the
    preference order.
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"A tool named '{tool.name}' is already registered.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def for_action(self, action: ResearchAction) -> BaseTool | None:
        if action.source_name:
            preferred = self._tools.get(action.source_name)
            if preferred is not None and preferred.supports(action):
                return preferred
        for tool in self._tools.values():
            if tool.supports(action):
                return tool
        return None

    def list_capabilities(self) -> dict[str, dict[str, Any]]:
        """Capability snapshot for the run record / capability panel."""
        snapshot: dict[str, dict[str, Any]] = {}
        for name, tool in self._tools.items():
            entry: dict[str, Any] = {"adapter_version": tool.adapter_version}
            try:
                entry.update(tool.capabilities().model_dump())
            except Exception as exc:  # a broken adapter must not hide the others
                entry["capabilities_error"] = f"{type(exc).__name__}: {exc}"
            snapshot[name] = entry
        return snapshot

    async def run_action(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        tool = self.for_action(action)
        if tool is None:
            return ToolResult(
                action_id=action.action_id,
                tool_name="registry",
                status="unsupported",
                error_message=(
                    f"No registered tool supports action type '{action.action_type}'"
                    + (f" (requested source '{action.source_name}')" if action.source_name else "")
                    + "."
                ),
                negative_observations=[
                    f"Action '{action.action_type}' could not be routed to any adapter."
                ],
            )
        return await tool.execute(action, context)
