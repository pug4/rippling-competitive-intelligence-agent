"""SerpApi Google Ads Transparency Center path (preferred live Google-ads seam).

Real cited creatives via the SerpApi API become the PREFERRED live path; the
Exa web path stays as fallback. No network anywhere: the SerpApi HTTP seam
(``_serpapi_get``) is monkeypatched with canned LIST + DETAIL JSON matching the
live API shapes (probed once, out of band). Hermetic convention: the SerpApi
key is blanked with ``monkeypatch.setenv(NAME, "")`` — NEVER delenv — because
``secret_from_env_or_settings`` otherwise falls back to the real ``.env`` key.

Honesty boundary asserted here: VIDEO ad copy (headline/CTA/snippet) is a real
machine-readable API field; image/text creatives are rendered images whose copy
is NEVER invented (empty creative_text + an explicit note). No spend /
impressions / performance is ever emitted.
"""

from __future__ import annotations

from typing import Any

import httpx

from competitive_agent.config import AppConfig, FocalCompanyConfig, Settings
from competitive_agent.schemas.ad_intel import AdRecord
from competitive_agent.schemas.source import ResearchAction, ToolResult
from competitive_agent.tools.ads import (
    _SERPAPI_IMAGE_COPY_NOTE,
    _SERPAPI_MAX_DETAILS,
    SERPAPI_API_KEY_ENV,
    SERPAPI_FLAG_NAME,
    SERPAPI_SEARCH_URL,
    GoogleAdsTool,
)
from competitive_agent.tools.base import ToolContext

# Well-known UTC midnight epochs -> the ISO dates the records must carry.
TS_2024_01_01 = 1704067200  # -> 2024-01-01
TS_2024_06_01 = 1717200000  # -> 2024-06-01
TS_2023_12_01 = 1701388800  # -> 2023-12-01
TS_2024_05_01 = 1714521600  # -> 2024-05-01
TS_2024_06_02 = 1717286400  # -> 2024-06-02 (stray row, newest last_shown)

VIDEO_DETAIL_LINK = (
    "https://serpapi.com/search?engine=google_ads_transparency_center_ad_details"
    "&advertiser_id=AR13975028471928374650&creative_id=CR11111111111111"
)
IMAGE_DETAIL_LINK = (
    "https://serpapi.com/search?engine=google_ads_transparency_center_ad_details"
    "&advertiser_id=AR13975028471928374650&creative_id=CR22222222222222"
)
STRAY_DETAIL_LINK = (
    "https://serpapi.com/search?engine=google_ads_transparency_center_ad_details"
    "&advertiser_id=AR000000000000000001&creative_id=CR33333333333333"
)

VIDEO_HEADLINE = "Run global payroll in minutes"
VIDEO_CTA = "Get started"
VIDEO_SNIPPET = "Deel handles compliance, taxes, and payments in 150+ countries."
VIDEO_PERMALINK = (
    "https://adstransparency.google.com/advertiser/AR13975028471928374650"
    "/creative/CR11111111111111?region=anywhere"
)

LIST_BODY: dict[str, Any] = {
    "ad_creatives": [
        {
            "advertiser": "Deel, Inc.",
            "advertiser_id": "AR13975028471928374650",
            "ad_creative_id": "CR11111111111111",
            "format": "video",
            "first_shown": TS_2024_01_01,
            "last_shown": TS_2024_06_01,
            "total_days_shown": 152,
            "target_domain": "deel.com",
            "serpapi_details_link": VIDEO_DETAIL_LINK,
            "width": 1200,
            "height": 628,
        },
        {
            "advertiser": "Deel, Inc.",
            "advertiser_id": "AR13975028471928374650",
            "ad_creative_id": "CR22222222222222",
            "format": "image",
            "first_shown": TS_2023_12_01,
            "last_shown": TS_2024_05_01,
            "total_days_shown": 90,
            "target_domain": "deel.com",
            "image": "https://tpc.googlesyndication.com/archive/deel-image.png",
            "serpapi_details_link": IMAGE_DETAIL_LINK,
        },
        {
            # Stray advertiser: different advertiser AND target_domain. Its
            # last_shown is the newest so it survives sampling — the strict
            # scope filter is what must drop it.
            "advertiser": "Costco Wholesale",
            "advertiser_id": "AR000000000000000001",
            "ad_creative_id": "CR33333333333333",
            "format": "text",
            "first_shown": TS_2023_12_01,
            "last_shown": TS_2024_06_02,
            "total_days_shown": 30,
            "target_domain": "costco.com",
            "serpapi_details_link": STRAY_DETAIL_LINK,
        },
    ]
}

