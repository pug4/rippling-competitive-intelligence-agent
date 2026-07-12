from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .common import VersionedModel


class RawArtifact(VersionedModel):
    artifact_id: str
    company_id: str
    source_type: str
    source_name: str
    url: str
    final_url: str
    title: str | None = None
    # The person who authored the content (e.g. a LinkedIn post's poster). None
    # for company-page/website content that has no individual author.
    author: str | None = None
    published_at: datetime | None = None
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    retrieved_at: datetime
    time_window_ids: list[str] = Field(default_factory=list)
    raw_text: str = ""
    normalized_text: str = ""
    content_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    screenshot_path: str | None = None
    collection_method: str
    is_partial: bool = False
    is_fixture: bool = False
    # Wayback and other archives: the ACTUAL capture timestamp, never the
    # requested date (§40.1 accuracy gate).
    archive_capture_at: datetime | None = None
