"""Unit tests for the website map + fetch adapters (sitemap parse, priority
scoring, robots skip, fixture determinism)."""

from __future__ import annotations

import pytest

from competitive_agent.schemas.source import ResearchAction
from competitive_agent.tools.webpage import _score_path


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200, url: str = "https://x/"):
        self.text = text
        self.status_code = status_code
        self.url = url
        self.headers = {"content-type": "text/html"}
        self.extensions = {"truncated": False}


class FakeRobots:
    def __init__(self, disallow: set[str] | None = None):
        self._disallow = disallow or set()

    async def is_allowed(self, url: str) -> bool:
        return url not in self._disallow


class FakeHttp:
    def __init__(self, pages: dict[str, FakeResponse], disallow: set[str] | None = None):
        self._pages = pages
        self.robots = FakeRobots(disallow)

    async def get(self, url: str) -> FakeResponse:
        if url in self._pages:
            return self._pages[url]
        return FakeResponse("", status_code=404, url=url)


SITEMAP = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.example-hr.com/</loc></url>
  <url><loc>https://www.example-hr.com/pricing</loc></url>
  <url><loc>https://www.example-hr.com/platform</loc></url>
  <url><loc>https://www.example-hr.com/compare/example-hr-vs-competitor</loc></url>
  <url><loc>https://www.example-hr.com/blog/random-post</loc></url>
</urlset>"""


def _ctx(http, tmp_path):
    from competitive_agent.config import get_config, get_settings
    from competitive_agent.tools.base import ToolContext

    return ToolContext(
        run_id="r1",
        company_id="c1",
        mode="live",
        config=get_config(),
        settings=get_settings(),
        repository=_NullRepo(),
        http=http,
    )


class _NullRepo:
    def record_tool_call(self, **_):
        return "tc"

    def find_cached_tool_call(self, *_a, **_k):
        return None


def test_score_path_prioritizes_marketing_pages():
    assert _score_path("https://x/pricing")[0] == "pricing"
    assert _score_path("https://x/compare/x-vs-y")[0] == "comparison"
    assert _score_path("https://x/")[0] == "home"
    assert _score_path("https://x/blog/random")[0] == "other"


async def test_sitemap_map_scores_and_orders(tmp_path):
    from competitive_agent.tools.webpage import WebsiteMapTool

    http = FakeHttp(
        {
            "https://www.example-hr.com/sitemap.xml": FakeResponse(SITEMAP),
            "https://www.example-hr.com/": FakeResponse("<html><title>Home</title></html>"),
        }
    )
    tool = WebsiteMapTool()
    action = ResearchAction(
        action_id="a1", action_type="map_current_website", company_id="c1",
        source_name="website_map", parameters={"domain": "example-hr.com"},
    )
    result = await tool._execute_live(action, _ctx(http, tmp_path))
    assert result.status == "success"
    page_map = result.artifacts[0].metadata["page_map"]
    # pricing (1.0) must outrank the blog page (0.1)
    assert page_map[0]["category"] == "pricing"
    assert {p["category"] for p in page_map} >= {"pricing", "platform", "comparison"}


async def test_homepage_anchor_fallback_when_no_sitemap(tmp_path):
    from competitive_agent.tools.webpage import WebsiteMapTool

    html = '<html><body><a href="/pricing">Pricing</a><a href="/platform">Platform</a></body></html>'
    http = FakeHttp({"https://www.example-hr.com/": FakeResponse(html)})
    tool = WebsiteMapTool()
    action = ResearchAction(
        action_id="a1", action_type="map_current_website", company_id="c1",
        source_name="website_map", parameters={"domain": "example-hr.com"},
    )
    result = await tool._execute_live(action, _ctx(http, tmp_path))
    assert result.status == "success"
    urls = {p["url"] for p in result.artifacts[0].metadata["page_map"]}
    assert any(u.endswith("/pricing") for u in urls)


async def test_fetch_skips_robots_disallowed(tmp_path):
    from competitive_agent.tools.webpage import WebpageFetchTool

    http = FakeHttp(
        {"https://www.example-hr.com/pricing": FakeResponse("<html><title>P</title>Pricing copy</html>", url="https://www.example-hr.com/pricing")},
        disallow={"https://www.example-hr.com/secret"},
    )
    tool = WebpageFetchTool()
    action = ResearchAction(
        action_id="a2", action_type="fetch_webpage", company_id="c1", source_name="webpage_fetch",
        parameters={"urls": ["https://www.example-hr.com/pricing", "https://www.example-hr.com/secret"], "source_type": "webpage"},
    )
    result = await tool._execute_live(action, _ctx(http, tmp_path))
    assert result.status == "partial"
    assert len(result.artifacts) == 1
    assert any("robots disallowed" in n for n in result.negative_observations)


async def test_fetch_partial_when_one_url_fails(tmp_path):
    from competitive_agent.tools.webpage import WebpageFetchTool

    http = FakeHttp(
        {"https://www.example-hr.com/a": FakeResponse("<html>ok body</html>", url="https://www.example-hr.com/a")},
    )
    tool = WebpageFetchTool()
    action = ResearchAction(
        action_id="a3", action_type="fetch_webpage", company_id="c1", source_name="webpage_fetch",
        parameters={"urls": ["https://www.example-hr.com/a", "https://www.example-hr.com/missing"], "source_type": "webpage"},
    )
    result = await tool._execute_live(action, _ctx(http, tmp_path))
    assert result.status == "partial"
    assert len(result.artifacts) == 1
