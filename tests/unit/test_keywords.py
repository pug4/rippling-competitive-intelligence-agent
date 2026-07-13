"""Keyword-intelligence tests (KEYWORDS contract + Gemini SERP addendum).

No network anywhere: the Semrush and Gemini HTTP layers are monkeypatched
fake clients, paid-search enrichment uses injected fake providers, and every
no-provider path is pinned to degrade typed (never fabricate). Model calls
run in fixture mode with zero API keys — the developer's REAL Gemini key in
.env must never be reachable from a test.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from competitive_agent.config import AppConfig, FocalCompanyConfig, Settings
from competitive_agent.schemas.common import utcnow
from competitive_agent.schemas.keywords import KeywordMetric, SerpIntel
from competitive_agent.schemas.source import ResearchAction
from competitive_agent.tools import keywords as keywords_module
from competitive_agent.tools.base import ToolContext
from competitive_agent.tools.keywords import (
    GEMINI_BILLING_MESSAGE,
    MAX_KEYWORDS_PER_BATCH,
    MAX_SERP_CALLS_PER_BATCH,
    NO_PROVIDER_MESSAGE,
    GeminiSerpProvider,
    GeminiSerpQuotaError,
    KeywordProviderError,
    KeywordsTool,
    NoKeywordProvider,
    SemrushProvider,
    active_keyword_provider,
    active_serp_provider,
    fetch_keyword_metrics,
    fetch_serp_intel,
    parse_semrush_csv,
    parse_serp_json,
    resolve_keyword_provider,
)

# ---------------------------------------------------------------------------
# shared fakes / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_keyword_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with NO provider configured (opt in explicitly).

    The developer's REAL Gemini key lives in .env — it must never leak into a
    test run (no live Gemini calls, ever)."""
    # Present-but-EMPTY means "explicitly disabled" to the secret helper —
    # a deleted variable would fall back to the .env-backed Settings field,
    # which on a dev machine carries the user's REAL keys.
    monkeypatch.setenv("SEMRUSH_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_MODEL", "")


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("EXA_API_KEY", "")
    from competitive_agent import config as config_mod

    config_mod.reset_config_cache()
    settings = config_mod.get_settings()
    monkeypatch.setattr(settings, "db_path", tmp_path / "agent.db")
    monkeypatch.setattr(settings, "outputs_dir", tmp_path / "outputs")
    yield tmp_path
    config_mod.reset_config_cache()


class FakeRepository:
    def record_tool_call(self, **record: Any) -> None:
        pass

    def find_cached_tool_call(self, tool_name: str, args_hash: str) -> Any | None:
        return None


class FakeHttpClient:
    """Stand-in for ``httpx.Client``: canned Semrush CSV bodies per phrase."""

    responses: dict[str, str] = {}
    default_body: str = "ERROR 50 :: NOTHING FOUND"
    requests: list[dict[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> FakeHttpClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        params = dict(params or {})
        FakeHttpClient.requests.append({"url": url, "params": params})
        body = FakeHttpClient.responses.get(str(params.get("phrase")), FakeHttpClient.default_body)
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))


def _install_fake_http(
    monkeypatch: pytest.MonkeyPatch, responses: dict[str, str] | None = None
) -> None:
    FakeHttpClient.responses = responses or {}
    FakeHttpClient.requests = []
    monkeypatch.setattr(keywords_module.httpx, "Client", FakeHttpClient)


