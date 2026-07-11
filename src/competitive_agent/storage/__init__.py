"""Storage layer: lean physical SQLite store, rich logical model (§40.3)."""

from __future__ import annotations

from .migrations import LATEST_USER_VERSION, TABLES, get_user_version, migrate
from .repository import (
    DEFAULT_CACHEABLE_STATUSES,
    SCHEMA_REGISTRY,
    Repository,
    canonical_args_hash,
    canonical_args_json,
    populate_schema_registry,
    register_schema,
)
from .sqlite import connect

__all__ = [
    "DEFAULT_CACHEABLE_STATUSES",
    "LATEST_USER_VERSION",
    "SCHEMA_REGISTRY",
    "TABLES",
    "Repository",
    "canonical_args_hash",
    "canonical_args_json",
    "connect",
    "get_user_version",
    "migrate",
    "populate_schema_registry",
    "register_schema",
]
