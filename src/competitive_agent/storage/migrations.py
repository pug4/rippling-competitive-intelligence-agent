"""Schema migrations: lean physical model, rich logical model (blueprint §40.3).

Exactly seven physical tables. Schema-flexible analytical objects live as
versioned Pydantic JSON payloads (``payload_json`` + ``schema_name`` +
``schema_version``) inside them; only the columns needed for lookup, lineage
and caching are promoted to real columns. Versioning uses
``PRAGMA user_version`` — each entry in ``_MIGRATIONS`` is applied once, in
order, and the pragma is bumped after each.
"""

from __future__ import annotations

import sqlite3

_V1_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        company TEXT,
        mode TEXT,
        status TEXT,
        parent_run_id TEXT,
        relationship_type TEXT,
        retry_mode TEXT,
        current_node TEXT,
        state_json TEXT,
        state_schema_version TEXT,
        execution_mode TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        artifact_id TEXT PRIMARY KEY,
        run_id TEXT,
        company_id TEXT,
        url TEXT,
        source_type TEXT,
        content_hash TEXT,
        normalized_text TEXT,
        retrieved_at TEXT,
        archive_capture_at TEXT,
        payload_json TEXT,
        schema_name TEXT,
        schema_version TEXT,
        created_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_artifacts_run_id ON artifacts (run_id)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_company_id ON artifacts (company_id)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_content_hash ON artifacts (content_hash)",
    """
    CREATE TABLE IF NOT EXISTS classifications (
        id TEXT PRIMARY KEY,
        run_id TEXT,
        artifact_id TEXT,
        company_id TEXT,
        family TEXT,
        payload_json TEXT,
        schema_name TEXT,
        schema_version TEXT,
        prompt_version TEXT,
        model_id TEXT,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS claims (
        id TEXT PRIMARY KEY,
        run_id TEXT,
        company_id TEXT,
        claim_type TEXT,
        grounding_status TEXT,
        payload_json TEXT,
        schema_name TEXT,
        schema_version TEXT,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS opportunities (
        id TEXT PRIMARY KEY,
        run_id TEXT,
        critic_verdict TEXT,
        payload_json TEXT,
        schema_name TEXT,
        schema_version TEXT,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_calls (
        id TEXT PRIMARY KEY,
        run_id TEXT,
        action_id TEXT,
        tool_name TEXT,
        execution_mode TEXT,
        args_hash TEXT,
        args_json TEXT,
        status TEXT,
        latency_ms INTEGER,
        cost_usd REAL,
        error_type TEXT,
        error_message TEXT,
        artifact_ids_json TEXT,
        created_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tool_calls_cache ON tool_calls (tool_name, args_hash)",
    """
    CREATE TABLE IF NOT EXISTS feedback_events (
        id TEXT PRIMARY KEY,
        run_id TEXT,
        target_type TEXT,
        target_id TEXT,
        feedback_type TEXT,
        reason TEXT,
        retry_mode TEXT,
        created_at TEXT
    )
    """,
]

# Association of artifacts to runs beyond the owning run (retry child runs reuse
# a parent's evidence). An artifact row is owned by exactly one run (its
# ``run_id`` column); additional runs reference it here. This lets a retry share
# the parent's evidence WITHOUT reassigning the parent's rows (which would
# silently destroy the parent's analysis).
_V2_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS run_artifacts (
        run_id TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        created_at TEXT,
        PRIMARY KEY (run_id, artifact_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_run_artifacts_run ON run_artifacts (run_id)",
]

# version -> list of DDL statements. Append new versions; never edit old ones.
_MIGRATIONS: dict[int, list[str]] = {
    1: _V1_STATEMENTS,
    2: _V2_STATEMENTS,
}

LATEST_USER_VERSION: int = max(_MIGRATIONS)

TABLES: tuple[str, ...] = (
    "runs",
    "artifacts",
    "classifications",
    "claims",
    "opportunities",
    "tool_calls",
    "feedback_events",
)


def get_user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations; return the resulting user_version. Idempotent."""
    current = get_user_version(conn)
    for version in sorted(_MIGRATIONS):
        if version <= current:
            continue
        for statement in _MIGRATIONS[version]:
            conn.execute(statement)
        # PRAGMA does not accept bound parameters; version is a trusted int key.
        conn.execute(f"PRAGMA user_version = {int(version)}")
        conn.commit()
        current = version
    return current
