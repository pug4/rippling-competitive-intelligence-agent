"""Industry adaptivity: infer_industry_context parses a model IndustryContext
into a dict, degrades to a typed honest fallback on ANY gateway error (never
fabricates an industry), and its prompt renders under StrictUndefined.

No network, no live model call: the gateway is either the deterministic
FixtureGateway (tests/fixtures/model/infer_industry_context/default.json) or a
monkeypatched stub.
"""

from __future__ import annotations

from typing import Any

import pytest
from jinja2 import UndefinedError

from competitive_agent.config import Settings
from competitive_agent.industry import (
    INDUSTRY_PROMPT_NAME,
    IndustryContext,
    infer_industry_context,
)
from competitive_agent.model_gateway import FixtureGateway
from competitive_agent.prompt_registry import PromptRegistry

THEME_SAMPLE = ["continuous compliance", "SOC 2 automation", "trust center"]
MESSAGE_SAMPLE = ["Get audit-ready in weeks, not months.", "Automate evidence collection."]


class _Result:
    def __init__(self, output: Any) -> None:
        self.output = output


class ReturningGateway:
    """Returns a fixed IndustryContext; records the rendered user_content."""

    def __init__(self, output: IndustryContext) -> None:
        self._output = output
        self.seen_user_content: str | None = None
        self.seen_task: str | None = None
        self.seen_model: Any = None

    async def generate_structured(
        self,
        task_name: str,
        system: str,
        user_content: str,
        output_model: Any,
        prompt_name: str = "",
        prompt_version: str = "",
    ) -> _Result:
        self.seen_user_content = user_content
        self.seen_task = task_name
        self.seen_model = output_model
        return _Result(self._output)


class RaisingGateway:
    async def generate_structured(self, *args: Any, **kwargs: Any) -> _Result:
        raise RuntimeError("model provider unavailable")


# ---- parse a model IndustryContext into a dict -------------------------------


async def test_returns_parsed_dict_from_gateway() -> None:
    output = IndustryContext(
        industry="security & compliance automation",
        sub_category="SOC 2 continuous compliance",
        key_terminology=["continuous compliance", "SOC 2"],
        primary_buyer_personas=["CISO", "Compliance Manager"],
        how_focal_competes_here="Based on the focal company's platform positioning, ...",
        positioning_frame="Own the access-evidence angle.",
    )
    gateway = ReturningGateway(output)
    result = await infer_industry_context(
        "Vanta", THEME_SAMPLE, MESSAGE_SAMPLE, gateway, PromptRegistry()
    )
    assert isinstance(result, dict)
    assert result["industry"] == "security & compliance automation"
    assert result["key_terminology"] == ["continuous compliance", "SOC 2"]
    assert result["primary_buyer_personas"] == ["CISO", "Compliance Manager"]
    # The correct task + output model were routed.
    assert gateway.seen_task == "infer_industry_context"
    assert gateway.seen_model is IndustryContext
    # The competitor sample rode into the (untrusted-fenced) prompt.
    assert gateway.seen_user_content is not None
    assert "continuous compliance" in gateway.seen_user_content
    assert "audit-ready" in gateway.seen_user_content
    assert "<untrusted_source_content>" in gateway.seen_user_content
    assert "Vanta" in gateway.seen_user_content


# ---- typed honest fallback on ANY error (never fabricates) -------------------


async def test_gateway_error_returns_typed_fallback() -> None:
    result = await infer_industry_context(
        "Vanta", THEME_SAMPLE, MESSAGE_SAMPLE, RaisingGateway(), PromptRegistry()
    )
    assert result == {"industry": None, "note": "industry inference unavailable"}


async def test_missing_prompt_returns_typed_fallback() -> None:
    class NoPromptRegistry:
        def get(self, name: str) -> Any:
            raise KeyError(name)

    result = await infer_industry_context(
        "Vanta", THEME_SAMPLE, MESSAGE_SAMPLE, RaisingGateway(), NoPromptRegistry()
    )
    assert result == {"industry": None, "note": "industry inference unavailable"}


# ---- keyless fixture-gateway path works end to end ---------------------------


async def test_fixture_gateway_end_to_end_generic_competitor() -> None:
    gateway = FixtureGateway(Settings())
    result = await infer_industry_context(
        "Vanta", THEME_SAMPLE, MESSAGE_SAMPLE, gateway, PromptRegistry()
    )
    # The generic fixture validates through IndustryContext and returns a dict.
    assert result["industry"] == "security & compliance automation"
    assert "SOC 2" in result["key_terminology"]
    IndustryContext.model_validate(result)  # round-trips


# ---- prompt renders under StrictUndefined ------------------------------------


def test_prompt_renders_with_strict_undefined_given_sample() -> None:
    prompt = PromptRegistry().get(INDUSTRY_PROMPT_NAME)
    rendered = prompt.render(
        company_name="Vanta",
        focal_company="Rippling",
        competitor_themes="- continuous compliance\n- SOC 2 automation",
        competitor_messages="- Get audit-ready in weeks.",
    )
    assert "Vanta" in rendered
    assert "Rippling" in rendered
    assert "continuous compliance" in rendered
    assert "<untrusted_source_content>" in rendered


def test_prompt_missing_variable_is_strict_error() -> None:
    prompt = PromptRegistry().get(INDUSTRY_PROMPT_NAME)
    # StrictUndefined: an unfilled variable is a hard error, never silent.
    with pytest.raises(UndefinedError):
        prompt.render(company_name="Vanta")


def test_industry_context_is_missing_tolerant() -> None:
    # Every field defaulted: an empty model still validates.
    ctx = IndustryContext()
    assert ctx.industry is None
    assert ctx.key_terminology == []
    assert ctx.primary_buyer_personas == []
