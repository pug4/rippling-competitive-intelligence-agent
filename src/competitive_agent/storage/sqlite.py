"""SQLite connection factory.

One tuned connection style for the whole app: WAL for concurrent
reader/writer friendliness, foreign keys enforced, ``sqlite3.Row`` rows so
columns are addressable by name everywhere.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating parent directories if needed) a tuned SQLite connection."""
    if str(db_path) != ":memory:":
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        target = str(path)
    else:
        target = ":memory:"
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn
