"""Ad-intelligence dual path (ADS contract): strict advertiser scoping, typed
keyless degrades, creative-text containment, bounded next-query loop, fixture
records, and AdIntelligence schema round-trip.

No network and no live model calls anywhere: Exa and the Meta Graph API are
monkeypatched at the tool seams; model extraction uses the deterministic
FixtureGateway (tests/fixtures/model/ad_intelligence/default.json).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from competitive_agent.config import AppConfig, FocalCompanyConfig, Settings
from competitive_agent.model_gateway import FixtureGateway
from competitive_agent.schemas.ad_intel import AdIntelligence, AdRecord
from competitive_agent.schemas.source import ResearchAction
from competitive_agent.tools.ads import (
    EXA_SEARCH_URL,
    META_ADS_TOKEN_ENV,
    NO_META_TOKEN_NOTE,
    GoogleAdsTool,
    MetaAdsTool,
)
from competitive_agent.tools.base import ToolContext

# Creatives from tests/fixtures/model/ad_intelligence/default.json (verbatim).
FIXTURE_CREATIVE_1 = (
    "Run global payroll in minutes, not weeks. Deel handles compliance, "
    "taxes, and payments in 150+ countries."
)
FIXTURE_CREATIVE_2 = (
    "Hire anyone, anywhere. Deel's EOR hires your international team "
    "compliantly - no local entity required."
)

DEEL_ADVERTISER_URL = (
    "https://adstransparency.google.com/advertiser/AR13975028471928374650?region=US&domain=deel.com"
)


class FakeRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record_tool_call(self, **kwargs: Any) -> None:
        self.records.append(kwargs)

    def find_cached_tool_call(self, tool_name: str, args_hash: str) -> Any | None:
        return None


def make_context(
    mode: str = "live", exa_api_key: str = "test-exa-key", anthropic_api_key: str = ""
) -> ToolContext:
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
        company_id="deel",
        mode=mode,  # type: ignore[arg-type]
        config=config,
        settings=Settings(exa_api_key=exa_api_key, anthropic_api_key=anthropic_api_key),
        repository=FakeRepository(),
    )


def make_action(action_type: str, **params: Any) -> ResearchAction:
    return ResearchAction(
        action_id=f"ACT-test-{action_type}",
        action_type=action_type,
        company_id="deel",
        parameters=params,
    )


def exa_result(url: str, title: str, text: str) -> dict[str, Any]:
    return {"id": f"exa-{hash(url) & 0xFFFF}", "url": url, "title": title, "text": text}


def install_exa(
    monkeypatch: Any, tool: Any, responses_by_call: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Monkeypatch the tool's Exa seam; returns the recorded search calls."""
    calls: list[dict[str, Any]] = []

    async def fake_exa_post(url: str, payload: dict[str, Any], api_key: str) -> httpx.Response:
        if url == EXA_SEARCH_URL:
            calls.append(payload)
            index = len(calls) - 1
            body = responses_by_call[index] if index < len(responses_by_call) else {"results": []}
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={"results": []})  # exa_contents: nothing

    monkeypatch.setattr(tool, "_exa_post", fake_exa_post)
    return calls


# ---- schema ------------------------------------------------------------------


def test_ad_intelligence_schema_round_trips() -> None:
    record = AdRecord(
        advertiser="Deel",
        platform="google",
        creative_text="Run payroll everywhere.",
        headline="Global payroll",
        regions=["US", "GB"],
        first_seen="2026-04-01",
        active=True,
        impression_bucket="10K-15K",
        source_url="https://adstransparency.google.com/advertiser/AR1/creative/CR1",
        extraction_confidence="high",
    )
    intel = AdIntelligence(
        ads=[record],
        campaign_themes=["global payroll"],
        implied_search_intents=["global payroll software"],
        next_queries=["Deel ad library"],
        notes="ok",
    )
    round_tripped = AdIntelligence.model_validate_json(intel.model_dump_json())
    assert round_tripped == intel
    assert round_tripped.ads[0].impression_bucket == "10K-15K"


def test_ad_record_forbids_performance_fields() -> None:
    # extra=forbid: fields like cpc/spend can never ride along silently.
    with pytest.raises(ValidationError):
        AdRecord.model_validate(
            {
                "advertiser": "Deel",
                "platform": "google",
                "creative_text": "x",
                "source_url": "https://example.com",
                "extraction_confidence": "high",
                "cpc": 1.25,
            }
        )


