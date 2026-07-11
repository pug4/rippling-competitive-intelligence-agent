"""Project exception hierarchy.

Every error raised by competitive_agent code derives from
``CompetitiveAgentError`` so callers can catch the whole family with one
clause while still distinguishing specific failure modes.
"""

from __future__ import annotations


class CompetitiveAgentError(Exception):
    """Base class for all competitive_agent errors."""


class ModelOutputInvalid(CompetitiveAgentError):
    """Structured model output failed schema validation after the bounded
    repair-retry / escalation sequence (blueprint §37.28)."""


class ToolExecutionError(CompetitiveAgentError):
    """A collection tool failed in a way the tool layer could not convert
    into a graceful ``ToolResult``."""


class UrlPolicyViolation(CompetitiveAgentError):
    """A URL violated the fetch safety policy (non-public scheme, private or
    loopback address, blocked host — blueprint §37.29)."""


class FixtureMissing(CompetitiveAgentError):
    """A fixture-mode component could not find a recorded fixture for the
    requested task/content; the message lists the paths that were tried."""