class FakeGeminiClient:
    """Stand-in for ``httpx.Client``: scripted Gemini responses per call.

    Each entry in ``responses`` is consumed in order: an int is a bare HTTP
    status; a dict is a 200 JSON payload; an Exception instance is raised.
    Any call beyond the script (or a ``get``) fails the test — proof that no
    unexpected network path ran."""

    responses: list[Any] = []
    requests: list[dict[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> FakeGeminiClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        raise AssertionError(f"unexpected GET (semrush path must not run): {url}")

    def post(
        self, url: str, params: dict[str, Any] | None = None, json: Any = None
    ) -> httpx.Response:
        FakeGeminiClient.requests.append({"url": url, "params": dict(params or {}), "body": json})
        if not FakeGeminiClient.responses:
            raise AssertionError(f"unexpected extra Gemini call: {url}")
        spec = FakeGeminiClient.responses.pop(0)
        if isinstance(spec, Exception):
            raise spec
        request = httpx.Request("POST", url)
        if isinstance(spec, int):
            return httpx.Response(spec, json={}, request=request)
        return httpx.Response(200, json=spec, request=request)


def _install_fake_gemini(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> None:
    FakeGeminiClient.responses = list(responses)
    FakeGeminiClient.requests = []
    monkeypatch.setattr(keywords_module.httpx, "Client", FakeGeminiClient)


_SERP_OBJ = {
    "paa_questions": [
        "What does it mean to consolidate HR systems?",
        "How do I combine multiple HR tools?",
        "What is the best all-in-one HR software?",
        "Is it cheaper to consolidate HR software?",
        "How long does an HR migration take?",
    ],
    "related_searches": ["hr tech stack consolidation", "all in one hr platform"],
    "ranking_formats": ["listicle", "comparison"],
    "serp_features": ["people_also_ask", "ai_overview"],
    "intent_note": "Commercial investigation before a purchase.",
}
_SERP_SOURCES = ["https://example.com/serp-a", "https://blog.example.org/serp-b"]


def _gemini_payload(text: str, *, grounded: bool = True) -> dict[str, Any]:
    candidate: dict[str, Any] = {"content": {"parts": [{"text": text}]}}
    if grounded:
        candidate["groundingMetadata"] = {
            "groundingChunks": [
                {"web": {"uri": _SERP_SOURCES[0], "title": "A"}},
                {"web": {"uri": _SERP_SOURCES[1], "title": "B"}},
            ],
            "webSearchQueries": ["hr software consolidation"],
        }
    return {"candidates": [candidate]}


def _grounded_json_payload() -> dict[str, Any]:
    return _gemini_payload(json.dumps(_SERP_OBJ))


def _serp_row(keyword: str) -> SerpIntel:
    return SerpIntel(
        keyword=keyword,
        paa_questions=list(_SERP_OBJ["paa_questions"]),
        related_searches=list(_SERP_OBJ["related_searches"]),
        ranking_formats=list(_SERP_OBJ["ranking_formats"]),
        serp_features=list(_SERP_OBJ["serp_features"]),
        intent_note=str(_SERP_OBJ["intent_note"]),
        sources=list(_SERP_SOURCES),
        retrieved_at=datetime(2026, 7, 13, tzinfo=UTC),
    )


class FakeProvider:
    """Injected provider: returns canned volumes; keywords absent from the
    table return NO metric (missing stays missing, never zero)."""

    name = "fake"

    def __init__(self, volumes: dict[str, int | None]) -> None:
        self.volumes = volumes
        self.calls: list[list[str]] = []

    def fetch(self, keywords: Any) -> list[KeywordMetric]:
        self.calls.append(list(keywords))
        now = utcnow()
        return [
            KeywordMetric(
                keyword=kw,
                volume=self.volumes[kw.casefold()],
                cpc_usd=1.5,
                competition=0.5,
                source=self.name,
                retrieved_at=now,
            )
            for kw in keywords
            if kw.casefold() in self.volumes
        ]


class FailingProvider:
    name = "failing"

    def fetch(self, keywords: Any) -> list[KeywordMetric]:
        raise KeywordProviderError("provider exploded")


def make_context(mode: str = "live") -> ToolContext:
    config = AppConfig(
        focal_company=FocalCompanyConfig(),
        sources={"keywords": True},
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
        settings=Settings(exa_api_key=""),
        repository=FakeRepository(),
    )


def make_action(**parameters: Any) -> ResearchAction:
    return ResearchAction(
        action_id="ACT-test-keywords",
        action_type="enrich_keywords",
        company_id="example-hr",
        parameters=parameters,
    )


_CSV_BODY = (
    "Keyword;Search Volume;CPC;Competition\n"
    "hr software consolidation;1300;12.40;0.78\n"
    "all in one hr platform;2900;18.10;0.85"
)


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------


def test_parse_semrush_csv_rows() -> None:
    rows = parse_semrush_csv(_CSV_BODY)
    assert rows == [
        {
            "keyword": "hr software consolidation",
            "volume": 1300,
            "cpc_usd": 12.4,
            "competition": 0.78,
        },
        {"keyword": "all in one hr platform", "volume": 2900, "cpc_usd": 18.1, "competition": 0.85},
    ]


def test_parse_semrush_csv_missing_columns_stay_none() -> None:
    rows = parse_semrush_csv("Keyword;CPC\npayroll software;3.20")
    assert rows == [
        {"keyword": "payroll software", "volume": None, "cpc_usd": 3.2, "competition": None}
    ]


def test_parse_semrush_error_body_nothing_found_is_empty() -> None:
    # Contract: the Semrush "no data" error body parses as EMPTY, never a row.
    assert parse_semrush_csv("ERROR 50 :: NOTHING FOUND") == []
    assert parse_semrush_csv("") == []
    assert parse_semrush_csv("   \n") == []


def test_parse_semrush_other_error_raises_typed() -> None:
    # Auth/limit errors must surface typed, never masquerade as "no data".
    with pytest.raises(KeywordProviderError):
        parse_semrush_csv("ERROR 130 :: API KEY DISALLOWED")


# ---------------------------------------------------------------------------
# provider seam / registry
# ---------------------------------------------------------------------------


def test_no_env_key_means_no_active_provider() -> None:
    assert active_keyword_provider() is None
    assert isinstance(resolve_keyword_provider(), NoKeywordProvider)
    assert fetch_keyword_metrics(["hr software"]) is None  # None, not []
    # SERP seam mirrors it: no GEMINI_API_KEY -> None, not [].
    assert active_serp_provider() is None
    assert fetch_serp_intel(["hr software"]) is None


def test_blank_env_key_means_no_active_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMRUSH_API_KEY", "   ")
    monkeypatch.setenv("GEMINI_API_KEY", "   ")
    assert active_keyword_provider() is None
    assert active_serp_provider() is None


def test_env_key_selects_semrush(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMRUSH_API_KEY", "sk-test")
    provider = active_keyword_provider()
    assert isinstance(provider, SemrushProvider)
    assert provider.name == "semrush"


def test_gemini_env_key_selects_serp_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    provider = active_serp_provider()
    assert isinstance(provider, GeminiSerpProvider)
    assert provider.name == "gemini_serp"
    # The VOLUME seam is untouched by the Gemini key (future providers).
    assert active_keyword_provider() is None


def test_no_provider_fetch_raises_exact_message() -> None:
    with pytest.raises(KeywordProviderError, match=r"no keyword provider configured"):
        NoKeywordProvider().fetch(["hr software"])
    assert "GEMINI_API_KEY" in NO_PROVIDER_MESSAGE


def test_semrush_fetch_parses_and_stays_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_http(
        monkeypatch,
        {
            "hr software consolidation": _CSV_BODY.split("\n")[0]
            + "\nhr software consolidation;1300;12.40;0.78",
            "unknown phrase": "ERROR 50 :: NOTHING FOUND",
        },
    )
    provider = SemrushProvider(api_key="sk-test")
    metrics = provider.fetch(
        ["hr software consolidation", "HR  software Consolidation", "unknown phrase"]
    )
    # casefold+whitespace dedupe -> 2 requests, and NOTHING FOUND yields no metric.
    assert [m.keyword for m in metrics] == ["hr software consolidation"]
    assert metrics[0].volume == 1300
    assert metrics[0].cpc_usd == 12.4
    assert metrics[0].competition == 0.78
    assert metrics[0].source == "semrush"
    assert len(FakeHttpClient.requests) == 2
    first = FakeHttpClient.requests[0]["params"]
    assert first["type"] == "phrase_this"
    assert first["export_columns"] == "Ph,Nq,Cp,Co"
    assert first["phrase"] == "hr software consolidation"


def test_fetch_keyword_metrics_caps_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeProvider({})
    monkeypatch.setattr(keywords_module, "active_keyword_provider", lambda: fake)
    got = keywords_module.fetch_keyword_metrics([f"kw {i}" for i in range(60)])
    assert got == []
    assert len(fake.calls) == 1
    assert len(fake.calls[0]) == MAX_KEYWORDS_PER_BATCH


# ---------------------------------------------------------------------------
# GeminiSerpProvider (mocked httpx — no network, ever)
# ---------------------------------------------------------------------------


def test_parse_serp_json_fenced_and_dirty_paths() -> None:
    clean = json.dumps(_SERP_OBJ)
    assert parse_serp_json(clean) == _SERP_OBJ
    # Fenced (```json ... ```), with prose around the fence.
    fenced = f"Here is what the SERP shows:\n```json\n{clean}\n```\nHope that helps!"
    assert parse_serp_json(fenced) == _SERP_OBJ
    # Dirty: prose-wrapped bare object.
    dirty = f"Sure! The live results show: {clean} — observed just now."
    assert parse_serp_json(dirty) == _SERP_OBJ
    # Garbage / non-object payloads parse to None, never to invented data.
    assert parse_serp_json("no json here at all") is None
    assert parse_serp_json("") is None
    assert parse_serp_json('["a", "list"]') is None


def test_gemini_grounded_response_parses_to_serp_intel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    _install_fake_gemini(monkeypatch, [_grounded_json_payload()])
    rows = fetch_serp_intel(["hr software consolidation"])
    assert rows is not None and len(rows) == 1
    row = rows[0]
    assert row.keyword == "hr software consolidation"
    assert row.paa_questions == _SERP_OBJ["paa_questions"]
    assert row.related_searches == _SERP_OBJ["related_searches"]
    assert row.ranking_formats == ["listicle", "comparison"]
    assert row.serp_features == ["people_also_ask", "ai_overview"]
    assert row.intent_note == "Commercial investigation before a purchase."
    assert row.sources == _SERP_SOURCES  # grounding URIs, non-empty, kept
    assert row.retrieved_at is not None
    # REST shape: default model, key as query param, google_search tool on.
    req = FakeGeminiClient.requests[0]
    assert req["url"].endswith("/models/gemini-flash-latest:generateContent")
    assert req["params"] == {"key": "gm-test"}
    assert req["body"]["tools"] == [{"google_search": {}}]
    text = req["body"]["contents"][0]["parts"][0]["text"]
    assert 'Search Google for "hr software consolidation"' in text
    assert "Report only what is actually present." in text


def test_gemini_ungrounded_response_is_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """GROUNDING-REQUIRED: no groundingChunks -> model recall -> never shipped."""
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    _install_fake_gemini(monkeypatch, [_gemini_payload(json.dumps(_SERP_OBJ), grounded=False)])
    provider = active_serp_provider()
    assert provider is not None
    rows = provider.fetch_serp(["hr software consolidation"])
    assert rows == []
    assert any("ungrounded" in n and "discarded" in n for n in provider.notes)
    # Discarded BEFORE any JSON-retry call: exactly one request went out.
    assert len(FakeGeminiClient.requests) == 1


def test_gemini_billing_429_first_call_is_typed_degrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    _install_fake_gemini(monkeypatch, [429])
    with pytest.raises(GeminiSerpQuotaError) as excinfo:
        fetch_serp_intel(["hr software consolidation"])
    # The EXACT spec message — enable billing on the key's project.
    assert str(excinfo.value) == GEMINI_BILLING_MESSAGE
    assert str(excinfo.value) == (
        "Gemini search-grounding quota unavailable — enable billing on the "
        "Gemini API key's project (AI Studio -> Settings -> Plan)"
    )


def test_gemini_429_mid_batch_stops_cleanly_with_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    _install_fake_gemini(monkeypatch, [_grounded_json_payload(), 429])
    provider = active_serp_provider()
    assert provider is not None
    rows = provider.fetch_serp(["kw one", "kw two", "kw three"])
    # The first keyword's grounded row is kept; the batch stops at the 429.
    assert [r.keyword for r in rows] == ["kw one"]
    assert "rate-limited after 1 keywords" in provider.notes
    assert len(FakeGeminiClient.requests) == 2  # nothing attempted after the 429


def test_gemini_404_walks_model_fallbacks_then_pins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    _install_fake_gemini(
        monkeypatch,
        [404, 404, _grounded_json_payload(), _grounded_json_payload()],
    )
    provider = active_serp_provider()
    assert provider is not None
    rows = provider.fetch_serp(["kw one", "kw two"])
    assert [r.keyword for r in rows] == ["kw one", "kw two"]
    models = [r["url"].rsplit("/models/", 1)[1].split(":", 1)[0] for r in FakeGeminiClient.requests]
    # Walks the spec's fallback list once each, then PINS the working model.
    assert models == [
        "gemini-flash-latest",
        "gemini-3.1-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.0-flash",
    ]


def test_gemini_all_models_404_is_typed_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    _install_fake_gemini(monkeypatch, [404, 404, 404])
    provider = active_serp_provider()
    assert provider is not None
    with pytest.raises(KeywordProviderError, match=r"no available model"):
        provider.fetch_serp(["kw one"])


def test_gemini_model_env_override_heads_the_fallback_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-custom")
    _install_fake_gemini(monkeypatch, [404, _grounded_json_payload()])
    provider = active_serp_provider()
    assert provider is not None
    rows = provider.fetch_serp(["kw one"])
    assert len(rows) == 1
    models = [r["url"].rsplit("/models/", 1)[1].split(":", 1)[0] for r in FakeGeminiClient.requests]
    assert models == ["gemini-custom", "gemini-3.1-flash-lite"]


def test_gemini_unparseable_json_retries_once_then_gives_up_typed_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    # First answer grounded but not JSON; the strict re-ask returns clean JSON.
    _install_fake_gemini(
        monkeypatch,
        [_gemini_payload("The SERP shows several PAA boxes and ads."), _grounded_json_payload()],
    )
    rows = fetch_serp_intel(["hr software consolidation"])
    assert rows is not None and len(rows) == 1
    retry_text = FakeGeminiClient.requests[1]["body"]["contents"][0]["parts"][0]["text"]
    assert retry_text.endswith("Return ONLY the JSON object.")

    # Both answers unparseable -> typed empty with a note, never invented rows.
    _install_fake_gemini(
        monkeypatch,
        [_gemini_payload("prose only"), _gemini_payload("still prose")],
    )
    provider = active_serp_provider()
    assert provider is not None
    assert provider.fetch_serp(["hr software consolidation"]) == []
    assert any("unparseable" in n for n in provider.notes)


def test_fetch_serp_intel_caps_gemini_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    _install_fake_gemini(monkeypatch, [_grounded_json_payload()] * MAX_SERP_CALLS_PER_BATCH)
    rows = fetch_serp_intel([f"kw {i}" for i in range(30)])
    assert rows is not None and len(rows) == MAX_SERP_CALLS_PER_BATCH
    assert len(FakeGeminiClient.requests) == MAX_SERP_CALLS_PER_BATCH  # capped at 12


# ---------------------------------------------------------------------------
# KeywordsTool (Level-B tool plumbing)
# ---------------------------------------------------------------------------


async def test_tool_without_key_is_typed_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_http(monkeypatch)
    result = await KeywordsTool().execute(make_action(keywords=["hr software"]), make_context())
    assert result.status == "unsupported"
    assert result.error_type == "provider_not_configured"
    assert result.error_message == NO_PROVIDER_MESSAGE  # exact contract message
    assert result.artifacts == []
    assert result.negative_observations
    assert FakeHttpClient.requests == []  # nothing attempted without a key


async def test_tool_live_attaches_labeled_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMRUSH_API_KEY", "sk-test")
    _install_fake_http(monkeypatch, {"hr software consolidation": _CSV_BODY})
    result = await KeywordsTool().execute(
        make_action(keywords=["hr software consolidation", "no data phrase"]), make_context()
    )
    # One keyword had no provider data -> partial, disclosed, never zeroed.
    assert result.status == "partial"
    assert any("no data phrase" in n for n in result.negative_observations)
    artifact = result.artifacts[0]
    assert artifact.source_type == "keywords"
    assert artifact.metadata["provider"] == "semrush"
    assert artifact.metadata["estimated"] is True
    metrics = artifact.metadata["metrics"]
    assert {m["keyword"] for m in metrics} == {
        "hr software consolidation",
        "all in one hr platform",
    }
    assert all(m["source"] == "semrush" and m["retrieved_at"] for m in metrics)
    assert "provider-reported estimates" in artifact.raw_text


async def test_tool_empty_provider_data_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMRUSH_API_KEY", "sk-test")
    _install_fake_http(monkeypatch)  # every phrase -> ERROR 50 :: NOTHING FOUND
    result = await KeywordsTool().execute(make_action(keywords=["zz nothing"]), make_context())
    assert result.status == "empty"
    assert result.artifacts == []
    assert result.negative_observations


async def test_tool_missing_keywords_param_is_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMRUSH_API_KEY", "sk-test")
    _install_fake_http(monkeypatch)
    result = await KeywordsTool().execute(make_action(), make_context())
    assert result.status == "failed_terminal"
    assert result.error_type == "invalid_parameters"


async def test_tool_provider_error_is_typed_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMRUSH_API_KEY", "sk-test")
    _install_fake_http(monkeypatch, {"hr software": "ERROR 130 :: API KEY DISALLOWED"})
    result = await KeywordsTool().execute(make_action(keywords=["hr software"]), make_context())
    assert result.status == "failed_terminal"
    assert result.error_type == "KeywordProviderError"


async def test_tool_fixture_mode_needs_no_keys() -> None:
    result = await KeywordsTool().execute(
        make_action(keywords=["hr software consolidation"]), make_context(mode="fixture")
    )
    assert result.status == "success"
    artifact = result.artifacts[0]
    assert artifact.is_fixture is True
    assert artifact.source_type == "keywords"
    assert artifact.metadata["metrics"]
    # The fixture also carries a serp-intel shaped record (keyless SERP path).
    serp_artifacts = [a for a in result.artifacts if a.metadata.get("provider") == "gemini_serp"]
    assert serp_artifacts, "fixture must include a serp-intel shaped record"
    serp_meta = serp_artifacts[0].metadata
    assert serp_meta["serp_intel"]["paa_questions"]
    assert serp_meta["serp_intel"]["sources"] and serp_meta["sources"]
    assert serp_artifacts[0].is_fixture is True


async def test_tool_prefers_serp_intel_when_gemini_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With BOTH keys set the tool takes the SERP path (semrush never called —
    the FakeGeminiClient's ``get`` would fail the test) and stores one labeled
    artifact per keyword."""
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    monkeypatch.setenv("SEMRUSH_API_KEY", "sk-test")
    _install_fake_gemini(monkeypatch, [_grounded_json_payload(), _grounded_json_payload()])
    result = await KeywordsTool().execute(
        make_action(keywords=["hr software consolidation", "all in one hr platform"]),
        make_context(),
    )
    assert result.status == "success"
    assert len(result.artifacts) == 2  # one artifact PER keyword
    artifact = result.artifacts[0]
    assert artifact.source_type == "keywords"
    assert artifact.metadata["provider"] == "gemini_serp"
    assert artifact.metadata["sources"] == _SERP_SOURCES
    intel = artifact.metadata["serp_intel"]
    assert intel["keyword"] == "hr software consolidation"
    assert intel["paa_questions"] == _SERP_OBJ["paa_questions"]
    assert intel["sources"] == _SERP_SOURCES
    # Readable rollup: PAA + related + formats/features + grounding URLs.
    assert "People also ask:" in artifact.raw_text
    assert "What does it mean to consolidate HR systems?" in artifact.raw_text
    assert "hr tech stack consolidation" in artifact.normalized_text
    assert "listicle" in artifact.raw_text and "people_also_ask" in artifact.raw_text
    assert _SERP_SOURCES[0] in artifact.raw_text and _SERP_SOURCES[1] in artifact.raw_text
    assert artifact.collection_method == "gemini_google_search_grounding"


async def test_tool_serp_billing_429_is_typed_degrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    _install_fake_gemini(monkeypatch, [429])
    result = await KeywordsTool().execute(
        make_action(keywords=["hr software consolidation"]), make_context()
    )
    assert result.status == "failed_terminal"
    assert result.error_type == "provider_quota"
    assert result.error_message == GEMINI_BILLING_MESSAGE  # exact spec message
    assert result.artifacts == []


async def test_tool_serp_partial_when_some_keywords_ungrounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    _install_fake_gemini(
        monkeypatch,
        [_grounded_json_payload(), _gemini_payload(json.dumps(_SERP_OBJ), grounded=False)],
    )
    result = await KeywordsTool().execute(
        make_action(keywords=["kw grounded", "kw ungrounded"]), make_context()
    )
    assert result.status == "partial"
    assert len(result.artifacts) == 1
    assert result.artifacts[0].metadata["serp_intel"]["keyword"] == "kw grounded"
    assert any("ungrounded" in n for n in result.negative_observations)
    assert any("kw ungrounded" in n for n in result.negative_observations)


# ---------------------------------------------------------------------------
# paid_search enrichment
# ---------------------------------------------------------------------------

_RUN_ID = "RUN-kwtest"


def _write_min_pkg(tmp_path: Path) -> None:
    pkg = {
        "companies": [
            {"company_id": "comp-1", "canonical_name": "Deel", "primary_domain": "deel.com"},
            {
                "company_id": "focal-1",
                "canonical_name": "Rippling",
                "primary_domain": "rippling.com",
            },
        ],
        "category_entry_points": [
            {
                "cep": "consolidating_hr_tools",
                "ownership": "competitor",
                "ownership_basis": "pages",
                "competitor_pages": 3,
                "focal_pages": 1,
            }
        ],
        "theme_comparison": {},
        "classifications": [],
    }
    run_dir = tmp_path / "outputs" / "runs" / _RUN_ID
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "data.json").write_text(json.dumps(pkg), encoding="utf-8")


def _generate(force: bool = False) -> dict[str, Any]:
    from competitive_agent.paid_search import generate_paid_search_targets

    return asyncio.run(generate_paid_search_targets(_RUN_ID, execution_mode="fixture", force=force))


def test_no_provider_envelope_is_null_and_unscored(isolated_env: Path) -> None:
    _write_min_pkg(isolated_env)
    res = _generate()
    assert res["keyword_provider"] is None
    assert res["disclaimer"].startswith("Search volume")
    assert "not publicly knowable" in res["disclaimer"]
    assert "Opportunity score" not in res["method_note"]
    for cluster in res["clusters"]:
        assert "keyword_metrics" not in cluster
        assert "opportunity_score" not in cluster
        assert cluster["validate_before_spend"] is True


def test_fake_provider_attaches_metrics_scores_and_sorts(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_min_pkg(isolated_env)
    fake = FakeProvider(
        {
            "hr software consolidation": 1000,
            "all in one hr platform": 500,
            "deel vs rippling": 9000,
        }
    )
    monkeypatch.setattr(keywords_module, "active_keyword_provider", lambda: fake)
    res = _generate()

    assert res["keyword_provider"] == "fake"
    labels = [c["cluster_label"] for c in res["clusters"]]
    # Scores: conquesting = 9000 * 0.3 (proof missing) = 2700.0 beats
    # consolidation = (1000 + 500) * 0.6 (proof partial) = 900.0 -> sorted desc.
    assert labels == ["competitor brand comparison", "consolidating HR tools"]
    conquest, grounded = res["clusters"]
    assert conquest["opportunity_score"] == 2700.0
    assert grounded["opportunity_score"] == 900.0
    # Metrics attached per cluster; the no-data keyword stays absent (never 0).
    assert {m["keyword"] for m in grounded["keyword_metrics"]} == {
        "hr software consolidation",
        "all in one hr platform",
    }
    assert {m["keyword"] for m in conquest["keyword_metrics"]} == {"deel vs rippling"}
    assert all(m["source"] == "fake" for m in grounded["keyword_metrics"])
    # The formula is documented in the method note.
    assert "Opportunity score" in res["method_note"]
    assert "available=1.0, partial=0.6, missing=0.3" in res["method_note"]
    # Disclaimer softens but still demands live-auction validation.
    assert res["disclaimer"].startswith("Search volume")
    assert "validate final bids in the live auction" in res["disclaimer"]
    # Hard guards are unaffected by enrichment.
    assert conquest["legal_review_required"] is True
    assert conquest["evidence_basis"] == "inferred"  # fabricated quote still demoted
    assert all(c["validate_before_spend"] is True for c in res["clusters"])
    # Batch stayed deduped + capped and included both clusters' seeds.
    seed_batches = [call for call in fake.calls if "deel vs rippling" in call]
    assert seed_batches and len(seed_batches[0]) <= MAX_KEYWORDS_PER_BATCH


def test_provider_failure_degrades_to_null_not_fabricated(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_min_pkg(isolated_env)
    monkeypatch.setattr(keywords_module, "active_keyword_provider", lambda: FailingProvider())
    res = _generate()
    assert res["keyword_provider"] is None
    assert "not publicly knowable" in res["disclaimer"]
    assert all("opportunity_score" not in c for c in res["clusters"])


def _inject_serp(
    monkeypatch: pytest.MonkeyPatch, rows: list[SerpIntel], calls: list[list[str]]
) -> None:
    """Inject the SERP seam the way tests inject the volume seam: module-level
    monkeypatch (which also opens the fixture-mode enrichment gate)."""

    def fake_fetch(keywords: list[str]) -> list[SerpIntel]:
        calls.append(list(keywords))
        return rows

    monkeypatch.setattr(keywords_module, "active_serp_provider", lambda: object())
    monkeypatch.setattr(keywords_module, "fetch_serp_intel", fake_fetch)


def test_serp_intel_attaches_observations_and_never_scores(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRIMARY path: serp_intel per cluster, envelope gemini_serp, the exact
    SERP disclaimer, an enriched/skipped method note — and NO volumes, NO
    opportunity_score, NO re-sort (never rank on invented numbers)."""
    _write_min_pkg(isolated_env)
    calls: list[list[str]] = []
    rows = [_serp_row("hr software consolidation"), _serp_row("deel vs rippling")]
    _inject_serp(monkeypatch, rows, calls)
    res = _generate()

    assert res["keyword_provider"] == "gemini_serp"
    assert res["disclaimer"] == (
        "SERP intelligence is observed live from Google results at draft time; "
        "volumes/CPC are not returned by this provider — validate free in Google "
        "Keyword Planner / Search Console."
    )
    # Fixture draft order preserved — the serp path never re-sorts clusters.
    labels = [c["cluster_label"] for c in res["clusters"]]
    assert labels == ["consolidating HR tools", "competitor brand comparison"]
    grounded, conquest = res["clusters"]
    assert [i["keyword"] for i in grounded["serp_intel"]] == ["hr software consolidation"]
    assert [i["keyword"] for i in conquest["serp_intel"]] == ["deel vs rippling"]
    for cluster in res["clusters"]:
        for intel in cluster["serp_intel"]:
            assert intel["sources"], "a kept row must carry grounding sources"
            assert intel["paa_questions"]
        # No invented numbers on the serp path — ever.
        assert "keyword_metrics" not in cluster
        assert "opportunity_score" not in cluster
        assert cluster["validate_before_spend"] is True
    # method_note: N enriched / M skipped + the provider name.
    assert "gemini_serp" in res["method_note"]
    assert "2 seed keyword(s) enriched, 3 skipped" in res["method_note"]
    # Hard guards unaffected by enrichment.
    assert conquest["legal_review_required"] is True
    assert conquest["evidence_basis"] == "inferred"  # fabricated quote still demoted
    # One bounded batch: every cluster's top seed first, then depth, capped.
    assert len(calls) == 1
    assert calls[0][:2] == ["hr software consolidation", "deel vs rippling"]
    assert len(calls[0]) <= 12


def test_serp_quota_degrade_keeps_envelope_null_with_exact_note(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_min_pkg(isolated_env)

    def raise_quota(keywords: list[str]) -> list[SerpIntel]:
        raise GeminiSerpQuotaError(GEMINI_BILLING_MESSAGE)

    monkeypatch.setattr(keywords_module, "active_serp_provider", lambda: object())
    monkeypatch.setattr(keywords_module, "fetch_serp_intel", raise_quota)
    res = _generate()
    assert res["keyword_provider"] is None  # spec: envelope stays null
    assert GEMINI_BILLING_MESSAGE in res["method_note"]  # the typed degrade note
    assert "not publicly knowable" in res["disclaimer"]  # base disclaimer kept
    for cluster in res["clusters"]:
        assert "serp_intel" not in cluster
        assert "keyword_metrics" not in cluster
        assert "opportunity_score" not in cluster


def test_fixture_mode_draft_skips_all_provider_network_enrichment(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mode isolation (accuracy review): real keys in the developer's env must
    NOT cause a fixture draft to call providers — only an explicitly injected
    provider (module-level monkeypatch) may enrich. Any HTTP call here fails
    the test via the scripted fake client."""
    _write_min_pkg(isolated_env)
    monkeypatch.setenv("GEMINI_API_KEY", "gm-real-looking")
    monkeypatch.setenv("SEMRUSH_API_KEY", "sk-real-looking")
    _install_fake_gemini(monkeypatch, [])  # any POST/GET raises AssertionError
    res = _generate()
    assert FakeGeminiClient.requests == []  # zero network attempts
    assert res["keyword_provider"] is None
    assert "not publicly knowable" in res["disclaimer"]
    for cluster in res["clusters"]:
        assert "serp_intel" not in cluster
        assert "keyword_metrics" not in cluster
        assert "opportunity_score" not in cluster


# ---------------------------------------------------------------------------
# planner: enrich_keywords proposal (allowlist-gated)
# ---------------------------------------------------------------------------


class _PlannerCfg:
    """Minimal AppConfig stand-in for direct propose_actions calls."""

    sources = {"website": True, "keywords": True}
    budgets = {"max_retries_per_source": 2}
    windows: dict[str, Any] = {}
    collection = {"deep_crawl": False}
    exa_agent: dict[str, Any] = {}


class _CepClassification:
    def __init__(self, ceps: list[str]) -> None:
        self.category_entry_points = ceps


class _CepRepo:
    def __init__(self, cep_lists: list[list[str]]) -> None:
        self._models = [_CepClassification(c) for c in cep_lists]

    def list_classifications(self, run_id: str) -> list[_CepClassification]:
        return self._models


def _planner_state(**overrides: Any) -> Any:
    from competitive_agent.schemas.company import Company
    from competitive_agent.state import DirectorState

    state = DirectorState(run_id="RUN-kwplan", company_input="deel.com", **overrides)
    state.company = Company(
        company_id="CO-deel",
        canonical_name="Deel",
        primary_domain="deel.com",
        resolved_at=utcnow(),
        resolution_confidence="high",
    )
    return state


def _planner_ctx(repository: Any = None) -> Any:
    from competitive_agent.graph import GraphContext

    return GraphContext(repository=repository, trace=None, config=_PlannerCfg(), settings=None)


def _enrich_proposals(state: Any, ctx: Any) -> list[Any]:
    from competitive_agent.planner import propose_actions

    return [p for p in propose_actions(state, ctx) if p.action_type == "enrich_keywords"]


def test_planner_proposes_enrich_keywords_only_under_allowlist() -> None:
    ctx = _planner_ctx()

    # Default batch run (allowlist None) NEVER proposes it.
    assert _enrich_proposals(_planner_state(), ctx) == []
    # An allowlist without "keywords" doesn't either.
    assert _enrich_proposals(_planner_state(source_allowlist=["reviews"]), ctx) == []

    # A focused "keywords" pass proposes exactly one, with fallback seeds
    # (no stored CEPs): competitor name + the user's focus terms.
    state = _planner_state(source_allowlist=["keywords"], user_focus=["onboarding pain in reviews"])
    proposals = _enrich_proposals(state, ctx)
    assert len(proposals) == 1
    action = proposals[0]
    assert action.source_name == "keywords"
    assert action.parameters["keywords"] == ["Deel", "onboarding pain in reviews"]

    # Once executed this pass, it is not re-proposed (bounded spend); a new
    # "keywords" research pass clears executed keys and re-arms it.
    from competitive_agent.planner import action_key

    state.executed_action_keys = [action_key("enrich_keywords", action.parameters)]
    assert _enrich_proposals(state, ctx) == []


def test_planner_keyword_seeds_prefer_humanized_cep_labels() -> None:
    ceps = [
        ["consolidating_hr_tools", "global_payroll_compliance", "consolidating_hr_tools"],
        [
            "hiring_internationally",
            "contractor_conversion",
            "benefits_administration",
            "hr_reporting_and_analytics",
            "onboarding_automation",
            "multi_state_payroll",
            "equipment_provisioning",  # 9th unique — beyond the 8 cap
        ],
    ]
    state = _planner_state(source_allowlist=["keywords"])
    proposals = _enrich_proposals(state, _planner_ctx(repository=_CepRepo(ceps)))
    assert len(proposals) == 1
    seeds = proposals[0].parameters["keywords"]
    assert len(seeds) == 8  # capped
    assert seeds[0] == "consolidating hr tools"  # humanized, deduped, order kept
    assert all("_" not in s for s in seeds)
    assert "equipment provisioning" not in seeds


def test_build_inputs_default_keeps_strict_jinja_happy(isolated_env: Path) -> None:
    from competitive_agent.paid_search import build_inputs
    from competitive_agent.prompt_registry import PromptRegistry

    _write_min_pkg(isolated_env)
    pkg = json.loads(
        (isolated_env / "outputs" / "runs" / _RUN_ID / "data.json").read_text(encoding="utf-8")
    )
    inputs = build_inputs(_RUN_ID, pkg)
    assert inputs["keyword_metrics"] == "(no keyword API configured)"
    rendered = PromptRegistry().get("paid_search_targeting").render(**inputs)
    assert "(no keyword API configured)" in rendered
    assert "never estimate" in rendered  # the shifted instruction is live
