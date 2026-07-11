"""Generic adapter contract suite (blueprint §37.33), proven with a DummyTool.

Every adapter inherits this behavior from BaseTool.execute(); the suite
checks the boundary itself:

- capabilities returns a valid schema
- unsupported action is rejected
- success / empty / retryable / terminal results validate
- disabled feature flag -> skipped_disabled
- fixture mode is deterministic; missing fixture -> unsupported (no fake data)
- exceptions in _execute_live are converted, never propagated
- secrets do not appear in recorded args
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from competitive_agent.config import AppConfig, FocalCompanyConfig, Settings
from competitive_agent.schemas.artifact import RawArtifact
from competitive_agent.schemas.common import new_id, utcnow
from competitive_agent.schemas.source import ResearchAction, ToolCapabilities, ToolResult
from competitive_agent.tools import BaseTool, ToolContext, ToolRegistry

# ---------------------------------------------------------------------------
# Repository: use the real one if the storage layer exists, else an in-memory
# stub compatible with the RepositoryLike protocol.
# ---------------------------------------------------------------------------


class InMemoryRepository:
    """Minimal RepositoryLike stand-in until storage/repository.py lands."""

    def __init__(self) -> None:
        self.tool_calls: list[dict[str, Any]] = []
        self.cached: dict[tuple[str, str], Any] = {}
        self.artifacts: dict[str, RawArtifact] = {}

    def record_tool_call(self, record: dict[str, Any]) -> None:
        self.tool_calls.append(record)

    def find_cached_tool_call(self, tool_name: str, args_hash: str) -> Any | None:
        return self.cached.get((tool_name, args_hash))

    def get_artifacts(self, artifact_ids: list[str]) -> list[RawArtifact]:
        return [self.artifacts[i] for i in artifact_ids if i in self.artifacts]


def make_repository(tmp_path: Path) -> Any:
    try:
        from competitive_agent.storage.repository import (  # type: ignore[import-not-found]
            Repository,
        )
    except Exception:
        return InMemoryRepository()
    for kwargs in ({"db_path": tmp_path / "contract-test.db"}, {}):
        try:
            return Repository(**kwargs)
        except Exception:
            continue
    return InMemoryRepository()


# ---------------------------------------------------------------------------
# DummyTool + context builders
# ---------------------------------------------------------------------------


def make_artifact(company_id: str = "cmp-test") -> RawArtifact:
    return RawArtifact(
        artifact_id=new_id("art"),
        company_id=company_id,
        source_type="website",
        source_name="dummy",
        url="https://example.com/pricing",
        final_url="https://example.com/pricing",
        retrieved_at=utcnow(),
        raw_text="Example pricing page",
        normalized_text="Example pricing page",
        content_hash="deadbeef",
        collection_method="dummy_fetch",
    )


class DummyTool(BaseTool):
    name = "dummy"
    adapter_version = "0.1.0"
    source_flag_name = "dummy"
    retry_base_delay = 0.0  # keep contract tests fast

    def __init__(self, behavior: str = "success") -> None:
        self.behavior = behavior
        self.live_calls = 0

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=True,
            fixture_available=True,
            supported_action_types=["dummy_search"],
            known_limitations=["test adapter"],
        )

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type in self.capabilities().supported_action_types

    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        self.live_calls += 1
        if self.behavior == "raise":
            raise RuntimeError("provider exploded; api_key=sk-should-never-leak")
        if self.behavior == "empty":
            return ToolResult(
                action_id=action.action_id,
                tool_name=self.name,
                status="empty",
                negative_observations=["No evidence was observed using these search parameters."],
            )
        if self.behavior == "retryable":
            return ToolResult(
                action_id=action.action_id,
                tool_name=self.name,
                status="failed_retryable",
                error_type="RateLimited",
                error_message="provider rate limited",
                retryable=True,
            )
        if self.behavior == "terminal":
            return ToolResult(
                action_id=action.action_id,
                tool_name=self.name,
                status="failed_terminal",
                error_type="NotPubliclyAccessible",
                error_message="login required",
                retryable=False,
            )
        return ToolResult(
            action_id=action.action_id,
            tool_name=self.name,
            status="success",
            artifacts=[make_artifact(action.company_id)],
        )


def make_config(sources: dict[str, bool]) -> AppConfig:
    return AppConfig(
        focal_company=FocalCompanyConfig(),
        sources=sources,
        execution={},
        budgets={},
        portfolio={},
        windows={},
        taxonomy={},
        model_routes={},
        source_capabilities={},
    )


def make_action(action_type: str = "dummy_search", **parameters: Any) -> ResearchAction:
    return ResearchAction(
        action_id=new_id("act"),
        action_type=action_type,
        company_id="cmp-test",
        parameters=parameters,
    )


def make_context(
    tmp_path: Path,
    *,
    mode: str = "live",
    sources: dict[str, bool] | None = None,
    repository: Any | None = None,
    allow_live_fallback: bool = False,
) -> ToolContext:
    return ToolContext(
        run_id=new_id("run"),
        company_id="cmp-test",
        mode=mode,  # type: ignore[arg-type]
        config=make_config(sources if sources is not None else {"dummy": True}),
        settings=Settings(fixtures_dir=tmp_path / "fixtures"),
        repository=repository if repository is not None else InMemoryRepository(),
        allow_live_fallback=allow_live_fallback,
    )


def write_fixture(tmp_path: Path, tool_name: str, action_type: str) -> Path:
    fixture_result = ToolResult(
        action_id="act-fixture",
        tool_name=tool_name,
        status="success",
        artifacts=[make_artifact()],
    )
    path = tmp_path / "fixtures" / "tools" / tool_name / f"{action_type}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fixture_result.model_dump_json(), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Contract suite
# ---------------------------------------------------------------------------


def test_capabilities_returns_valid_schema() -> None:
    caps = DummyTool().capabilities()
    assert isinstance(caps, ToolCapabilities)
    # round-trips through the schema
    assert ToolCapabilities.model_validate(caps.model_dump()) == caps
    assert "dummy_search" in caps.supported_action_types


async def test_unsupported_action_rejected(tmp_path: Path) -> None:
    tool = DummyTool()
    context = make_context(tmp_path, mode="live", repository=make_repository(tmp_path))
    action = make_action("satellite_imagery")
    result = await tool.execute(action, context)
    assert result.status == "unsupported"
    assert tool.live_calls == 0
    assert ToolResult.model_validate(result.model_dump())

    # the registry refuses to route it as well
    registry = ToolRegistry()
    registry.register(tool)
    assert registry.for_action(action) is None
    routed = await registry.run_action(action, context)
    assert routed.status == "unsupported"


async def test_success_result_validates(tmp_path: Path) -> None:
    tool = DummyTool("success")
    result = await tool.execute(
        make_action(), make_context(tmp_path, repository=make_repository(tmp_path))
    )
    assert result.status == "success"
    assert len(result.artifacts) == 1
    assert ToolResult.model_validate(result.model_dump())


async def test_empty_result_validates(tmp_path: Path) -> None:
    tool = DummyTool("empty")
    result = await tool.execute(
        make_action(), make_context(tmp_path, repository=make_repository(tmp_path))
    )
    assert result.status == "empty"
    assert result.negative_observations  # negative observation is a finding
    assert ToolResult.model_validate(result.model_dump())


async def test_retryable_failure_validates_with_bounded_retries(tmp_path: Path) -> None:
    tool = DummyTool("retryable")
    result = await tool.execute(
        make_action(), make_context(tmp_path, repository=make_repository(tmp_path))
    )
    assert result.status == "failed_retryable"
    assert result.retryable is True
    # 1 initial attempt + max_live_retries bounded retries, then gave up
    assert tool.live_calls == 1 + DummyTool.max_live_retries
    assert ToolResult.model_validate(result.model_dump())


async def test_terminal_failure_validates_and_does_not_retry(tmp_path: Path) -> None:
    tool = DummyTool("terminal")
    result = await tool.execute(
        make_action(), make_context(tmp_path, repository=make_repository(tmp_path))
    )
    assert result.status == "failed_terminal"
    assert result.retryable is False
    assert tool.live_calls == 1
    assert ToolResult.model_validate(result.model_dump())


async def test_disabled_flag_yields_skipped_disabled(tmp_path: Path) -> None:
    tool = DummyTool("success")
    context = make_context(tmp_path, sources={"dummy": False}, repository=make_repository(tmp_path))
    result = await tool.execute(make_action(), context)
    assert result.status == "skipped_disabled"
    assert tool.live_calls == 0  # never touched the live path
    assert "disabled" in (result.error_message or "").lower()


async def test_fixture_mode_is_deterministic(tmp_path: Path) -> None:
    write_fixture(tmp_path, "dummy", "dummy_search")
    tool = DummyTool("success")
    context = make_context(tmp_path, mode="fixture", repository=make_repository(tmp_path))
    action = make_action()

    first = await tool.execute(action, context)
    second = await tool.execute(action, context)

    assert first.status == "success"
    assert tool.live_calls == 0  # fixture mode never goes live
    assert first.artifacts[0].is_fixture is True
    # deterministic: identical evidence on every run
    assert [a.content_hash for a in first.artifacts] == [a.content_hash for a in second.artifacts]
    assert first.model_dump(exclude={"latency_ms"}) == second.model_dump(exclude={"latency_ms"})
    assert first.action_id == action.action_id  # rebound to the requesting action


async def test_missing_fixture_is_unsupported_never_fake(tmp_path: Path) -> None:
    tool = DummyTool("success")
    context = make_context(tmp_path, mode="fixture", repository=make_repository(tmp_path))
    result = await tool.execute(make_action(), context)
    assert result.status == "unsupported"
    assert result.artifacts == []  # no fake data, ever
    # the error message lists the path(s) that were looked up
    expected_path = str(tmp_path / "fixtures" / "tools" / "dummy" / "dummy_search.json")
    assert expected_path in (result.error_message or "")
    assert tool.live_calls == 0


async def test_exception_in_execute_live_becomes_failed_terminal(tmp_path: Path) -> None:
    tool = DummyTool("raise")
    result = await tool.execute(
        make_action(), make_context(tmp_path, repository=make_repository(tmp_path))
    )  # must NOT raise
    assert result.status == "failed_terminal"
    assert result.error_type == "RuntimeError"
    # the secret embedded in the provider exception was redacted
    assert "sk-should-never-leak" not in (result.error_message or "")
    assert ToolResult.model_validate(result.model_dump())


async def test_secrets_do_not_appear_in_recorded_args(tmp_path: Path) -> None:
    # Uses the in-memory stub explicitly: the behavior under test is the
    # BaseTool boundary's redaction before persistence, not the backend.
    repo = InMemoryRepository()
    tool = DummyTool("success")
    context = make_context(tmp_path, repository=repo)
    action = make_action(query="deel eor ads", api_key="sk-super-secret-42")
    await tool.execute(action, context)

    assert repo.tool_calls, "tool call was not recorded"
    record = repo.tool_calls[-1]
    serialized = json.dumps(record, default=str)
    assert "sk-super-secret-42" not in serialized
    assert "[REDACTED]" in record["args_json"]
    assert "deel eor ads" in record["args_json"]  # non-secret args preserved


async def test_cached_miss_without_fallback_is_empty_negative_observation(
    tmp_path: Path,
) -> None:
    tool = DummyTool("success")
    context = make_context(tmp_path, mode="cached", repository=InMemoryRepository())
    result = await tool.execute(make_action(), context)
    assert result.status == "empty"
    assert result.negative_observations
    assert result.capability_snapshot.get("cache_hit") is False
    assert tool.live_calls == 0


async def test_cached_miss_with_fallback_goes_live(tmp_path: Path) -> None:
    tool = DummyTool("success")
    context = make_context(
        tmp_path, mode="cached", repository=InMemoryRepository(), allow_live_fallback=True
    )
    result = await tool.execute(make_action(), context)
    assert result.status == "success"
    assert tool.live_calls == 1
    assert result.capability_snapshot.get("cache_hit") is False


async def test_cached_hit_reconstructs_result(tmp_path: Path) -> None:
    from competitive_agent.tools import action_args_hash

    repo = InMemoryRepository()
    tool = DummyTool("success")
    action = make_action()
    cached_result = ToolResult(
        action_id="act-old",
        tool_name="dummy",
        status="success",
        artifacts=[make_artifact()],
    )
    repo.cached[("dummy", action_args_hash("dummy", action))] = {
        "result_json": cached_result.model_dump_json()
    }
    context = make_context(tmp_path, mode="cached", repository=repo)
    result = await tool.execute(action, context)
    assert result.status == "success"
    assert result.capability_snapshot.get("cache_hit") is True
    assert result.action_id == action.action_id  # rebound to the requesting action
    assert tool.live_calls == 0


async def test_every_execution_is_recorded(tmp_path: Path) -> None:
    repo = InMemoryRepository()
    tool = DummyTool("terminal")
    await tool.execute(make_action(), make_context(tmp_path, repository=repo))
    assert len(repo.tool_calls) == 1
    record = repo.tool_calls[0]
    assert record["tool_name"] == "dummy"
    assert record["status"] == "failed_terminal"
    assert record["args_hash"]
