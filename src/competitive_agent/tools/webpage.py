"""Current-website collection: site mapping then targeted page fetch (§37.12).

Two adapters share the ``website`` feature flag:

- :class:`WebsiteMapTool` discovers a page map (sitemap first, homepage
  anchors as fallback), scores paths by marketing relevance, and returns ONE
  map artifact — it does not crawl the mapped pages.
- :class:`WebpageFetchTool` fetches an explicit batch of URLs into artifacts.

Mapping-before-crawling keeps the agent from fetching irrelevant paths and
lets the planner spend its fetch budget on pricing / product / comparison /
customer pages first (§16.1).
"""

from __future__ import annotations

import re
from typing import Any, ClassVar
from urllib.parse import urlsplit
from xml.etree import ElementTree

from ..processing.normalize import content_hash, html_to_text, normalize_text
from ..schemas.artifact import RawArtifact
from ..schemas.common import new_id, utcnow
from ..schemas.source import ResearchAction, ToolCapabilities, ToolResult
from .base import BaseTool, ToolContext

# Marketing-relevant path patterns -> (category, score). Higher score = fetch
# sooner. FIRST match wins, so order matters. Data, not logic: retargeting to
# another industry edits this table.
PRIORITY_PATTERNS: tuple[tuple[str, str, float], ...] = (
    (r"/pricing|/plans", "pricing", 1.0),
    # platform hubs incl. Deel's /hr-platform and any *-platform slug
    (r"/platform|-platform(/|$)", "platform", 0.9),
    (r"/products?(/|$)|/solutions?(/|$)|/use-cases?(/|$)", "product", 0.86),
    # Common B2B product-category hubs (generic across HR/fintech/etc.), so real
    # product pages are never left at the 0.1 default. Bare 2-letter hubs /it /hr
    # are products here, not locales (see _is_non_english_locale_path).
    (
        r"/(payroll|peo|eor|hris|hcm|benefits|onboarding|offboarding|compliance|"
        r"contractors?|global-hiring|universal-hr|workforce|mobility|immigration|"
        r"engage|it|hr)(/|$)",
        "product",
        0.8,
    ),
    (r"/customers?|/case-stud", "customers", 0.75),
    (r"/why-", "product", 0.7),
    (r"/enterprise|/segments?/", "segment", 0.68),
    # Comparison: ONLY dedicated compare pages, not blog '-vs-' SEO posts (those
    # are capped by _LOWVALUE_SUBTREES first). Scored BELOW product so a real
    # product page never loses the fetch budget to a comparison page.
    (
        r"^/compare|/versus|/alternatives?|/[a-z0-9]+-vs-[a-z0-9-]+/?$|-vs-competitors",
        "comparison",
        0.72,
    ),
    (r"/security", "product", 0.6),
    (r"/integrations?", "product", 0.55),
    (r"/press|/news(room)?", "press", 0.5),
    (r"/changelog|/releases|/whats-new", "changelog", 0.5),
    (r"/about", "about", 0.4),
)

# SEO/content subtrees are capped regardless of slug — this is the single
# biggest lever: it stops '-vs-' blog/glossary posts (w2-vs-1099, iam-vs-pam)
# from borrowing the comparison score and flooding the fetch budget ahead of
# real product pages (audit finding).
_LOWVALUE_SUBTREES = re.compile(
    r"/(blog|glossary|resources?|guides?|academy|learn|help|support|docs|"
    r"templates?|tools|dictionary|hub)(/|$)",
    re.IGNORECASE,
)

_MAX_MAP_URLS = 500
_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# Locale-prefixed paths (/es/, /fr/, /pt-br/, /sv-se/ …) are non-canonical
# translations of the English pages; we want the English source, not a Swedish
# blog post. A first segment matching this and not English is heavily penalized
# so canonical pages win the fetch budget.
_LOCALE_RE = re.compile(r"^[a-z]{2}(-[a-z]{2})?$")
_ENGLISH_LOCALES = {"en", "en-us", "en-gb"}

# Canonical high-value English paths worth trying on any B2B SaaS site,
# independent of sitemap quality. 404s become negative observations, not fake
# data. Data, not logic: retargeting to another industry edits this list.
_CANONICAL_SEED_PATHS = (
    "/",
    "/pricing",
    "/platform",
    "/product",
    "/products",
    "/solutions",
    "/customers",
    "/case-studies",
    "/use-cases",
    "/security",
    "/integrations",
)


def _is_non_english_locale_path(path: str) -> bool:
    segments = [s for s in path.split("/") if s]
    # A BARE 2-letter segment (/it, /hr, /es) is a product hub / landing page,
    # not a locale prefix — locales always PREFIX further path segments
    # (/es/blog/...). Only penalize when a locale code is followed by content.
    if len(segments) < 2:
        return False
    first = segments[0].lower()
    return bool(_LOCALE_RE.match(first)) and first not in _ENGLISH_LOCALES


