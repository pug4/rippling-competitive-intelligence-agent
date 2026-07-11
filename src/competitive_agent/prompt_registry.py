"""Versioned prompt registry (blueprint §37.28).

Prompts live at ``src/competitive_agent/prompts/*.md`` as YAML frontmatter
(``--- name / version / purpose / output_schema ---``) followed by a Jinja2
body. The registry lets every model call record the exact prompt
``(name, version)`` it used, and re-parses a file when its mtime changes so
prompt edits are picked up without a process restart.

Rendering uses ``StrictUndefined``: a missing template variable is a hard
error, never silently-empty prompt text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined, Template
from pydantic import BaseModel

from .exceptions import CompetitiveAgentError

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


class Prompt(BaseModel):
    name: str
    version: str
    purpose: str = ""
    output_schema: str = ""
    body_template: str

    def render(self, **kwargs: Any) -> str:
        return Template(self.body_template, undefined=StrictUndefined).render(**kwargs)


def _parse_prompt_file(path: Path) -> Prompt:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise CompetitiveAgentError(
            f"prompt file {path} is missing YAML frontmatter (--- name/version/... ---)"
        )
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict):
        raise CompetitiveAgentError(f"prompt file {path} frontmatter is not a YAML mapping")
    return Prompt(
        name=str(meta.get("name", path.stem)),
        version=str(meta.get("version", "0.0.0")),
        purpose=str(meta.get("purpose", "")),
        output_schema=str(meta.get("output_schema", "")),
        body_template=text[match.end() :],
    )


class PromptRegistry:
    """Loads prompts by frontmatter ``name``, caching parses by file mtime."""

    def __init__(self, prompts_dir: Path | str = PROMPTS_DIR) -> None:
        self._dir = Path(prompts_dir)
        self._cache: dict[Path, tuple[float, Prompt]] = {}

    def get(self, name: str) -> Prompt:
        for path in sorted(self._dir.glob("*.md")):
            prompt = self._load(path)
            if prompt.name == name:
                return prompt
        raise KeyError(f"no prompt named {name!r} under {self._dir}")

    def _load(self, path: Path) -> Prompt:
        mtime = path.stat().st_mtime
        cached = self._cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        prompt = _parse_prompt_file(path)
        self._cache[path] = (mtime, prompt)
        return prompt
