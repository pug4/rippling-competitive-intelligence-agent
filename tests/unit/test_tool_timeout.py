"""Per-tool timeout boundary (P0 item 3).

Regression cover for two coupled bugs:

1. A tool whose internal poll budget exceeds the run-level
   ``ToolContext.tool_timeout_seconds`` default died at the shared 60s boundary
   on every live call. Tools now declare ``TOOL_TIMEOUT_SECONDS`` and the
   boundary honors it.
2. A boundary self-timeout was treated as retryable, so a doomed call burned 3x
   (initial + ``max_live_retries``). A boundary self-timeout is now
   non-retryable within the run, while a *provider* TimeoutError (transient
   read/connect) stays retryable.

Hermetic: no network. The slow paths use a stub tool that merely sleeps, so the
boundary cancels it deterministically.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, ClassVar

from competitive_agent.config import AppConfig, FocalCompanyConfig, Settings
from competitive_agent.schemas.source import ResearchAction, ToolCapabilities, ToolResult
from competitive_agent.tools import exa_agent as exa_agent_module
from competitive_agent.tools import similarweb as similarweb_module
from competitive_agent.tools.base import BaseTool, ToolContext
from competitive_agent.tools.exa_agent import ExaAgentTool
from competitive_agent.tools.similarweb import SimilarwebTool


class _FakeRepo:
    def record_tool_call(self, **record: Any) -> None:  # pragma: no cover - trivial sink
        return None

    def find_cached_tool_call(self, tool_name: str, args_hash: str) -> Any | None:
        return None


def _context(source_flag: str, *, tool_timeout_seconds: int = 60) -> ToolContext:
    config = AppConfig(
        focal_company=FocalCompanyConfig(),
        sources={source_flag: True},
        execution={},
        budgets={},
        portfolio={},
        windows={},
        taxonomy={},
        model_routes={},
        source_capabilities={},
    )
    return ToolContext(
        run_id="RUN-timeout",
        company_id="c",
        mode="live",  # type: ignore[arg-type]
        config=config,
        settings=Settings(),
        repository=_FakeRepo(),
        tool_timeout_seconds=tool_timeout_seconds,
    )


def _action(action_type: str) -> ResearchAction:
    return ResearchAction(
        action_id="ACT-timeout", action_type=action_type, company_id="c", parameters={}
    )


class _SlowTool(BaseTool):
    """Self-times-out: declares a 1s budget but its live call sleeps 2s."""

    name: ClassVar[str] = "slow_probe_tool"
    source_flag_name: ClassVar[str] = "slow_probe"
    TOOL_TIMEOUT_SECONDS: ClassVar[int | None] = 1

    def __init__(self) -> None:
        self.live_calls = 0

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=True,
            fixture_available=False,
            supported_action_types=["slow_probe"],
        )

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type == "slow_probe"

    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        self.live_calls += 1
        await asyncio.sleep(2)  # exceeds the declared 1s boundary
        return ToolResult(action_id=action.action_id, tool_name=self.name, status="success")


class _DefaultBoundaryTool(_SlowTool):
    """No override: falls back to the context's tool_timeout_seconds."""

    name: ClassVar[str] = "default_boundary_tool"
    source_flag_name: ClassVar[str] = "default_boundary"
    TOOL_TIMEOUT_SECONDS: ClassVar[int | None] = None

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type == "default_boundary"

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=True,
            fixture_available=False,
            supported_action_types=["default_boundary"],
        )


class _ProviderTimeoutTool(BaseTool):
    """Raises a provider TimeoutError immediately (NOT the boundary)."""

    name: ClassVar[str] = "provider_timeout_tool"
    source_flag_name: ClassVar[str] = "provider_timeout"
    TOOL_TIMEOUT_SECONDS: ClassVar[int | None] = 30  # generous; the raise is instant
    max_live_retries: ClassVar[int] = 2
    retry_base_delay: ClassVar[float] = 0.0  # keep the test fast

    def __init__(self) -> None:
        self.live_calls = 0

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=True,
            fixture_available=False,
            supported_action_types=["provider_timeout"],
        )

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type == "provider_timeout"

    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        self.live_calls += 1
        raise TimeoutError("provider read timed out")