# ---- strict advertiser scoping (web path) --------------------------------------


async def test_advertiser_scope_filter_drops_mismatched_advertisers(monkeypatch: Any) -> None:
    """The Costco / Electoral-Commission junk class: results not matching the
    competitor domain or advertiser name are dropped and counted."""
    tool = GoogleAdsTool()
    monkeypatch.setattr(tool, "_build_gateway", lambda ctx: None)  # discovery only
    install_exa(
        monkeypatch,
        tool,
        [
            {
                "results": [
                    exa_result(
                        DEEL_ADVERTISER_URL,
                        "Deel - Ads Transparency Center",
                        "Ads by Deel. Global payroll and EOR advertising.",
                    ),
                    # Explicit other-advertiser page: killed by the junk rule
                    # (domain query param mismatch).
                    exa_result(
                        "https://adstransparency.google.com/advertiser/AR000001?domain=costco.com",
                        "Costco Wholesale - Ads Transparency Center",
                        "Ads by Costco Wholesale.",
                    ),
                    # Advertiser page with no domain param and no competitor
                    # mention: killed by the strict scope filter.
                    exa_result(
                        "https://adstransparency.google.com/advertiser/AR000002",
                        "Electoral Commission - Ads Transparency Center",
                        "Ads by the Electoral Commission about voting.",
                    ),
                ]
            }
        ],
    )
    result = await tool.execute(
        make_action("search_google_ads", advertiser="Deel", domain="deel.com"),
        make_context(),
    )
    assert result.status == "partial"
    assert len(result.artifacts) == 1
    assert result.artifacts[0].url == DEEL_ADVERTISER_URL
    assert result.artifacts[0].metadata["is_discovery_pointer"] is True
    joined = " ".join(result.negative_observations)
    assert "advertiser scoping" in joined
    assert "non-advertiser pages" in joined  # the junk-rule drop is also counted


# ---- extraction: containment + full record persisted ----------------------------


async def test_creative_containment_keeps_verbatim_and_drops_paraphrase(
    monkeypatch: Any,
) -> None:
    """The fixture model output has two creatives; the page text contains only
    the first verbatim — the second must be dropped and logged."""
    tool = GoogleAdsTool()
    settings = Settings(exa_api_key="test-exa-key", anthropic_api_key="")
    monkeypatch.setattr(tool, "_build_gateway", lambda ctx: FixtureGateway(settings))
    calls = install_exa(
        monkeypatch,
        tool,
        [
            {
                "results": [
                    exa_result(
                        DEEL_ADVERTISER_URL,
                        "Deel - Ads Transparency Center",
                        f"Ads by Deel.\n{FIXTURE_CREATIVE_1}\nFormat: responsive search ad.",
                    )
                ]
            },
            {"results": []},  # the follow-up next_query search finds nothing new
        ],
    )
    result = await tool.execute(
        make_action("search_google_ads", advertiser="Deel", domain="deel.com"),
        make_context(),
    )
    records = [a for a in result.artifacts if "ad_record" in a.metadata]
    pointers = [a for a in result.artifacts if a.metadata.get("is_discovery_pointer")]
    assert len(pointers) == 1
    assert len(records) == 1  # creative 2 failed containment and was dropped
    record_meta = records[0].metadata
    assert record_meta["creative_body"] == FIXTURE_CREATIVE_1
    # The FULL AdRecord rides in metadata and still validates.
    parsed = AdRecord.model_validate(record_meta["ad_record"])
    assert parsed.creative_text == FIXTURE_CREATIVE_1
    assert parsed.advertiser == "Deel"
    # Creative rows are NOT discovery pointers (junk filter must pass them).
    assert "is_discovery_pointer" not in record_meta
    # The drop was logged, not silent.
    assert any(
        "containment" in n or "not found verbatim" in n for n in result.negative_observations
    )
    # The TOOL ran the model-proposed follow-up query.
    assert len(calls) == 2
    assert calls[1]["query"] == "Deel advertiser page adstransparency.google.com creatives"
    assert result.status == "partial"  # a dropped record is a disclosed degrade


