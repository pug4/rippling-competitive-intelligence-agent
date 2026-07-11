"""Tool adapter framework: contract, registry, and shared HTTP client."""

from __future__ import annotations

from .base import (
    BaseTool,
    RepositoryLike,
    ToolContext,
    TraceWriterLike,
    action_args_hash,
)
from .http import (
    USER_AGENT,
    RobotsCache,
    SharedHttp,
    UrlPolicyError,
    retry_async,
)
from .registry import ToolRegistry

__all__ = [
    "USER_AGENT",
    "BaseTool",
    "RepositoryLike",
    "RobotsCache",
    "SharedHttp",
    "ToolContext",
    "ToolRegistry",
    "TraceWriterLike",
    "UrlPolicyError",
    "action_args_hash",
    "retry_async",
]
