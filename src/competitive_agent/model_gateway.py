"""Provider-facing model gateway (blueprint §37.28).

Core code calls ``generate_structured`` and never touches the Anthropic SDK
directly. Model IDs are configuration values (``config/model_routes.yaml``),
never architecture: routing resolves ``task_name -> tier -> model id`` and
every ``ModelResult`` records the exact model that produced it.

Structured output is obtained by forcing a single ``emit`` tool whose
``input_schema`` is the pydantic output model's JSON schema, then validating
the returned tool input. Invalid output gets exactly ONE repair retry that
carries the validation errors back to the model; a still-invalid result
optionally escalates ONCE to the configured escalation model, after which
``ModelOutputInvalid`` is raised. There is no unbounded retry loop.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Literal

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

from .config import AppConfig, ExecutionMode, Settings
from .exceptions import CompetitiveAgentError, FixtureMissing, ModelOutputInvalid

EMIT_TOOL_NAME = "emit"
DEFAULT_TIER = "tier2"
DEFAULT_MAX_TOKENS = 4096

# Approximate USD prices per million tokens (input, output), keyed by model id.
# These are configuration values, not architecture — update alongside
# config/model_routes.yaml as pricing changes. Unknown model ids price at 0.0
# so cost accounting degrades gracefully instead of failing a run.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-8": (5.00, 25.00),
}


def compute_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    in_price, out_price = MODEL_PRICES.get(model_id, (0.0, 0.0))
    return (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price


class ModelResult(BaseModel):
    """Record of one structured-generation call, including cost/provenance."""

    output: Any
    model_id: str
    task_name: str
    prompt_name: str = ""
    prompt_version: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    repair_retry_used: bool = False
    escalated: bool = False
    cache_status: Literal["live", "fixture", "cached"] = "live"


class RoutingContext(BaseModel):
    task_name: str
    escalate_on_failure: bool = True


def _find_emit_block(response: Any) -> Any | None:
    for block in getattr(response, "content", None) or []:
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", "") == EMIT_TOOL_NAME
        ):
            return block
    return None


def _validate_emit(
    response: Any, output_model: type[BaseModel]
) -> tuple[BaseModel | None, str | None]:
    """Return (validated instance, None) or (None, error description)."""
    block = _find_emit_block(response)
    if block is None:
        return None, f"the response did not include a tool_use block named {EMIT_TOOL_NAME!r}"
    try:
        return output_model.model_validate(block.input), None
    except ValidationError as exc:
        return None, str(exc)


def _repair_instruction(error: str) -> str:
    return (
        f"The previous {EMIT_TOOL_NAME!r} tool input failed schema validation.\n"
        f"Validation errors:\n{error}\n\n"
        f"Call the {EMIT_TOOL_NAME!r} tool again with a corrected input that satisfies "
        "the schema exactly. Keep every field that was already valid unchanged and fix "
        "only the reported problems. Do not add commentary."
    )


class AnthropicGateway:
    """Live gateway over ``anthropic.AsyncAnthropic``.

    ``routes`` is the parsed ``config/model_routes.yaml`` document from
    ``config.get_config().model_routes``:

    ``models``: tier name -> model id (plus ``escalation``);
    ``routes``: task name -> tier name;
    ``max_output_tokens``: tier name -> int.
    """

    def __init__(self, settings: Settings, routes: dict[str, Any]) -> None:
        self._settings = settings
        self._routes = routes
        # Created lazily so tests can inject a fake client object before any
        # network-capable client is constructed.
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = AsyncAnthropic(api_key=self._settings.anthropic_api_key or None)
        return self._client

    def _resolve(self, task_name: str) -> tuple[str, str, int]:
        """task name -> (tier, model id, max output tokens)."""
        tier = self._routes.get("routes", {}).get(task_name, DEFAULT_TIER)
        models = self._routes.get("models", {})
        model_id = models.get(tier) or models.get(DEFAULT_TIER) or ""
        if not model_id:
            raise CompetitiveAgentError(
                f"no model configured for task {task_name!r} (tier {tier!r}); "
                "check config/model_routes.yaml"
            )
        max_tokens = self._routes.get("max_output_tokens", {}).get(tier, DEFAULT_MAX_TOKENS)
        return tier, model_id, int(max_tokens)

    def _escalation_model(self, fallback: str) -> str:
        models = self._routes.get("models", {})
        return models.get("escalation") or models.get("tier2") or fallback

    async def generate_structured(
        self,
        task_name: str,
        system: str,
        user_content: str,
        output_model: type[BaseModel],
        routing_context: RoutingContext | None = None,
        prompt_name: str = "",
        prompt_version: str = "",
    ) -> ModelResult:
        ctx = routing_context or RoutingContext(task_name=task_name)
        _tier, model_id, max_tokens = self._resolve(task_name)
        tool = {
            "name": EMIT_TOOL_NAME,
            "description": "Return the structured result",
            "input_schema": output_model.model_json_schema(),
        }
        base_messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]

        started = time.monotonic()
        input_tokens = 0
        output_tokens = 0
        cost_usd = 0.0

        async def attempt(model: str, messages: list[dict[str, Any]]) -> Any:
            nonlocal input_tokens, output_tokens, cost_usd
            response = await self._get_client().messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=[tool],
                tool_choice={"type": "tool", "name": EMIT_TOOL_NAME},
            )
            usage = getattr(response, "usage", None)
            attempt_in = int(getattr(usage, "input_tokens", 0) or 0)
            attempt_out = int(getattr(usage, "output_tokens", 0) or 0)
            input_tokens += attempt_in
            output_tokens += attempt_out
            cost_usd += compute_cost_usd(model, attempt_in, attempt_out)
            return response

        def result(
            output: BaseModel, model: str, *, repaired: bool, escalated: bool
        ) -> ModelResult:
            return ModelResult(
                output=output,
                model_id=model,
                task_name=task_name,
                prompt_name=prompt_name,
                prompt_version=prompt_version,
                latency_ms=int((time.monotonic() - started) * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                repair_retry_used=repaired,
                escalated=escalated,
                cache_status="live",
            )

        # Attempt 1: the routed model.
        response = await attempt(model_id, base_messages)
        parsed, error = _validate_emit(response, output_model)
        if parsed is not None:
            return result(parsed, model_id, repaired=False, escalated=False)

        # Exactly one repair retry carrying the validation errors back.
        repair_messages = base_messages + [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": _repair_instruction(error or "unknown error")},
        ]
        response = await attempt(model_id, repair_messages)
        parsed, error = _validate_emit(response, output_model)
        if parsed is not None:
            return result(parsed, model_id, repaired=True, escalated=False)

        if not ctx.escalate_on_failure:
            raise ModelOutputInvalid(
                f"task {task_name!r} on model {model_id!r}: output still failed schema "
                f"validation after one repair retry (escalation disabled): {error}"
            )

        # One attempt on the escalation model, reusing the repair conversation
        # so the stronger model sees the failed attempt and its errors.
        escalation_model = self._escalation_model(model_id)
        response = await attempt(escalation_model, repair_messages)
        parsed, error = _validate_emit(response, output_model)
        if parsed is not None:
            return result(parsed, escalation_model, repaired=True, escalated=True)

        raise ModelOutputInvalid(
            f"task {task_name!r}: output failed schema validation after one repair retry "
            f"on {model_id!r} and one escalation attempt on {escalation_model!r}: {error}"
        )


class FixtureGateway:
    """Deterministic gateway for offline/test runs (mode ``fixture``).

    Fixture layout: ``<fixtures_dir>/model/<task_name>/<key>.json`` where
    ``key`` is the first existing of ``sha256(user_content)[:16]`` and
    ``default``. The JSON body is the dict the model's ``emit`` tool would
    have returned; it is validated through ``output_model`` exactly like a
    live response so schema drift breaks fixtures loudly.
    """

    def __init__(self, settings: Settings, routes: dict[str, Any] | None = None) -> None:
        self._settings = settings
        self._routes = routes or {}

    async def generate_structured(
        self,
        task_name: str,
        system: str,
        user_content: str,
        output_model: type[BaseModel],
        routing_context: RoutingContext | None = None,
        prompt_name: str = "",
        prompt_version: str = "",
    ) -> ModelResult:
        started = time.monotonic()
        digest = hashlib.sha256(user_content.encode("utf-8")).hexdigest()[:16]
        base = Path(self._settings.fixtures_dir) / "model" / task_name
        candidates = [base / f"{digest}.json", base / "default.json"]
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            raise FixtureMissing(
                f"no model fixture for task {task_name!r}; looked for: "
                + ", ".join(str(p) for p in candidates)
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        try:
            output = output_model.model_validate(payload)
        except ValidationError as exc:
            raise ModelOutputInvalid(
                f"fixture {path} failed validation for task {task_name!r}: {exc}"
            ) from exc
        return ModelResult(
            output=output,
            model_id="fixture",
            task_name=task_name,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            latency_ms=int((time.monotonic() - started) * 1000),
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            repair_retry_used=False,
            escalated=False,
            cache_status="fixture",
        )


def build_gateway(
    mode: ExecutionMode, settings: Settings, config: AppConfig
) -> AnthropicGateway | FixtureGateway:
    """Gateway factory: ``fixture`` -> FixtureGateway; ``live``/``cached`` ->
    AnthropicGateway.

    Model-level response caching is handled elsewhere (a storage-layer wrapper
    that stamps ``cache_status='cached'`` on hits); any call that reaches the
    AnthropicGateway is a live API call and reports ``cache_status='live'``.
    """
    if mode == "fixture":
        return FixtureGateway(settings, config.model_routes)
    return AnthropicGateway(settings, config.model_routes)