async def test_next_query_loop_is_bounded_to_three_follow_ups(monkeypatch: Any) -> None:
    """Even if extraction keeps proposing fresh queries, the TOOL stops after
    the initial search + 3 next-query iterations."""
    tool = GoogleAdsTool()
    monkeypatch.setattr(tool, "_build_gateway", lambda ctx: object())  # extraction on

    extract_calls = {"n": 0}

    async def fake_extract(
        gateway: Any, prompt: Any, **kwargs: Any
    ) -> tuple[AdIntelligence, float]:
        extract_calls["n"] += 1
        return (
            AdIntelligence(ads=[], next_queries=[f"deel follow-up {extract_calls['n']}"]),
            0.0,
        )

    monkeypatch.setattr(tool, "_extract", fake_extract)

    def page(n: int) -> dict[str, Any]:
        return {
            "results": [
                exa_result(
                    f"https://adstransparency.google.com/advertiser/AR10000000{n}?domain=deel.com",
                    f"Deel - Ads Transparency Center {n}",
                    "Ads by Deel.",
                )
            ]
        }

    calls = install_exa(monkeypatch, tool, [page(1), page(2), page(3), page(4), page(5)])
    result = await tool.execute(
        make_action("search_google_ads", advertiser="Deel", domain="deel.com"),
        make_context(),
    )
    assert len(calls) == 4  # 1 initial + at most 3 next-query iterations
    assert result.status in ("partial", "success")


# ---- defensive provenance repair (platform / source_url) ---------------------------


def make_record(**overrides: Any) -> AdRecord:
    base: dict[str, Any] = {
        "advertiser": "Deel",
        "platform": "google",
        "creative_text": FIXTURE_CREATIVE_1,
        "source_url": DEEL_ADVERTISER_URL,
        "extraction_confidence": "high",
    }
    base.update(overrides)
    return AdRecord.model_validate(base)


async def run_google_extraction(
    monkeypatch: Any, intelligence: AdIntelligence, page_text: str
) -> Any:
    """Run the Google web path with a faked extraction result over one page."""
    tool = GoogleAdsTool()
    monkeypatch.setattr(tool, "_build_gateway", lambda ctx: object())  # extraction on

    async def fake_extract(
        gateway: Any, prompt: Any, **kwargs: Any
    ) -> tuple[AdIntelligence, float]:
        return intelligence, 0.0

    monkeypatch.setattr(tool, "_extract", fake_extract)
    install_exa(
        monkeypatch,
        tool,
        [
            {
                "results": [
                    exa_result(DEEL_ADVERTISER_URL, "Deel - Ads Transparency Center", page_text)
                ]
            }
        ],
    )
    return await tool.execute(
        make_action("search_google_ads", advertiser="Deel", domain="deel.com"),
        make_context(),
    )


async def test_blank_or_invalid_source_url_corrected_to_extraction_page(
    monkeypatch: Any,
) -> None:
    """A record is never dropped for a blank/invalid source_url alone: the
    persisted artifact carries the extraction page URL, and the correction is
    counted in the notes."""
    creative_blank = "Deel runs payroll in 150+ countries."
    creative_invalid = "Hire globally with Deel EOR today."
    intelligence = AdIntelligence(
        ads=[
            make_record(creative_text=creative_blank, source_url=""),
            make_record(creative_text=creative_invalid, source_url="not-a-url"),
        ]
    )
    result = await run_google_extraction(
        monkeypatch,
        intelligence,
        f"Ads by Deel.\n{creative_blank}\n{creative_invalid}\n",
    )
    records = [a for a in result.artifacts if "ad_record" in a.metadata]
    assert len(records) == 2  # never dropped for these fields alone
    for artifact in records:
        assert artifact.url == DEEL_ADVERTISER_URL  # canonical URL = extraction page
        parsed = AdRecord.model_validate(artifact.metadata["ad_record"])
        assert parsed.source_url == DEEL_ADVERTISER_URL
    assert any(
        "2 record(s) had platform/source_url corrected to the extraction page" in n
        for n in result.negative_observations
    )


