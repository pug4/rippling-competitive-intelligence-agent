"""Shared HTTP client: one AsyncClient, politeness limits, URL policy, robots.

Every network fetch in the system goes through :class:`SharedHttp` so that
URL safety (``security.url_is_allowed``), redirect caps, response-size caps,
per-domain politeness, and bounded retries are enforced in exactly one place.
"""

from __future__ import annotations

import asyncio
import random
import urllib.robotparser
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from ..security import MAX_REDIRECTS, MAX_RESPONSE_BYTES, url_is_allowed

USER_AGENT = "competitive-agent-research/0.1 (public marketing research)"
DEFAULT_TIMEOUT_SECONDS = 20.0

# Errors worth retrying: transient connectivity and timeouts. Everything else
# (4xx, protocol errors, policy violations) is terminal at this layer.
RETRYABLE_EXCEPTIONS = (httpx.ConnectError, httpx.TimeoutException)


class UrlPolicyError(ValueError):
    """Raised when a URL is rejected by the security policy."""


async def retry_async(
    fn: Callable[[], Awaitable[httpx.Response]],
    *,
    retries: int = 2,
    base_delay: float = 0.5,
    jitter: float = 0.25,
) -> httpx.Response:
    """Await ``fn`` with bounded retries on connect/timeout errors and 5xx.

    Anything else (including 4xx responses) is returned/raised immediately —
    retrying a terminal failure only burns budget.
    """
    attempt = 0
    while True:
        try:
            response = await fn()
        except RETRYABLE_EXCEPTIONS:
            if attempt >= retries:
                raise
        else:
            if response.status_code < 500 or attempt >= retries:
                return response
        delay = base_delay * (2**attempt) + random.uniform(0, jitter)
        await asyncio.sleep(delay)
        attempt += 1


async def _guard_request(request: httpx.Request) -> None:
    """Request event hook: re-validate EVERY hop, including redirects.

    This is what lets ``url_is_allowed`` skip DNS resolution — a public URL
    that redirects to a private/loopback target is rejected here.
    """
    allowed, reason = url_is_allowed(str(request.url))
    if not allowed:
        raise UrlPolicyError(f"blocked request to {request.url}: {reason}")


class SharedHttp:
    """One ``httpx.AsyncClient`` shared by every adapter.

    - honest research User-Agent, 20 s timeout, redirects capped at
      ``MAX_REDIRECTS`` and re-validated per hop;
    - global + per-domain semaphores for politeness;
    - responses streamed and truncated at ``MAX_RESPONSE_BYTES``.
    """

    def __init__(
        self,
        *,
        max_parallel_fetches: int = 5,
        per_domain_concurrency: int = 2,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            event_hooks={"request": [_guard_request]},
        )
        self._global_semaphore = asyncio.Semaphore(max_parallel_fetches)
        self._per_domain_concurrency = per_domain_concurrency
        self._domain_semaphores: dict[str, asyncio.Semaphore] = {}
        self.robots = RobotsCache(self)

    @classmethod
    def from_settings(cls, settings: object) -> SharedHttp:
        return cls(
            max_parallel_fetches=getattr(settings, "max_parallel_fetches", 5),
            per_domain_concurrency=getattr(settings, "per_domain_concurrency", 2),
        )

    def _domain_semaphore(self, host: str) -> asyncio.Semaphore:
        return self._domain_semaphores.setdefault(
            host, asyncio.Semaphore(self._per_domain_concurrency)
        )

    async def get(self, url: str) -> httpx.Response:
        """Fetch ``url`` under the full safety/politeness policy."""
        allowed, reason = url_is_allowed(url)
        if not allowed:
            raise UrlPolicyError(f"blocked request to {url}: {reason}")
        host = (urlsplit(url).hostname or "").lower()
        async with self._global_semaphore, self._domain_semaphore(host):
            return await retry_async(lambda: self._fetch_once(url))

    async def _fetch_once(self, url: str) -> httpx.Response:
        """Stream the body, truncating at ``MAX_RESPONSE_BYTES``."""
        async with self._client.stream("GET", url) as upstream:
            body = bytearray()
            truncated = False
            async for chunk in upstream.aiter_bytes():
                remaining = MAX_RESPONSE_BYTES - len(body)
                if remaining <= 0:
                    truncated = True
                    break
                if len(chunk) > remaining:
                    body.extend(chunk[:remaining])
                    truncated = True
                    break
                body.extend(chunk)
            # aiter_bytes() already decompressed the body, so strip
            # transport-level headers that no longer describe the content.
            headers = [
                (k, v)
                for k, v in upstream.headers.items()
                if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
            ]
            return httpx.Response(
                status_code=upstream.status_code,
                headers=headers,
                content=bytes(body),
                request=upstream.request,
                extensions={"truncated": truncated},
            )

    async def aclose(self) -> None:
        await self._client.aclose()


class RobotsCache:
    """robots.txt policy, fetched at most once per host.

    Failure to *fetch* robots.txt (network error, 4xx/5xx) is treated as
    ALLOW — the common convention for absent robots — but a note is recorded
    in ``self.notes`` so the run's limitations panel can disclose it. An
    explicit Disallow is always respected (§37.38: robots blocked → respect
    and fall back).
    """

    def __init__(self, http: SharedHttp) -> None:
        self._http = http
        self._parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.notes: list[str] = []

    async def is_allowed(self, url: str) -> bool:
        parser = await self._get_parser(url)
        if parser is None:
            return True  # robots unavailable -> allow (note already recorded)
        return parser.can_fetch(USER_AGENT, url)

    async def _get_parser(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{(parts.netloc or '').lower()}"
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self._parsers:
                return self._parsers[key]
            parser = await self._fetch_parser(key)
            self._parsers[key] = parser
            return parser

    async def _fetch_parser(self, origin: str) -> urllib.robotparser.RobotFileParser | None:
        robots_url = f"{origin}/robots.txt"
        try:
            response = await self._http.get(robots_url)
        except Exception as exc:  # fetch failure = allow, with a note
            self.notes.append(
                f"robots.txt unavailable for {origin} ({type(exc).__name__}): treated as allow"
            )
            return None
        if response.status_code >= 400:
            self.notes.append(
                f"robots.txt returned HTTP {response.status_code} for {origin}: treated as allow"
            )
            return None
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser
