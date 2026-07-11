"""Tool adapter contract (blueprint §37.11, §37.38).

Every source integration subclasses :class:`BaseTool` and implements only
``capabilities()``, ``supports()``, and ``_execute_live()``. The concrete
``execute()`` boundary owns everything cross-cutting:

- feature-flag gating (disabled source -> ``skipped_disabled``);
- fixture mode (deterministic files, never fabricated data);
- cached mode (repository lookup by args hash, optional live fallback);
- live mode (timeout + bounded retries on retryable results only);
- recording every call in the repository and trace, with secrets redacted;
- converting EVERY exception into a typed ``ToolResult`` — provider
  exceptions never propagate past this boundary.
"""

from __future__ import annotations

import abc
import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol

import httpx

from ..config import AppConfig, ExecutionMode, Settings
from ..schemas.common import utcnow
from ..schemas.source import ResearchAction, ToolCapabilities, ToolResult
from ..security import redact_secrets
from .http import SharedHttp

# Exceptions that make a live call worth retrying (transient by nature).
_RETRYABLE_EXCEPTIONS = (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.ConnectError)


class RepositoryLike(Protocol):
    """Structural contract BaseTool needs from the storage layer.

    ``storage/repository.py`` (built separately) must satisfy this. The
    boundary treats both methods as best-effort: recording failures never
    break a tool call, and lookup failures degrade to a cache miss.
    """

    def record_tool_call(self, record: dict[str, Any]) -> None: ...

    def find_cached_tool_call(self, tool_name: str, args_hash: str) -> Any | None: ...


class TraceWriterLike(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any]) -> None: ...


@dataclass
class ToolContext:
    """Everything an adapter may need at execution time."""

    run_id: str
    company_id: str
    mode: ExecutionMode
    config: AppConfig
    settings: Settings
    repository: Any  # RepositoryLike; typed Any until storage lands
    http: SharedHttp | None = None
    trace: Any | None = None  # TraceWriterLike
    allow_live_fallback: bool = False
    tool_timeout_seconds: int = 60


def action_args_hash(tool_name: str, action: ResearchAction) -> str:
    """Deterministic cache key over the semantically meaningful arguments."""
    payload = {
        "tool": tool_name,
        "action_type": action.action_type,
        "company_id": action.company_id,
        "source_name": action.source_name,
        "time_window_ids": sorted(action.time_window_ids),
        "parameters": action.parameters,
    }
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:60]


def _param_slug(parameters: dict[str, Any]) -> str:
    if not parameters:
        return ""
    return _slugify("_".join(f"{key}-{parameters[key]}" for key in sorted(parameters)))


