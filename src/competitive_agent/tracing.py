"""Append-only run tracing (blueprint §37.30).

Every significant event is appended as one JSON line to
``<run_dir>/trace.jsonl`` with a UTC timestamp and the run id. Event types
are validated against ``competitive_agent.schemas.trace.TRACE_EVENT_TYPES``
when that module exists — unknown types produce a warning, never a crash,
so tracing can never take down a run.

Secrets are never logged: any payload key whose name looks credential-like
(key / secret / token / password / credential / auth) has its value replaced
with a redaction marker, recursively.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REDACTED = "[REDACTED]"

# Substring match on key names, by design: over-redacting is safer than
# leaking. (Cost: benign names like "keywords" are also redacted.)
_SENSITIVE_KEY_RE = re.compile(r"key|secret|token|passw|credential|auth", re.IGNORECASE)

# Sentinel distinguishing "not yet attempted" from "attempted, unavailable".
_UNLOADED = object()
_trace_event_types: Any = _UNLOADED


def _known_event_types() -> frozenset[str] | None:
    """TRACE_EVENT_TYPES from schemas.trace if importable, else None. Cached."""
    global _trace_event_types
    if _trace_event_types is _UNLOADED:
        try:
            from competitive_agent.schemas.trace import TRACE_EVENT_TYPES

            _trace_event_types = frozenset(TRACE_EVENT_TYPES)
        except Exception:
            _trace_event_types = None
    return _trace_event_types


def redact(value: Any) -> Any:
    """Recursively replace values of credential-like keys with REDACTED."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and _SENSITIVE_KEY_RE.search(k) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class TraceWriter:
    """Appends redacted, timestamped events to ``<run_dir>/trace.jsonl``.

    ``run_id`` defaults to the run directory's name, which is the run id by
    convention (``outputs/runs/<run_id>/``).
    """

    def __init__(self, run_dir: str | Path, run_id: str | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or self.run_dir.name
        self.path = self.run_dir / "trace.jsonl"

    def append(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        known = _known_event_types()
        if known is not None and event_type not in known:
            logger.warning(
                "Unknown trace event type %r (not in schemas.trace.TRACE_EVENT_TYPES); "
                "writing it anyway",
                event_type,
            )
        record = {
            "ts": self._now_iso(),
            "run_id": self.run_id,
            "event_type": event_type,
            "payload": redact(payload or {}),
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    @staticmethod
    def _now_iso() -> str:
        from competitive_agent.schemas.common import utcnow

        return utcnow().isoformat()