def _score_path(url: str) -> tuple[str, float]:
    path = urlsplit(url).path or "/"
    if path in ("", "/"):
        return "home", 0.95
    # Low-value SEO/content subtrees are capped BEFORE the priority patterns so a
    # blog '-vs-' post can never inherit the comparison score.
    if _LOWVALUE_SUBTREES.search(path):
        return "content", 0.12
    category, score = "other", 0.1
    for pattern, cat, sc in PRIORITY_PATTERNS:
        if re.search(pattern, path, re.IGNORECASE):
            category, score = cat, sc
            break
    # Push non-English locale variants to the bottom of the fetch queue.
    if _is_non_english_locale_path(path):
        score = min(score, 0.05)
    return category, score


def _extract_title(html: str) -> str | None:
    match = _TITLE_RE.search(html or "")
    if not match:
        return None
    return normalize_text(match.group(1)) or None


class WebsiteMapTool(BaseTool):
    """Discover a scored page map for a domain (sitemap + homepage anchors)."""

    name: ClassVar[str] = "website_map"
    adapter_version: ClassVar[str] = "0.1.0"
    source_flag_name: ClassVar[str] = "website"
    ACTION_TYPES: ClassVar[tuple[str, ...]] = ("map_current_website",)

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=True,
            fixture_available=True,
            supported_action_types=list(self.ACTION_TYPES),
            supports_date_filters=False,
            supports_historical_data=False,
            supports_exact_content=True,
            returns_estimates=False,
            known_limitations=[
                "Sitemap may be absent or partial; homepage-anchor fallback is shallower.",
                "Dynamic / JS-rendered navigation may hide pages from static mapping.",
            ],
        )

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type in self.ACTION_TYPES

    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        domain = str((action.parameters or {}).get("domain") or "").strip()
        if not domain:
            return self._result(
                action,
                status="failed_terminal",
                error_type="ValueError",
                error_message="map_current_website requires a 'domain' parameter.",
            )
        if context.http is None:
            return self._result(
                action,
                status="failed_terminal",
                error_type="RuntimeError",
                error_message="No HTTP client available in the tool context.",
            )
        origin = f"https://www.{domain}" if not domain.startswith("http") else domain
        negatives: list[str] = []

        urls: set[str] = set()
        # 1. sitemap.xml (one level of sitemap-index nesting)
        urls |= await self._collect_sitemap(context, f"{origin}/sitemap.xml", negatives, depth=0)
        # 2. homepage + anchor fallback
        homepage_html = await self._safe_get_text(context, origin + "/", negatives)
        if homepage_html:
            urls.add(origin + "/")
            urls |= self._anchor_urls(homepage_html, origin)
        # 3. Always seed canonical high-value English paths so the core pages
        # are tried even when the sitemap is locale-heavy or missing. Non-
        # existent seeds simply 404 into negative observations on fetch.
        urls |= {origin.rstrip("/") + p for p in _CANONICAL_SEED_PATHS}

        if not urls:
            return self._result(
                action,
                status="empty",
                negative_observations=negatives
                or [f"no sitemap or homepage anchors discoverable for {domain}"],
            )

        # Score EVERY url, then sort by score, THEN truncate — otherwise a large
        # sitemap's unordered set is cut to _MAX_MAP_URLS before ranking and the
        # highest-value pages (homepage, pricing, platform) can be dropped before
        # they are ever seen (reviewer R2 root cause).
        page_map: list[dict[str, Any]] = []
        for url in urls:
            category, score = _score_path(url)
            page_map.append(
                {
                    "url": url,
                    "path": urlsplit(url).path or "/",
                    "category": category,
                    "score": score,
                }
            )
        page_map.sort(key=lambda row: -float(row["score"]))
        page_map = page_map[:_MAX_MAP_URLS]
        artifact = RawArtifact(
            artifact_id=new_id("ART"),
            company_id=action.company_id,
            source_type="sitemap",
            source_name=self.name,
            url=origin + "/",
            final_url=origin + "/",
            title=f"Page map for {domain}",
            retrieved_at=utcnow(),
            time_window_ids=list(action.time_window_ids),
            raw_text="\n".join(str(row["url"]) for row in page_map),
            normalized_text="\n".join(str(row["url"]) for row in page_map),
            content_hash=content_hash("\n".join(sorted(urls))),
            metadata={"page_map": page_map, "map_size": len(page_map)},
            collection_method="sitemap_map",
        )
        return self._result(
            action,
            status="success",
            artifacts=[artifact],
            negative_observations=negatives,
        )

    async def _collect_sitemap(
        self, context: ToolContext, sitemap_url: str, negatives: list[str], depth: int
    ) -> set[str]:
        text = await self._safe_get_text(context, sitemap_url, negatives, robots_ok=True)
        if not text:
            return set()
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            negatives.append(f"sitemap at {sitemap_url} was not valid XML")
            return set()
        tag = root.tag.split("}")[-1]
        found: set[str] = set()
        if tag == "sitemapindex" and depth == 0:
            child_maps = [
                loc.text.strip()
                for loc in root.findall(".//sm:sitemap/sm:loc", _SITEMAP_NS)
                if loc.text
            ][:10]
            for child in child_maps:
                found |= await self._collect_sitemap(context, child, negatives, depth + 1)
        for loc in root.findall(".//sm:url/sm:loc", _SITEMAP_NS):
            if loc.text:
                found.add(loc.text.strip())
        # namespace-less sitemaps
        if not found:
            for loc in root.iter():
                if loc.tag.split("}")[-1] == "loc" and loc.text:
                    found.add(loc.text.strip())
        return found

    def _anchor_urls(self, html: str, origin: str) -> set[str]:
        host = urlsplit(origin).hostname or ""
        out: set[str] = set()
        for href in _HREF_RE.findall(html):
            if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
                continue
            if href.startswith("/"):
                out.add(origin.rstrip("/") + href)
            elif href.startswith("http") and host and host in (urlsplit(href).hostname or ""):
                out.add(href)
        return out

    async def _safe_get_text(
        self, context: ToolContext, url: str, negatives: list[str], robots_ok: bool = False
    ) -> str:
        assert context.http is not None  # guarded by caller
        try:
            if not robots_ok and not await context.http.robots.is_allowed(url):
                negatives.append(f"{url}: robots disallowed")
                return ""
            response = await context.http.get(url)
        except Exception as exc:  # noqa: BLE001 - one bad fetch never sinks the map
            negatives.append(f"{url}: fetch failed ({type(exc).__name__})")
            return ""
        if response.status_code != 200:
            negatives.append(f"{url}: HTTP {response.status_code}")
            return ""
        return response.text or ""