class BaseTool(abc.ABC):
    """Base class for every source adapter."""

    name: ClassVar[str] = "base"
    adapter_version: ClassVar[str] = "0.1.0"
    # Key in config.sources gating this adapter. Missing flag = disabled
    # (fail closed): a source must be explicitly enabled to run.
    source_flag_name: ClassVar[str] = "base"

    max_live_retries: ClassVar[int] = 2
    retry_base_delay: ClassVar[float] = 0.5

    # ---- adapter surface -------------------------------------------------

    @abc.abstractmethod
    def capabilities(self) -> ToolCapabilities: ...

    @abc.abstractmethod
    def supports(self, action: ResearchAction) -> bool: ...

    @abc.abstractmethod
    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult: ...

    # ---- concrete boundary -----------------------------------------------

    async def execute(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        started = time.perf_counter()
        self._trace(
            context,
            "tool_started",
            {
                "run_id": context.run_id,
                "tool_name": self.name,
                "action_id": action.action_id,
                "action_type": action.action_type,
                "mode": context.mode,
            },
        )
        cache_status = "not_applicable"
        try:
            result, cache_status = await self._dispatch(action, context)
        except Exception as exc:  # noqa: BLE001 - the boundary must never propagate
            result = self._failure(action, exc, retryable=isinstance(exc, _RETRYABLE_EXCEPTIONS))

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if result.latency_ms == 0:
            result.latency_ms = elapsed_ms
        result.capability_snapshot = {
            **self._capability_snapshot(),
            **result.capability_snapshot,
        }

        self._record(action, context, result, cache_status)

        if result.status in ("failed_retryable", "failed_terminal"):
            event = "tool_failed"
        else:
            event = "tool_completed"
        self._trace(
            context,
            event,
            {
                "run_id": context.run_id,
                "tool_name": self.name,
                "action_id": action.action_id,
                "status": result.status,
                "latency_ms": result.latency_ms,
                "artifact_count": len(result.artifacts),
                "error_message": redact_secrets(result.error_message or "") or None,
            },
        )
        return result

    # ---- dispatch ----------------------------------------------------------

    async def _dispatch(
        self, action: ResearchAction, context: ToolContext
    ) -> tuple[ToolResult, str]:
        if not context.config.sources.get(self.source_flag_name, False):
            return (
                self._result(
                    action,
                    status="skipped_disabled",
                    error_message="This source was disabled for the run.",
                    negative_observations=[
                        f"Source flag '{self.source_flag_name}' is disabled or missing; "
                        f"'{action.action_type}' was not attempted."
                    ],
                ),
                "not_applicable",
            )

        if not self.supports(action):
            return (
                self._result(
                    action,
                    status="unsupported",
                    error_message=(
                        f"Tool '{self.name}' does not support action type '{action.action_type}'."
                    ),
                ),
                "not_applicable",
            )

        if context.mode == "fixture":
            return self._load_fixture(action, context), "fixture"
        if context.mode == "cached":
            return await self._cached_path(action, context)
        result = await self._run_live(action, context)
        return result, "live"

    # ---- fixture mode ------------------------------------------------------

    def _load_fixture(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        base_dir = Path(context.settings.fixtures_dir) / "tools" / self.name
        candidates: list[Path] = []
        slug = _param_slug(action.parameters)
        if slug:
            candidates.append(base_dir / f"{action.action_type}_{slug}.json")
        candidates.append(base_dir / f"{action.action_type}.json")

        path = next((c for c in candidates if c.exists()), None)
        if path is None:
            looked = "; ".join(str(c) for c in candidates)
            # Never fabricate data when a fixture is missing.
            return self._result(
                action,
                status="unsupported",
                error_message=(
                    f"No fixture available for tool '{self.name}', action "
                    f"'{action.action_type}'. Looked for: {looked}"
                ),
                negative_observations=[
                    f"Fixture mode: no fixture file for {self.name}/{action.action_type}."
                ],
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fixture_result = ToolResult.model_validate(payload)
        except Exception as exc:  # malformed payload -> reject and trace (§37.38)
            return self._result(
                action,
                status="failed_terminal",
                error_type=type(exc).__name__,
                error_message=redact_secrets(f"Fixture {path} could not be validated: {exc}"),
            )
        artifacts = [a.model_copy(update={"is_fixture": True}) for a in fixture_result.artifacts]
        return fixture_result.model_copy(
            update={"action_id": action.action_id, "tool_name": self.name, "artifacts": artifacts}
        )

    # ---- cached mode ---------------------------------------------------------

    async def _cached_path(
        self, action: ResearchAction, context: ToolContext
    ) -> tuple[ToolResult, str]:
        args_hash = action_args_hash(self.name, action)
        try:
            record = context.repository.find_cached_tool_call(self.name, args_hash)
        except Exception:  # lookup failure degrades to a miss
            record = None

        if record is not None:
            result = self._result_from_cached(action, context, record)
            if result is not None:
                result.capability_snapshot = {**result.capability_snapshot, "cache_hit": True}
                return result, "hit"

        if context.allow_live_fallback:
            result = await self._run_live(action, context)
            result.capability_snapshot = {**result.capability_snapshot, "cache_hit": False}
            return result, "miss_live_fallback"

        return (
            self._result(
                action,
                status="empty",
                capability_snapshot={"cache_hit": False},
                negative_observations=[
                    f"No cached result for {self.name}/{action.action_type} "
                    f"(args_hash={args_hash[:12]}); live fallback disabled."
                ],
            ),
            "miss",
        )

    def _result_from_cached(
        self, action: ResearchAction, context: ToolContext, record: Any
    ) -> ToolResult | None:
        """Reconstruct a ToolResult from a cached record; None = treat as miss."""
        try:
            if isinstance(record, ToolResult):
                result = record
            else:
                if isinstance(record, dict):
                    payload = record.get("result_json") or record.get("result") or record
                else:
                    payload = getattr(record, "result_json", None) or getattr(
                        record, "result", None
                    )
                if isinstance(payload, str):
                    payload = json.loads(payload)
                if not isinstance(payload, dict):
                    return None
                result = ToolResult.model_validate(payload)

            if not result.artifacts:
                if isinstance(record, dict):
                    artifact_ids = record.get("artifact_ids")
                else:
                    artifact_ids = getattr(record, "artifact_ids", None)
                if artifact_ids:
                    loader = getattr(context.repository, "get_artifacts", None) or getattr(
                        context.repository, "load_artifacts", None
                    )
                    if loader is not None:
                        result = result.model_copy(update={"artifacts": list(loader(artifact_ids))})
            return result.model_copy(update={"action_id": action.action_id, "tool_name": self.name})
        except Exception:
            return None

    # ---- live mode -----------------------------------------------------------

    async def _run_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        attempt = 0
        while True:
            result = await self._live_once(action, context)
            if (
                result.status == "failed_retryable"
                and result.retryable
                and attempt < self.max_live_retries
            ):
                attempt += 1
                delay = self.retry_base_delay * (2 ** (attempt - 1))
                if delay:
                    await asyncio.sleep(delay)
                continue
            return result

    async def _live_once(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        try:
            async with asyncio.timeout(context.tool_timeout_seconds):
                return await self._execute_live(action, context)
        except _RETRYABLE_EXCEPTIONS as exc:
            return self._failure(action, exc, retryable=True)
        except Exception as exc:  # noqa: BLE001 - provider exceptions stop here
            return self._failure(action, exc, retryable=False)

    # ---- recording / tracing ---------------------------------------------------

    def _record(
        self,
        action: ResearchAction,
        context: ToolContext,
        result: ToolResult,
        cache_status: str,
    ) -> None:
        try:
            args_json = redact_secrets(
                json.dumps(
                    {
                        "action_type": action.action_type,
                        "company_id": action.company_id,
                        "source_name": action.source_name,
                        "time_window_ids": action.time_window_ids,
                        "parameters": action.parameters,
                    },
                    sort_keys=True,
                    default=str,
                )
            )
            context.repository.record_tool_call(
                {
                    "run_id": context.run_id,
                    "company_id": context.company_id,
                    "tool_name": self.name,
                    "adapter_version": self.adapter_version,
                    "action_id": action.action_id,
                    "action_type": action.action_type,
                    "args_hash": action_args_hash(self.name, action),
                    "args_json": args_json,
                    "status": result.status,
                    "cache_status": cache_status,
                    "latency_ms": result.latency_ms,
                    "cost_usd": result.cost_usd,
                    "artifact_ids": [a.artifact_id for a in result.artifacts],
                    "result_json": redact_secrets(result.model_dump_json()),
                    "recorded_at": utcnow().isoformat(),
                }
            )
        except Exception:  # recording must never break the tool boundary
            self._trace(
                context,
                "tool_call_record_failed",
                {"tool_name": self.name, "action_id": action.action_id},
            )

    def _trace(self, context: ToolContext, event_type: str, payload: dict[str, Any]) -> None:
        trace = context.trace
        if trace is None:
            return
        try:
            emit = (
                getattr(trace, "emit", None)
                or getattr(trace, "write", None)
                or getattr(trace, "event", None)
            )
            if emit is not None:
                emit(event_type, payload)
        except Exception:  # tracing is best-effort
            pass

    # ---- helpers ------------------------------------------------------------

    def _capability_snapshot(self) -> dict[str, bool]:
        try:
            caps = self.capabilities()
            return {
                "live_available": caps.live_available,
                "fixture_available": caps.fixture_available,
            }
        except Exception:
            return {}

    def _result(self, action: ResearchAction, **overrides: Any) -> ToolResult:
        return ToolResult(action_id=action.action_id, tool_name=self.name, **overrides)

    def _failure(self, action: ResearchAction, exc: Exception, *, retryable: bool) -> ToolResult:
        return self._result(
            action,
            status="failed_retryable" if retryable else "failed_terminal",
            error_type=type(exc).__name__,
            error_message=redact_secrets(str(exc)) or type(exc).__name__,
            retryable=retryable,
        )
