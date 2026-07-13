"""Unit tests for the advertising adapters (Google / Meta / LinkedIn).

Coverage-honesty contract (blueprint §37.12, §39.7):
- Meta / LinkedIn live collection degrades to ``unsupported`` (no general
  commercial-ad API exists).
- Google live discovery with no Exa key degrades to ``unsupported``.
- Fixture mode returns observed creatives carrying the normalized public-ad
  shape with NO performance / spend fields.
- capabilities().known_limitations spell out the §39.7 coverage reality.
"""

from __future__ import annotations

from typing import Any

from competitive_agent.config import AppConfig, FocalCompanyConfig, Settings
from competitive_agent.schemas.source import ResearchAction
from competitive_agent.tools.ads import (
    PUBLIC_AD_METADATA_KEYS,
    GoogleAdsTool,
    LinkedInAdsTool,
    MetaAdsTool,
)
from competitive_agent.tools.base import ToolContext

# Fields that ad libraries never expose for commercial ads and this system must
# never claim or fabricate (§37.12 do_not_claim, §39.7).
FORBIDDEN_METADATA_KEYS = {
    "roas",
    "cpa",
    "conversion_rate",
    "conversions",
    "revenue",
    "spend",
    "true_spend",
    "cost",
    "impressions",
    "reach",
    "frequency",
    "ctr",
    "cpc",
    "cpm",
    "winning_creative",
    "delivery",
    "bid_keywords",
}


class FakeRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record_tool_call(self, **kwargs: Any) -> None:
        self.records.append(kwargs)

    def find_cached_tool_call(self, tool_name: str, args_hash: str) -> Any | None:
        return None


class NoRetryGoogleAdsTool(GoogleAdsTool):
    max_live_retries = 0
    retry_base_delay = 0.0


def make_context(mode: str = "live", exa_api_key: str = "") -> ToolContext:
    config = AppConfig(
        focal_company=FocalCompanyConfig(),
        sources={"google_ads": True, "meta_ads": True, "linkedin_ads": True},
        execution={},
        budgets={},
        portfolio={},
        windows={},
        taxonomy={},
        model_routes={},
        source_capabilities={},
    )
    return ToolContext(
        run_id="RUN-test",
        company_id="example-hr",
        mode=mode,  # type: ignore[arg-type]
        config=config,
        settings=Settings(exa_api_key=exa_api_key),
        repository=FakeRepository(),
    )


def make_action(action_type: str, **params: Any) -> ResearchAction:
    return ResearchAction(
        action_id=f"ACT-test-{action_type}",
        action_type=action_type,
        company_id="example-hr",
        parameters=params,
    )


def _assert_no_performance_fields(metadata: dict[str, Any]) -> None:
    keys = {str(k).lower() for k in metadata}
    leaked = keys & FORBIDDEN_METADATA_KEYS
    assert not leaked, f"performance/spend fields leaked into ad metadata: {leaked}"


# ---- Meta / LinkedIn: keyless live paths degrade typed ------------------------


async def test_meta_live_without_any_key_degrades_typed(monkeypatch: Any) -> None:
    """Dual-path Meta (ADS contract): no META_ADS_ACCESS_TOKEN and no Exa key
    -> a typed unsupported result recording the exact honest skip reason."""
    monkeypatch.setenv("META_ADS_ACCESS_TOKEN", "")
    result = await MetaAdsTool().execute(
        make_action("search_meta_ads", advertiser="Example HR"), make_context()
    )
    assert result.status == "unsupported"
    assert result.artifacts == []
    assert result.error_type == "provider_not_configured"
    assert "no META_ADS_ACCESS_TOKEN — using public-web path" in result.negative_observations
    assert result.negative_observations  # coverage gap disclosed, not a silent drop


async def test_linkedin_live_is_unsupported_best_effort() -> None:
    result = await LinkedInAdsTool().execute(
        make_action("search_linkedin_ads", advertiser="Example HR"), make_context()
    )
    assert result.status == "unsupported"
    assert result.artifacts == []
    assert result.error_type == "interface_only"
    assert "no stable" in (result.error_message or "").lower()
    assert result.negative_observations


# ---- Google: best-effort discovery needs a key; none -> unsupported ----------


async def test_google_live_without_exa_key_is_unsupported() -> None:
    result = await NoRetryGoogleAdsTool().execute(
        make_action("search_google_ads", advertiser="Example HR"),
        make_context(exa_api_key=""),
    )
    assert result.status == "unsupported"
    assert result.artifacts == []
    assert result.error_type == "provider_not_configured"
    assert result.negative_observations


# ---- Fixture mode: observed creatives, normalized shape, no performance ------


async def test_google_fixture_returns_observed_creatives_no_performance() -> None:
    result = await GoogleAdsTool().execute(
        make_action("search_google_ads", advertiser="Example HR"),
        make_context(mode="fixture"),
    )
    assert result.status == "success"
    assert result.tool_name == "google_ads"
    assert result.action_id == "ACT-test-search_google_ads"
    assert len(result.artifacts) == 3
    for artifact in result.artifacts:
        assert artifact.is_fixture is True
        assert artifact.company_id == "example-hr"
        assert artifact.source_type == "google_ads"
        assert "adstransparency.google.com" in artifact.url
        # Normalized public-ad shape present in metadata (§37.12).
        for key in PUBLIC_AD_METADATA_KEYS:
            assert key in artifact.metadata
        _assert_no_performance_fields(artifact.metadata)
        # creative_hash is a hash of the creative body, not a spend figure.
        assert artifact.metadata["creative_hash"]


async def test_meta_fixture_returns_platform_labels_no_spend() -> None:
    result = await MetaAdsTool().execute(
        make_action("search_meta_ads", advertiser="Example HR"),
        make_context(mode="fixture"),
    )
    assert result.status == "success"
    assert result.tool_name == "meta_ads"
    assert len(result.artifacts) == 3
    for artifact in result.artifacts:
        assert artifact.is_fixture is True
        assert artifact.source_type == "meta_ads"
        assert artifact.metadata["platform_or_surface"] == "meta_ad_library"
        # Publisher platforms are observed presence labels only.
        assert isinstance(artifact.metadata["publisher_platforms"], list)
        assert artifact.metadata["publisher_platforms"]
        for key in PUBLIC_AD_METADATA_KEYS:
            assert key in artifact.metadata
        _assert_no_performance_fields(artifact.metadata)


# ---- capabilities: known_limitations name the §39.7 coverage reality ---------


def test_google_capabilities_disclose_repository_reality() -> None:
    caps = GoogleAdsTool().capabilities()
    assert caps.returns_estimates is False
    joined = " ".join(caps.known_limitations).lower()
    assert "creative repository" in joined
    assert "no stable public api" in joined
    assert "bid" in joined  # no bid-keyword conclusions


def test_meta_capabilities_disclose_ui_only_reality() -> None:
    caps = MetaAdsTool().capabilities()
    assert caps.returns_estimates is False
    # Dual path (ADS contract): live is available via ads_archive (token) or
    # advertiser-scoped public-web discovery — but the coverage limits stay.
    assert caps.live_available is True
    joined = " ".join(caps.known_limitations).lower()
    assert "political" in joined and ("ui" in joined or "interface" in joined)
    assert "not spend" in joined or "not spend or delivery" in joined


def test_linkedin_capabilities_disclose_best_effort_reality() -> None:
    caps = LinkedInAdsTool().capabilities()
    assert caps.returns_estimates is False
    assert caps.live_available is False
    joined = " ".join(caps.known_limitations).lower()
    assert "no stable api" in joined
    assert "report-critical" in joined
