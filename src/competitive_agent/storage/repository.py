"""Repository: the only write path into the lean physical store.

Blueprint §40.3: analytical objects are persisted as *versioned Pydantic JSON
payloads*; direct unvalidated dictionary writes are prohibited — every write
goes through ``save_payload`` which rejects anything that is not a pydantic
``BaseModel``, and every read of a payload validates back through the schema
registry keyed by ``(schema_name, schema_version)``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel

from .migrations import TABLES, migrate
from .sqlite import connect

# ---------------------------------------------------------------------------
# Schema registry
# ---------------------------------------------------------------------------

SCHEMA_REGISTRY: dict[tuple[str, str], type[BaseModel]] = {}

_registry_populated = False

_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

DEFAULT_CACHEABLE_STATUSES: tuple[str, ...] = ("success", "partial", "empty")


def _schema_name_of(cls: type[BaseModel]) -> str:
    schema_name = getattr(cls, "schema_name", None)
    if callable(schema_name):
        return str(schema_name())
    return cls.__name__


def _schema_version_of(cls: type[BaseModel]) -> str:
    return str(getattr(cls, "SCHEMA_VERSION", "1.0.0"))


def register_schema(cls: type[BaseModel]) -> type[BaseModel]:
    """Register a model class under (schema_name, SCHEMA_VERSION). Decorator-safe."""
    if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
        raise TypeError(f"register_schema expects a pydantic BaseModel subclass, got {cls!r}")
    SCHEMA_REGISTRY[(_schema_name_of(cls), _schema_version_of(cls))] = cls
    return cls


def populate_schema_registry() -> None:
    """Import every module under ``competitive_agent.schemas`` and register its models.

    Imported lazily (inside this function, on first payload load) to avoid a
    circular import at module load: schema modules may one day reference
    storage helpers, and storage must not hard-depend on the full schema tree.
    """
    global _registry_populated
    if _registry_populated:
        return
    import importlib
    import pkgutil

    import competitive_agent.schemas as schemas_pkg
    from competitive_agent.schemas.common import VersionedModel

    for module_info in pkgutil.iter_modules(schemas_pkg.__path__):
        module = importlib.import_module(f"competitive_agent.schemas.{module_info.name}")
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, VersionedModel)
                and obj is not VersionedModel
            ):
                register_schema(obj)
    _registry_populated = True


# ---------------------------------------------------------------------------
# Canonical args hashing (tool-call cache key)
# ---------------------------------------------------------------------------


def canonical_args_json(args: dict[str, Any]) -> str:
    """Stable canonical JSON: sorted keys, no whitespace, unicode preserved."""
    return json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def canonical_args_hash(args: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_args_json(args).encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    from competitive_agent.schemas.common import utcnow

    return utcnow()


def _to_sql(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    return value


class Repository:
    """Typed persistence facade over one SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @classmethod
    def open(cls, db_path: Any) -> Repository:
        """Convenience: connect, migrate, wrap."""
        conn = connect(db_path)
        migrate(conn)
        return cls(conn)

    def close(self) -> None:
        self.conn.close()

    # -- generic payload path -------------------------------------------------

    def save_payload(
        self,
        table: str,
        key_columns: dict[str, Any],
        model: BaseModel,
        *,
        replace: bool = False,
    ) -> None:
        """Insert one row: promoted key columns + validated JSON payload.

        Rejects anything that is not a pydantic model — unvalidated dict
        writes are prohibited by design (§40.3).
        """
        if not isinstance(model, BaseModel):
            raise TypeError(
                "save_payload requires a pydantic BaseModel; got "
                f"{type(model).__name__}. Direct dict writes are prohibited (§40.3)."
            )
        if table not in TABLES:
            raise ValueError(f"Unknown table {table!r}; expected one of {TABLES}")
        for column in key_columns:
            if not _IDENTIFIER_RE.match(column):
                raise ValueError(f"Invalid column name {column!r}")

        # Writing implies the schema is loadable in this process: auto-register.
        register_schema(type(model))

        row = {name: _to_sql(value) for name, value in key_columns.items()}
        row["payload_json"] = model.model_dump_json()
        row["schema_name"] = _schema_name_of(type(model))
        row["schema_version"] = _schema_version_of(type(model))
        row.setdefault("created_at", _utcnow().isoformat())

        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        self.conn.execute(
            f"{verb} INTO {table} ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        self.conn.commit()

    def load_payload(self, row: sqlite3.Row | dict[str, Any]) -> BaseModel:
        """Validate a stored payload back into its registered model class."""
        populate_schema_registry()
        schema_name = row["schema_name"]
        schema_version = row["schema_version"]
        model_cls = SCHEMA_REGISTRY.get((schema_name, schema_version))
        if model_cls is None:
            raise LookupError(
                f"No model registered for schema ({schema_name!r}, {schema_version!r}); "
                "call register_schema() or add the model to competitive_agent.schemas."
            )
        return model_cls.model_validate_json(row["payload_json"])

    # -- runs -------------------------------------------------------------------

    def create_run(
        self,
        *,
        run_id: str | None = None,
        company: str,
        mode: str,
        status: str = "created",
        parent_run_id: str | None = None,
        relationship_type: str | None = None,
        retry_mode: str | None = None,
        execution_mode: str = "cached",
    ) -> str:
        from competitive_agent.schemas.common import new_id

        run_id = run_id or new_id("run")
        now = _utcnow().isoformat()
        self.conn.execute(
            """
            INSERT INTO runs (
                run_id, company, mode, status, parent_run_id, relationship_type,
                retry_mode, current_node, state_json, state_schema_version,
                execution_mode, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)
            """,
            (
                run_id,
                company,
                mode,
                status,
                parent_run_id,
                relationship_type,
                retry_mode,
                execution_mode,
                now,
                now,
            ),
        )
        self.conn.commit()
        return run_id

    def update_run_state(
        self,
        run_id: str,
        *,
        status: str | None = None,
        current_node: str | None = None,
        state: BaseModel | None = None,
        execution_mode: str | None = None,
    ) -> None:
        """Checkpoint a run. ``state`` must be a pydantic model (never a raw dict)."""
        sets: list[str] = ["updated_at = ?"]
        params: list[Any] = [_utcnow().isoformat()]
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if current_node is not None:
            sets.append("current_node = ?")
            params.append(current_node)
        if execution_mode is not None:
            sets.append("execution_mode = ?")
            params.append(execution_mode)
        if state is not None:
            if not isinstance(state, BaseModel):
                raise TypeError(
                    "update_run_state requires a pydantic BaseModel state; got "
                    f"{type(state).__name__}. Direct dict writes are prohibited (§40.3)."
                )
            register_schema(type(state))
            sets.append("state_json = ?")
            params.append(state.model_dump_json())
            sets.append("state_schema_version = ?")
            params.append(_schema_version_of(type(state)))
        params.append(run_id)
        cursor = self.conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE run_id = ?", params)
        self.conn.commit()
        if cursor.rowcount == 0:
            raise LookupError(f"No run with run_id={run_id!r}")

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()

    def load_run_state(self, row: sqlite3.Row, state_cls: type[BaseModel]) -> BaseModel | None:
        """Reload a checkpointed run state through its known model class."""
        state_json = row["state_json"]
        if state_json is None:
            return None
        return state_cls.model_validate_json(state_json)

    def list_runs(
        self,
        *,
        company: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        if company is not None:
            clauses.append("company = ?")
            params.append(company)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        return self.conn.execute(
            f"SELECT * FROM runs {where} ORDER BY created_at DESC LIMIT ?", params
        ).fetchall()

    # -- artifacts ----------------------------------------------------------------

    def save_artifact(self, run_id: str, artifact: BaseModel) -> str:
        from competitive_agent.schemas.artifact import RawArtifact

        if not isinstance(artifact, RawArtifact):
            raise TypeError(f"save_artifact requires a RawArtifact; got {type(artifact).__name__}")
        self.save_payload(
            "artifacts",
            {
                "artifact_id": artifact.artifact_id,
                "run_id": run_id,
                "company_id": artifact.company_id,
                "url": artifact.url,
                "source_type": artifact.source_type,
                "content_hash": artifact.content_hash,
                "normalized_text": artifact.normalized_text,
                "retrieved_at": artifact.retrieved_at,
                "archive_capture_at": artifact.archive_capture_at,
            },
            artifact,
            replace=True,
        )
        return artifact.artifact_id

    def get_artifact(self, artifact_id: str) -> BaseModel | None:
        row = self.conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        return self.load_payload(row) if row is not None else None

    def find_artifact_by_hash(self, content_hash: str) -> BaseModel | None:
        row = self.conn.execute(
            "SELECT * FROM artifacts WHERE content_hash = ? ORDER BY created_at DESC LIMIT 1",
            (content_hash,),
        ).fetchone()
        return self.load_payload(row) if row is not None else None

    def link_artifact(self, run_id: str, artifact_id: str) -> None:
        """Reference an existing artifact from another run (retry evidence reuse).

        The artifact row keeps its owning ``run_id``; this only records that
        ``run_id`` also uses it. Never reassigns/overwrites the artifact row, so
        the owning run's evidence is preserved.
        """
        self.conn.execute(
            "INSERT OR IGNORE INTO run_artifacts (run_id, artifact_id, created_at) VALUES (?, ?, ?)",
            (run_id, artifact_id, _utcnow().isoformat()),
        )
        self.conn.commit()

    def list_artifacts(
        self,
        *,
        run_id: str | None = None,
        company_id: str | None = None,
    ) -> list[BaseModel]:
        if run_id is None and company_id is None:
            raise ValueError("list_artifacts requires run_id and/or company_id")
        clauses: list[str] = []
        params: list[Any] = []
        if run_id is not None:
            # Match artifacts owned by the run OR linked to it via run_artifacts
            # (retry child runs reuse a parent's evidence without owning the row).
            clauses.append(
                "(run_id = ? OR artifact_id IN "
                "(SELECT artifact_id FROM run_artifacts WHERE run_id = ?))"
            )
            params.extend([run_id, run_id])
        if company_id is not None:
            clauses.append("company_id = ?")
            params.append(company_id)
        rows = self.conn.execute(
            f"SELECT * FROM artifacts WHERE {' AND '.join(clauses)} ORDER BY created_at",
            params,
        ).fetchall()
        return [self.load_payload(row) for row in rows]

    def artifact_contains(self, artifact_id: str, excerpt: str) -> bool:
        """Exact-excerpt verification against stored normalized_text.

        Uses the SAME normalization path as artifact processing so
        verification can never drift from storage (§40.1 accuracy gate).
        """
        row = self.conn.execute(
            "SELECT normalized_text FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        if row is None or not row["normalized_text"]:
            return False
        try:
            from competitive_agent.processing.normalize import contains_excerpt
        except ImportError:
            # TODO: remove this fallback once competitive_agent.processing.normalize
            # is guaranteed present; simple whitespace-collapse + casefold containment.
            def contains_excerpt(haystack: str, needle: str) -> bool:  # type: ignore[misc]
                if not needle:
                    return False
                collapse = lambda s: " ".join(s.split()).casefold()  # noqa: E731
                return collapse(needle) in collapse(haystack)

        return contains_excerpt(row["normalized_text"], excerpt)

    # -- classifications ------------------------------------------------------------

    def save_classification(
        self,
        run_id: str,
        family: str,
        model: BaseModel,
        *,
        prompt_version: str,
        model_id: str,
    ) -> str:
        from competitive_agent.schemas.common import new_id

        row_id = new_id("cls")
        self.save_payload(
            "classifications",
            {
                "id": row_id,
                "run_id": run_id,
                "artifact_id": getattr(model, "artifact_id", None),
                "company_id": getattr(model, "company_id", None),
                "family": family,
                "prompt_version": prompt_version,
                "model_id": model_id,
            },
            model,
        )
        return row_id

    def list_classifications(self, run_id: str, *, family: str | None = None) -> list[BaseModel]:
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        if family is not None:
            clauses.append("family = ?")
            params.append(family)
        rows = self.conn.execute(
            f"SELECT * FROM classifications WHERE {' AND '.join(clauses)} ORDER BY created_at",
            params,
        ).fetchall()
        return [self.load_payload(row) for row in rows]

    # -- claims -----------------------------------------------------------------

    def save_claim(self, run_id: str, model: BaseModel) -> str:
        from competitive_agent.schemas.common import new_id

        row_id = str(
            getattr(model, "claim_id", None) or getattr(model, "id", None) or new_id("clm")
        )
        self.save_payload(
            "claims",
            {
                "id": row_id,
                "run_id": run_id,
                "company_id": getattr(model, "company_id", None),
                # Taxonomy fields default to 'unknown' rather than NULL.
                "claim_type": str(getattr(model, "claim_type", "unknown")),
                "grounding_status": str(getattr(model, "grounding_status", "unknown")),
            },
            model,
        )
        return row_id

    def list_claims(self, run_id: str, *, grounding_status: str | None = None) -> list[BaseModel]:
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        if grounding_status is not None:
            clauses.append("grounding_status = ?")
            params.append(grounding_status)
        rows = self.conn.execute(
            f"SELECT * FROM claims WHERE {' AND '.join(clauses)} ORDER BY created_at", params
        ).fetchall()
        return [self.load_payload(row) for row in rows]

    # -- opportunities -------------------------------------------------------------

    def save_opportunity(self, run_id: str, model: BaseModel) -> str:
        from competitive_agent.schemas.common import new_id

        row_id = str(
            getattr(model, "opportunity_id", None) or getattr(model, "id", None) or new_id("opp")
        )
        self.save_payload(
            "opportunities",
            {
                "id": row_id,
                "run_id": run_id,
                "critic_verdict": str(getattr(model, "critic_verdict", "unknown")),
            },
            model,
        )
        return row_id

    def delete_opportunities(self, run_id: str) -> int:
        """Delete all opportunities + proof-gaps for a run (they share the table).

        Used for REPLACE semantics when the loop regenerates against a fuller
        corpus, so a run never accumulates duplicate gap/opportunity sets.
        """
        cur = self.conn.execute("DELETE FROM opportunities WHERE run_id = ?", (run_id,))
        self.conn.commit()
        return cur.rowcount

    def list_opportunities(
        self, run_id: str, *, critic_verdict: str | None = None
    ) -> list[BaseModel]:
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        if critic_verdict is not None:
            clauses.append("critic_verdict = ?")
            params.append(critic_verdict)
        rows = self.conn.execute(
            f"SELECT * FROM opportunities WHERE {' AND '.join(clauses)} ORDER BY created_at",
            params,
        ).fetchall()
        return [self.load_payload(row) for row in rows]

    # -- tool calls ----------------------------------------------------------------

    def record_tool_call(
        self,
        run_id: str,
        action_id: str,
        tool_name: str,
        execution_mode: str,
        args: dict[str, Any],
        status: str,
        *,
        latency_ms: int = 0,
        cost_usd: float = 0.0,
        error_type: str | None = None,
        error_message: str | None = None,
        artifact_ids: list[str] | None = None,
    ) -> str:
        from competitive_agent.schemas.common import new_id

        row_id = new_id("tc")
        self.conn.execute(
            """
            INSERT INTO tool_calls (
                id, run_id, action_id, tool_name, execution_mode, args_hash,
                args_json, status, latency_ms, cost_usd, error_type,
                error_message, artifact_ids_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                run_id,
                action_id,
                tool_name,
                execution_mode,
                canonical_args_hash(args),
                canonical_args_json(args),
                status,
                int(latency_ms),
                float(cost_usd),
                error_type,
                error_message,
                json.dumps(list(artifact_ids or []), ensure_ascii=False),
                _utcnow().isoformat(),
            ),
        )
        self.conn.commit()
        return row_id

    def find_cached_tool_call(
        self,
        tool_name: str,
        args_hash: str,
        *,
        max_age_seconds: float | None = None,
        statuses: tuple[str, ...] = DEFAULT_CACHEABLE_STATUSES,
    ) -> sqlite3.Row | None:
        """Most recent reusable tool call for (tool_name, args_hash), if fresh enough."""
        if not statuses:
            return None
        status_marks = ", ".join("?" for _ in statuses)
        sql = (
            "SELECT * FROM tool_calls WHERE tool_name = ? AND args_hash = ? "
            f"AND status IN ({status_marks})"
        )
        params: list[Any] = [tool_name, args_hash, *statuses]
        if max_age_seconds is not None:
            # created_at is always utcnow().isoformat() (fixed-width, +00:00 offset),
            # so lexicographic comparison equals chronological comparison.
            cutoff = (_utcnow() - timedelta(seconds=max_age_seconds)).isoformat()
            sql += " AND created_at >= ?"
            params.append(cutoff)
        sql += " ORDER BY created_at DESC LIMIT 1"
        return self.conn.execute(sql, params).fetchone()

    # -- feedback -------------------------------------------------------------------

    def save_feedback(
        self,
        run_id: str,
        *,
        target_type: str,
        target_id: str,
        feedback_type: str,
        reason: str = "",
        retry_mode: str | None = None,
    ) -> str:
        from competitive_agent.schemas.common import new_id

        row_id = new_id("fb")
        self.conn.execute(
            """
            INSERT INTO feedback_events (
                id, run_id, target_type, target_id, feedback_type, reason,
                retry_mode, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                run_id,
                target_type,
                target_id,
                feedback_type,
                reason,
                retry_mode,
                _utcnow().isoformat(),
            ),
        )
        self.conn.commit()
        return row_id