# ---------------------------------------------------------------------------
# Boundary override is honored, and a self-timeout is non-retryable.
# ---------------------------------------------------------------------------


async def test_tool_timeout_override_fires_at_declared_boundary() -> None:
    tool = _SlowTool()
    started = time.perf_counter()
    result = await tool.execute(_action("slow_probe"), _context("slow_probe"))
    elapsed = time.perf_counter() - started

    # Fired at the 1s OVERRIDE, not the 60s context default and well before the
    # 2s the tool would have taken (proves the override is honored).
    assert 0.9 <= elapsed < 1.9, elapsed
    # Provider/timeout exceptions never propagate past the boundary — typed result.
    assert result.status == "failed_terminal"
    assert result.error_type == "TimeoutError"
    # A boundary self-timeout is NON-retryable: no 3x amplification.
    assert result.retryable is False
    assert tool.live_calls == 1


async def test_boundary_falls_back_to_context_default_when_unset() -> None:
    tool = _DefaultBoundaryTool()
    started = time.perf_counter()
    # No override -> uses the context's (here shortened) tool_timeout_seconds.
    result = await tool.execute(
        _action("default_boundary"), _context("default_boundary", tool_timeout_seconds=1)
    )
    elapsed = time.perf_counter() - started

    assert 0.9 <= elapsed < 1.9, elapsed
    assert result.status == "failed_terminal"
    assert result.retryable is False
    assert tool.live_calls == 1


async def test_provider_timeout_not_from_boundary_stays_retryable() -> None:
    tool = _ProviderTimeoutTool()
    result = await tool.execute(_action("provider_timeout"), _context("provider_timeout"))

    # A provider-raised TimeoutError (the boundary never fired) is transient and
    # still retried up to the bound — the anti-amplification fix must NOT swallow
    # genuinely retryable timeouts.
    assert result.status == "failed_retryable"
    assert result.retryable is True
    assert tool.live_calls == 1 + _ProviderTimeoutTool.max_live_retries


def test_base_tool_default_override_is_none() -> None:
    # The global default is unchanged: tools opt in individually.
    assert BaseTool.TOOL_TIMEOUT_SECONDS is None
    assert ToolContext.tool_timeout_seconds == 60


# ---------------------------------------------------------------------------
# The two real tools' overrides must cover their own poll budgets.
# ---------------------------------------------------------------------------


def test_exa_agent_timeout_exceeds_worst_case_poll_budget() -> None:
    # The tool's own poll loop must exhaust (returning a typed agent_incomplete)
    # BEFORE the boundary fires, or every live call dies at the boundary again.
    # Worst-case time to give up on its own = the initial POST budget + one
    # poll-interval sleep per poll. Computed from the tool's OWN constants so a
    # future poll change (more polls / longer interval) that outgrows the
    # boundary re-breaks this test.
    worst_case_poll_budget = exa_agent_module._POST_TIMEOUT + (
        exa_agent_module._MAX_POLLS * exa_agent_module._POLL_INTERVAL
    )
    assert ExaAgentTool.TOOL_TIMEOUT_SECONDS is not None
    assert ExaAgentTool.TOOL_TIMEOUT_SECONDS >= worst_case_poll_budget
    # And it must beat the global default that killed it before.
    assert ExaAgentTool.TOOL_TIMEOUT_SECONDS > ToolContext.tool_timeout_seconds


def test_similarweb_timeout_covers_its_poll_budget() -> None:
    # similarweb's between-poll sleeps dominate its give-up budget.
    sleep_budget = similarweb_module._POLL_MAX_ATTEMPTS * similarweb_module._POLL_DELAY_SECONDS
    assert SimilarwebTool.TOOL_TIMEOUT_SECONDS is not None
    assert SimilarwebTool.TOOL_TIMEOUT_SECONDS >= sleep_budget
    assert SimilarwebTool.TOOL_TIMEOUT_SECONDS > ToolContext.tool_timeout_seconds