async def test_wrong_platform_record_corrected_to_tool_platform(monkeypatch: Any) -> None:
    creative = "Deel handles compliance and taxes worldwide."
    intelligence = AdIntelligence(
        ads=[make_record(creative_text=creative, platform="meta")]  # wrong library
    )
    result = await run_google_extraction(monkeypatch, intelligence, f"Ads by Deel.\n{creative}\n")
    records = [a for a in result.artifacts if "ad_record" in a.metadata]
    assert len(records) == 1  # never dropped for platform alone
    parsed = AdRecord.model_validate(records[0].metadata["ad_record"])
    assert parsed.platform == "google"  # the tool knows which library it searched
    assert records[0].url == DEEL_ADVERTISER_URL  # valid source_url untouched
    assert any(
        "1 record(s) had platform/source_url corrected to the extraction page" in n
        for n in result.negative_observations
    )


async def test_valid_record_provenance_untouched(monkeypatch: Any) -> None:
    """A sound record keeps its own source_url (a creative permalink is NOT
    overwritten with the extraction page) and no correction note appears."""
    creative = "Run global payroll in minutes with Deel."
    permalink = "https://adstransparency.google.com/advertiser/AR13975028471928374650/creative/CR42"
    intelligence = AdIntelligence(ads=[make_record(creative_text=creative, source_url=permalink)])
    result = await run_google_extraction(monkeypatch, intelligence, f"Ads by Deel.\n{creative}\n")
    records = [a for a in result.artifacts if "ad_record" in a.metadata]
    assert len(records) == 1
    assert records[0].url == permalink
    parsed = AdRecord.model_validate(records[0].metadata["ad_record"])
    assert parsed.source_url == permalink
    assert parsed.platform == "google"
    assert not any("corrected to the extraction page" in n for n in result.negative_observations)
    assert result.status == "success"  # nothing dropped, nothing corrected


def test_repair_record_provenance_helper() -> None:
    from competitive_agent.tools.ads import repair_record_provenance

    page_url = DEEL_ADVERTISER_URL
    # Non-http(s) scheme and scheme-only URLs are invalid -> extraction page.
    for bad in ("", "   ", "not-a-url", "ftp://example.com/x", "https://"):
        repaired, corrected = repair_record_provenance(
            make_record(source_url=bad), platform="google", page_url=page_url
        )
        assert corrected is True
        assert repaired.source_url == page_url
    # Both fields repaired at once.
    repaired, corrected = repair_record_provenance(
        make_record(source_url="", platform="other"), platform="google", page_url=page_url
    )
    assert corrected is True
    assert repaired.source_url == page_url
    assert repaired.platform == "google"
    # A sound record comes back untouched.
    sound = make_record()
    repaired, corrected = repair_record_provenance(sound, platform="google", page_url=page_url)
    assert corrected is False
    assert repaired == sound


# ---- Meta: typed keyless degrade + official-API seam ------------------------------


async def test_keyless_meta_degrades_typed_with_exact_reason(monkeypatch: Any) -> None:
    monkeypatch.setenv(META_ADS_TOKEN_ENV, "")
    result = await MetaAdsTool().execute(
        make_action("search_meta_ads", advertiser="Deel", domain="deel.com"),
        make_context(exa_api_key=""),
    )
    assert result.status == "unsupported"
    assert result.error_type == "provider_not_configured"
    assert result.artifacts == []
    assert NO_META_TOKEN_NOTE in result.negative_observations
    assert result.negative_observations  # coverage gap disclosed, never silent


async def test_meta_official_api_maps_records_and_scopes_advertiser(monkeypatch: Any) -> None:
    monkeypatch.setenv(META_ADS_TOKEN_ENV, "test-token-abc123")
    tool = MetaAdsTool()
    seen_params: dict[str, str] = {}

    async def fake_meta_get(params: dict[str, str]) -> httpx.Response:
        seen_params.update(params)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "123",
                        "page_name": "Deel",
                        "ad_creative_bodies": ["Global hiring made simple with Deel."],
                        "ad_creative_link_titles": ["Hire globally"],
                        "publisher_platforms": ["facebook", "instagram"],
                        "ad_delivery_start_time": "2026-05-01",
                        "impressions": {"lower_bound": "10000", "upper_bound": "14999"},
                    },
                    {
                        "id": "456",
                        "page_name": "Costco Wholesale",
                        "ad_creative_bodies": ["Membership deals this week."],
                        "publisher_platforms": ["facebook"],
                        "ad_delivery_start_time": "2026-05-02",
                    },
                ]
            },
        )

    monkeypatch.setattr(tool, "_meta_get", fake_meta_get)
    result = await tool.execute(
        make_action("search_meta_ads", advertiser="Deel", domain="deel.com"),
        make_context(exa_api_key=""),  # API path must not need Exa
    )
    assert seen_params["search_terms"] == "Deel"
    assert result.status == "partial"  # the out-of-scope record was dropped + counted
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    assert artifact.metadata["advertiser"] == "Deel"
    # Impression BUCKET (claimable) exactly as the library shows it.
    assert artifact.metadata["impression_bucket"] == "10000-14999"
    record = AdRecord.model_validate(artifact.metadata["ad_record"])
    assert record.platform == "meta"
    assert record.active is True  # start shown, no delivery stop time
    # Public permalink, never ad_snapshot_url: the token must not leak anywhere.
    assert artifact.url == "https://www.facebook.com/ads/library/?id=123"
    blob = json.dumps(artifact.metadata) + artifact.url + " ".join(result.negative_observations)
    assert "test-token-abc123" not in blob
    assert any("advertiser scoping" in n for n in result.negative_observations)