# Live shape (verified 2026-07-13): for VIDEO ads DETAIL's ``ad_creatives`` is a
# LIST of variants (the first is used); each carries the real machine-readable
# copy. The parser also accepts a bare dict.
VIDEO_DETAIL: dict[str, Any] = {
    "search_metadata": {
        "google_ads_transparency_center_ad_details_url": VIDEO_PERMALINK,
    },
    "ad_creatives": [
        {
            "headline": VIDEO_HEADLINE,
            "call_to_action": VIDEO_CTA,
            "snippet": VIDEO_SNIPPET,
            "visible_link": "deel.com",
            "video_link": "https://www.youtube.com/watch?v=abc123",
            "video_duration": "0:30",
            "link": "https://www.deel.com",
        },
        {"headline": "Second variant", "snippet": "ignored", "video_link": "https://x"},
    ],
}
IMAGE_DETAIL: dict[str, Any] = {
    "ad_creatives": [{"image": "https://tpc.googlesyndication.com/archive/deel-image.png"}]
}


class FakeRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record_tool_call(self, **kwargs: Any) -> None:
        self.records.append(kwargs)

    def find_cached_tool_call(self, tool_name: str, args_hash: str) -> Any | None:
        return None


def make_context(
    mode: str = "live", *, serpapi_flag: bool = True, exa_api_key: str = ""
) -> ToolContext:
    sources = {"google_ads": True, "meta_ads": True, "linkedin_ads": True}
    if serpapi_flag:
        sources[SERPAPI_FLAG_NAME] = True
    config = AppConfig(
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
    return ToolContext(
        run_id="RUN-test",
        company_id="deel",
        mode=mode,  # type: ignore[arg-type]
        config=config,
        settings=Settings(exa_api_key=exa_api_key),
        repository=FakeRepository(),
    )


def make_action(**params: Any) -> ResearchAction:
    return ResearchAction(
        action_id="ACT-test-search_google_ads",
        action_type="search_google_ads",
        company_id="deel",
        parameters=params,
    )


def install_serpapi(
    monkeypatch: Any,
    tool: GoogleAdsTool,
    list_body: dict[str, Any],
    detail_bodies: dict[str, dict[str, Any]] | None = None,
    default_detail: dict[str, Any] | None = None,
) -> list[tuple[str, dict[str, str]]]:
    """Patch the tool's SerpApi HTTP seam; returns the recorded (url, params) calls."""
    calls: list[tuple[str, dict[str, str]]] = []
    bodies = detail_bodies or {}

    async def fake_get(url: str, params: dict[str, str]) -> httpx.Response:
        calls.append((url, dict(params)))
        if url == SERPAPI_SEARCH_URL:
            return httpx.Response(200, json=list_body)
        body = bodies.get(
            url, default_detail if default_detail is not None else {"ad_creatives": {}}
        )
        return httpx.Response(200, json=body)

    monkeypatch.setattr(tool, "_serpapi_get", fake_get)
    return calls


def _detail_calls(calls: list[tuple[str, dict[str, str]]]) -> int:
    return sum(1 for url, _ in calls if url != SERPAPI_SEARCH_URL)


async def _run_path(
    monkeypatch: Any, list_body: dict[str, Any], **kw: Any
) -> tuple[ToolResult, list[tuple[str, dict[str, str]]]]:
    tool = GoogleAdsTool()
    calls = install_serpapi(
        monkeypatch,
        tool,
        list_body,
        detail_bodies={VIDEO_DETAIL_LINK: VIDEO_DETAIL, IMAGE_DETAIL_LINK: IMAGE_DETAIL},
        **kw,
    )
    result = await tool._serpapi_path(
        make_action(advertiser="Deel, Inc.", domain="deel.com"),
        make_context(),
        advertiser="Deel, Inc.",
        advertiser_domain="deel.com",
        api_key="test-serpapi-key",
    )
    return result, calls


# ---- video: real machine-readable copy + iso dates ---------------------------


async def test_video_record_gets_real_copy_and_iso_dates(monkeypatch: Any) -> None:
    result, calls = await _run_path(monkeypatch, LIST_BODY)
    videos = [
        a for a in result.artifacts if (a.metadata.get("ad_record") or {}).get("format") == "video"
    ]
    assert len(videos) == 1
    art = videos[0]
    record = AdRecord.model_validate(art.metadata["ad_record"])
    # Real API copy — headline/CTA/snippet, not fabricated, not paraphrased.
    assert record.headline == VIDEO_HEADLINE
    assert record.cta == VIDEO_CTA
    assert VIDEO_HEADLINE in record.creative_text
    assert VIDEO_SNIPPET in record.creative_text
    assert VIDEO_CTA in record.creative_text
    # Unix -> ISO dates.
    assert record.first_seen == "2024-01-01"
    assert record.last_seen == "2024-06-01"
    # The real Google Transparency permalink (from search_metadata when present).
    assert record.source_url == VIDEO_PERMALINK
    assert art.url == VIDEO_PERMALINK
    assert art.metadata["copy_machine_readable"] is True
    assert art.metadata["video_link"] == "https://www.youtube.com/watch?v=abc123"
    assert art.metadata["total_days_shown"] == 152
    assert art.metadata["advertiser_id"] == "AR13975028471928374650"
    # No spend/impressions ever.
    assert record.impression_bucket is None
    # 1 LIST + 2 DETAIL (video + image; the stray is dropped before its detail).
    assert _detail_calls(calls) == 2


# ---- image/text: empty copy + rendered-image note, never invented -------------


async def test_image_record_has_empty_copy_and_rendered_image_note(monkeypatch: Any) -> None:
    result, _ = await _run_path(monkeypatch, LIST_BODY)
    images = [
        a for a in result.artifacts if (a.metadata.get("ad_record") or {}).get("format") == "image"
    ]
    assert len(images) == 1
    art = images[0]
    record = AdRecord.model_validate(art.metadata["ad_record"])
    # Copy is baked into the rendered image -> never machine-readable, never invented.
    assert record.creative_text == ""
    assert record.headline is None
    assert record.cta is None
    assert art.metadata["copy_machine_readable"] is False
    assert art.metadata["creative_copy_note"] == _SERPAPI_IMAGE_COPY_NOTE
    assert "rendered image" in art.metadata["creative_copy_note"]
    assert art.metadata["image_url"] == "https://tpc.googlesyndication.com/archive/deel-image.png"
    # Dates still map; permalink built from advertiser_id + creative_id.
    assert record.first_seen == "2023-12-01"
    assert record.last_seen == "2024-05-01"
    assert record.source_url == (
        "https://adstransparency.google.com/advertiser/AR13975028471928374650"
        "/creative/CR22222222222222?region=anywhere"
    )


# ---- strict advertiser scoping drops the stray row ----------------------------


async def test_strict_scoping_drops_stray_advertiser(monkeypatch: Any) -> None:
    result, calls = await _run_path(monkeypatch, LIST_BODY)
    assert result.status == "partial"  # a dropped row is a disclosed degrade
    assert len(result.artifacts) == 2  # video + image only
    advertisers = {a.metadata["advertiser"] for a in result.artifacts}
    assert "Costco Wholesale" not in advertisers
    joined = " ".join(result.negative_observations)
    assert "advertiser scoping" in joined
    # The stray creative's DETAIL was never fetched (scope check precedes detail).
    assert all("CR33333333333333" not in url for url, _ in calls)


# ---- detail-fetch cap honored (<= 12) -----------------------------------------


async def test_detail_fetch_cap_is_honored(monkeypatch: Any) -> None:
    rows = []
    for i in range(15):
        rows.append(
            {
                "advertiser": "Deel, Inc.",
                "advertiser_id": "AR13975028471928374650",
                "ad_creative_id": f"CR{i:016d}",
                "format": "video",
                "first_shown": TS_2024_01_01,
                "last_shown": TS_2024_06_01 - i,  # distinct, all in the same window
                "total_days_shown": 10,
                "target_domain": "deel.com",
                "serpapi_details_link": f"https://serpapi.com/search?detail={i}",
            }
        )
    tool = GoogleAdsTool()
    calls = install_serpapi(
        monkeypatch,
        tool,
        {"ad_creatives": rows},
        default_detail={"ad_creatives": {"headline": "H", "call_to_action": "C", "snippet": "S"}},
    )
    result = await tool._serpapi_path(
        make_action(advertiser="Deel, Inc.", domain="deel.com"),
        make_context(),
        advertiser="Deel, Inc.",
        advertiser_domain="deel.com",
        api_key="test-serpapi-key",
    )
    assert _detail_calls(calls) <= _SERPAPI_MAX_DETAILS
    assert _detail_calls(calls) == _SERPAPI_MAX_DETAILS  # 15 in-scope -> capped at 12
    assert len(result.artifacts) <= _SERPAPI_MAX_DETAILS
    # The cap is disclosed honestly, with the call count.
    assert any("cap 12" in n for n in result.negative_observations)


# ---- error-JSON LIST (HTTP 200) -> typed empty --------------------------------


async def test_error_json_list_is_typed_empty(monkeypatch: Any) -> None:
    tool = GoogleAdsTool()
    install_serpapi(
        monkeypatch,
        tool,
        {"error": "Google Ads Transparency Center hasn't returned any results for this query."},
    )
    result = await tool._serpapi_path(
        make_action(advertiser="Deel, Inc.", domain="deel.com"),
        make_context(),
        advertiser="Deel, Inc.",
        advertiser_domain="deel.com",
        api_key="test-serpapi-key",
    )
    assert result.status == "empty"
    assert result.artifacts == []
    assert any("coverage gap" in n for n in result.negative_observations)


# ---- execute(): preferred path wiring + fallbacks -----------------------------


async def test_execute_prefers_serpapi_when_configured(monkeypatch: Any) -> None:
    monkeypatch.setenv(SERPAPI_API_KEY_ENV, "test-serpapi-key")
    tool = GoogleAdsTool()
    install_serpapi(
        monkeypatch,
        tool,
        LIST_BODY,
        detail_bodies={VIDEO_DETAIL_LINK: VIDEO_DETAIL, IMAGE_DETAIL_LINK: IMAGE_DETAIL},
    )
    result = await tool.execute(
        make_action(advertiser="Deel, Inc.", domain="deel.com"),
        make_context(exa_api_key=""),  # no Exa key: SerpApi must stand on its own
    )
    assert result.status == "partial"  # stray dropped
    assert len(result.artifacts) == 2
    assert all(a.source_type == "google_ads" for a in result.artifacts)
    assert all(a.collection_method == "serpapi_transparency" for a in result.artifacts)
    # is_discovery_pointer is never stamped -> is_junk_ads_artifact passes these.
    assert all("is_discovery_pointer" not in a.metadata for a in result.artifacts)


async def test_no_serpapi_key_skips_serpapi_and_falls_back_to_web(monkeypatch: Any) -> None:
    # Hermetic: blank the key (never delenv — .env would supply the real one).
    monkeypatch.setenv(SERPAPI_API_KEY_ENV, "")
    tool = GoogleAdsTool()

    called = {"serpapi": False, "web": False}

    async def boom_serpapi(*args: Any, **kwargs: Any) -> ToolResult:
        called["serpapi"] = True
        raise AssertionError("SerpApi path must not run without a key")

    async def fake_web_path(
        action: ResearchAction, context: ToolContext, **kwargs: Any
    ) -> ToolResult:
        called["web"] = True
        return tool._result(
            action, status="success", negative_observations=list(kwargs.get("pre_notes") or [])
        )

    monkeypatch.setattr(tool, "_serpapi_path", boom_serpapi)
    monkeypatch.setattr(tool, "_web_path", fake_web_path)
    result = await tool.execute(
        make_action(advertiser="Deel, Inc.", domain="deel.com"),
        make_context(exa_api_key="test-exa-key"),  # web path needs an Exa key
    )
    assert result.status == "success"
    assert called["web"] is True
    assert called["serpapi"] is False


async def test_serpapi_flag_off_skips_serpapi_even_with_key(monkeypatch: Any) -> None:
    """Fail-closed gate: with the flag absent the SerpApi seam is never touched,
    even when a real key and a domain are present (this is what keeps frozen
    unit tests from ever calling SerpApi live)."""
    monkeypatch.setenv(SERPAPI_API_KEY_ENV, "test-serpapi-key")
    tool = GoogleAdsTool()

    async def boom_serpapi(*args: Any, **kwargs: Any) -> ToolResult:
        raise AssertionError("SerpApi path must not run when the flag is off")

    web_called = {"n": 0}

    async def fake_web_path(
        action: ResearchAction, context: ToolContext, **kwargs: Any
    ) -> ToolResult:
        web_called["n"] += 1
        return tool._result(action, status="empty")

    monkeypatch.setattr(tool, "_serpapi_path", boom_serpapi)
    monkeypatch.setattr(tool, "_web_path", fake_web_path)
    await tool.execute(
        make_action(advertiser="Deel, Inc.", domain="deel.com"),
        make_context(serpapi_flag=False, exa_api_key="test-exa-key"),
    )
    assert web_called["n"] == 1


# ---- fixture mode never reaches the SerpApi seam (keyless) --------------------


async def test_fixture_mode_never_invokes_serpapi(monkeypatch: Any) -> None:
    tool = GoogleAdsTool()

    async def boom_get(url: str, params: dict[str, str]) -> httpx.Response:
        raise AssertionError("fixture mode must never reach the SerpApi HTTP seam")

    monkeypatch.setattr(tool, "_serpapi_get", boom_get)
    # No keys, no flag needed: BaseTool dispatches fixtures before _execute_live.
    result = await tool.execute(
        make_action(advertiser="Deel", domain="deel.com"),
        make_context(mode="fixture", serpapi_flag=False),
    )
    assert result.status == "success"
    assert len(result.artifacts) == 3
    for art in result.artifacts:
        assert art.is_fixture is True
        assert art.source_type == "google_ads"
        assert art.collection_method != "serpapi_transparency"


def test_serpapi_ad_records_distinct_by_creative_id_not_deduped():
    """Image/text ad creatives have EMPTY creative_text; hashing on that alone
    collapsed every one into a single artifact (12 collected -> 1 stored). Each
    real ad must persist as its own artifact, keyed on its unique creative id."""
    from competitive_agent.schemas.ad_intel import AdRecord
    from competitive_agent.tools.ads import _ad_record_artifact

    action = make_action()

    def _artifact(creative_id: str):
        rec = AdRecord(
            advertiser="Deel, Inc.",
            platform="google",
            creative_text="",  # image/text creative: copy is a rendered image
            format="image",
            regions=[],
            source_url=f"https://adstransparency.google.com/advertiser/AR1/creative/{creative_id}?region=anywhere",
            extraction_confidence="high",
        )
        return _ad_record_artifact(
            action,
            source_type="google_ads",
            platform_surface="google_ads_transparency",
            collection_method="serpapi_transparency",
            record=rec,
            provenance={"ad_creative_id": creative_id, "collection_method": "serpapi_transparency"},
        )

    a1 = _artifact("CR_AAA")
    a2 = _artifact("CR_BBB")
    a3 = _artifact("CR_AAA")  # same id -> same ad -> may dedup (correct)
    assert a1.content_hash != a2.content_hash, "distinct creatives must not collide"
    assert a1.content_hash == a3.content_hash, "the same creative id is the same ad"
    # And a real video creative (with copy) is distinct from the empty ones.
    from competitive_agent.tools.ads import _ad_record_artifact as _mk

    vid = AdRecord(
        advertiser="Deel, Inc.",
        platform="google",
        creative_text="Deel x Strada",
        headline="Deel x Strada",
        format="video",
        regions=[],
        source_url="https://adstransparency.google.com/advertiser/AR1/creative/CR_VID?region=anywhere",
        extraction_confidence="high",
    )
    av = _mk(
        action,
        source_type="google_ads",
        platform_surface="google_ads_transparency",
        collection_method="serpapi_transparency",
        record=vid,
        provenance={"ad_creative_id": "CR_VID", "collection_method": "serpapi_transparency"},
    )
    assert av.content_hash not in {a1.content_hash, a2.content_hash}
