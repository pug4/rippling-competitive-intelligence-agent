"""Typed settings and configuration loading.

Environment (secrets, run defaults) comes from ``.env`` via pydantic-settings.
Behavioral configuration (flags, taxonomy, model routes, source capabilities)
comes from YAML files under ``config/`` so the tool can be retargeted to a
different focal company or industry without code changes.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"

ExecutionMode = Literal["live", "cached", "fixture"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = ""
    exa_api_key: str = ""
    perplexity_api_key: str = ""
    # Optional provider seams (each degrades typed when absent).
    gemini_api_key: str = ""
    gemini_model: str = ""
    semrush_api_key: str = ""
    meta_ads_access_token: str = ""

    playwright_enabled: bool = False

    default_run_mode: ExecutionMode = "cached"
    default_research_budget_usd: float = 5.00
    default_max_runtime_seconds: int = 600
    default_lookback_days: int = 365
    max_parallel_fetches: int = 5
    max_parallel_extractions: int = 4
    per_domain_concurrency: int = 2
    log_level: str = "INFO"

    db_path: Path = REPO_ROOT / "outputs" / "agent.db"
    outputs_dir: Path = REPO_ROOT / "outputs"
    fixtures_dir: Path = REPO_ROOT / "tests" / "fixtures"


class FocalCompanyConfig(BaseModel):
    name: str = "Rippling"
    domain: str = "rippling.com"


class AppConfig(BaseModel):
    """Parsed config/default.yaml plus sibling YAML documents."""

    focal_company: FocalCompanyConfig
    sources: dict[str, bool]
    execution: dict[str, Any]
    budgets: dict[str, Any]
    portfolio: dict[str, Any]
    windows: dict[str, Any]
    collection: dict[str, Any] = {}
    exa_agent: dict[str, Any] = {}
    taxonomy: dict[str, Any]
    model_routes: dict[str, Any]
    source_capabilities: dict[str, Any]


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    default = _load_yaml(CONFIG_DIR / "default.yaml")
    return AppConfig(
        focal_company=FocalCompanyConfig(**default.get("focal_company", {})),
        sources=default.get("sources", {}),
        execution=default.get("execution", {}),
        budgets=default.get("budgets", {}),
        portfolio=default.get("portfolio", {}),
        windows=default.get("windows", {}),
        collection=default.get("collection", {}),
        exa_agent=default.get("exa_agent", {}),
        taxonomy=_load_yaml(CONFIG_DIR / "taxonomy.yaml"),
        model_routes=_load_yaml(CONFIG_DIR / "model_routes.yaml"),
        source_capabilities=_load_yaml(CONFIG_DIR / "source_capabilities.yaml"),
    )


def reset_config_cache() -> None:
    get_settings.cache_clear()
    get_config.cache_clear()


def secret_from_env_or_settings(name: str) -> str:
    """Provider-secret lookup for seams keyed by environment variable name.

    The process environment wins WHEN THE VARIABLE IS PRESENT — including
    present-but-empty, which tests use to force the keyless path. Otherwise
    the .env-backed Settings field of the same lowercase name supplies it
    (pydantic-settings loads .env into the Settings object, never os.environ).
    """
    import os

    if name in os.environ:
        return os.environ[name].strip()
    return str(getattr(get_settings(), name.lower(), "") or "").strip()