class WebpageFetchTool(BaseTool):
    """Fetch an explicit batch of URLs into normalized artifacts."""

    name: ClassVar[str] = "webpage_fetch"
    adapter_version: ClassVar[str] = "0.1.0"
    source_flag_name: ClassVar[str] = "website"
    ACTION_TYPES: ClassVar[tuple[str, ...]] = ("fetch_webpage",)

    def capabilities(self) -> ToolCapabilities:
        return ToolCapabilities(
            live_available=True,
            fixture_available=True,
            supported_action_types=list(self.ACTION_TYPES),
            supports_date_filters=False,
            supports_historical_data=False,
            supports_exact_content=True,
            returns_estimates=False,
            known_limitations=[
                "Static fetch only; JS-rendered content may be missing.",
                "Robots-disallowed pages are skipped, never fetched.",
            ],
        )

    def supports(self, action: ResearchAction) -> bool:
        return action.action_type in self.ACTION_TYPES

    async def _execute_live(self, action: ResearchAction, context: ToolContext) -> ToolResult:
        params: dict[str, Any] = action.parameters or {}
        urls = [str(u) for u in (params.get("urls") or []) if u]
        source_type = str(params.get("source_type") or "webpage")
        page_category = params.get("page_category")
        if not urls:
            return self._result(
                action,
                status="failed_terminal",
                error_type="ValueError",
                error_message="fetch_webpage requires a non-empty 'urls' list.",
            )
        if context.http is None:
            return self._result(
                action,
                status="failed_terminal",
                error_type="RuntimeError",
                error_message="No HTTP client available in the tool context.",
            )

        artifacts: list[RawArtifact] = []
        negatives: list[str] = []
        for url in urls:
            try:
                if not await context.http.robots.is_allowed(url):
                    negatives.append(f"{url}: robots disallowed (not fetched)")
                    continue
                response = await context.http.get(url)
            except Exception as exc:  # noqa: BLE001
                negatives.append(f"{url}: fetch failed ({type(exc).__name__})")
                continue
            if response.status_code != 200:
                negatives.append(f"{url}: HTTP {response.status_code}")
                continue
            raw = response.text or ""
            category = page_category or _score_path(str(response.url))[0]
            artifacts.append(
                RawArtifact(
                    artifact_id=new_id("ART"),
                    company_id=action.company_id,
                    source_type=source_type,
                    source_name=self.name,
                    url=url,
                    final_url=str(response.url),
                    title=_extract_title(raw),
                    retrieved_at=utcnow(),
                    time_window_ids=list(action.time_window_ids),
                    raw_text=raw,
                    normalized_text=normalize_text(html_to_text(raw)),
                    content_hash=content_hash(raw),
                    metadata={
                        "page_category": category,
                        "http_status": response.status_code,
                        "final_url": str(response.url),
                    },
                    collection_method="webpage_fetch",
                    is_partial=bool(getattr(response, "extensions", {}).get("truncated", False)),
                )
            )
        if not artifacts:
            return self._result(
                action,
                status="empty",
                negative_observations=negatives or [f"no pages fetched from {len(urls)} URL(s)"],
            )
        return self._result(
            action,
            status="partial" if negatives else "success",
            artifacts=artifacts,
            negative_observations=negatives,
        )