async def test_meta_api_failure_falls_back_and_degrades_typed(monkeypatch: Any) -> None:
    monkeypatch.setenv(META_ADS_TOKEN_ENV, "test-token-abc123")
    tool = MetaAdsTool()

    async def fake_meta_get(params: dict[str, str]) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    monkeypatch.setattr(tool, "_meta_get", fake_meta_get)
    # No Exa key either: the web-path fallback is honestly unsupported.
    result = await tool.execute(
        make_action("search_meta_ads", advertiser="Deel", domain="deel.com"),
        make_context(exa_api_key=""),
    )
    assert result.status == "unsupported"
    assert result.artifacts == []
    joined = " ".join(result.negative_observations)
    assert "falling back to public-web discovery" in joined


# ---- fixture mode -----------------------------------------------------------------


async def test_fixture_mode_returns_deel_google_records() -> None:
    result = await GoogleAdsTool().execute(
        make_action("search_google_ads", advertiser="Deel", domain="deel.com"),
        make_context(mode="fixture"),
    )
    assert result.status == "success"
    assert len(result.artifacts) == 3
    for artifact in result.artifacts:
        assert artifact.is_fixture is True
        assert artifact.source_type == "google_ads"
        assert artifact.metadata["advertiser"] == "Deel"
        record = AdRecord.model_validate(artifact.metadata["ad_record"])
        assert record.platform == "google"
        assert record.creative_text  # a real creative, never empty


async def test_fixture_mode_returns_deel_meta_records() -> None:
    result = await MetaAdsTool().execute(
        make_action("search_meta_ads", advertiser="Deel", domain="deel.com"),
        make_context(mode="fixture"),
    )
    assert result.status == "success"
    assert len(result.artifacts) == 3
    for artifact in result.artifacts:
        assert artifact.source_type == "meta_ads"
        record = AdRecord.model_validate(artifact.metadata["ad_record"])
        assert record.platform == "meta"
        # EU impression buckets are ranges, never precise counts.
        if record.impression_bucket is not None:
            assert "-" in record.impression_bucket


def test_model_fixture_validates_against_schema() -> None:
    """tests/fixtures/model/ad_intelligence/default.json must stay loadable by
    the FixtureGateway (schema drift should break loudly here)."""
    path = Settings().fixtures_dir / "model" / "ad_intelligence" / "default.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    intel = AdIntelligence.model_validate(payload)
    assert len(intel.ads) == 2
    assert intel.ads[0].creative_text == FIXTURE_CREATIVE_1
    assert intel.ads[1].creative_text == FIXTURE_CREATIVE_2
    assert 0 < len(intel.next_queries) <= 3


def test_meta_active_status_future_scheduled_stop_is_unknown():
    """A future scheduled stop must never be claimed as 'inactive' (the ad may
    still be delivering); only a verifiably-past stop proves ended."""
    from competitive_agent.tools.ads import _meta_active_status

    assert _meta_active_status("2026-01-01", None) is True
    assert _meta_active_status("2026-01-01", "2020-06-01") is False
    assert _meta_active_status("2026-01-01", "2999-01-01") is None  # future: unknown
    assert _meta_active_status("2026-01-01", "not-a-date") is None
    assert _meta_active_status(None, None) is None
    assert _meta_active_status(None, "2020-06-01") is None
