"""Evaluation dataset assembly (blueprint §37.34, anti-contamination rule).

Selects a diverse, product-heavy set of REAL collected artifacts (live/cached
runs only — fixtures are synthetic and would not test the classifier on real
marketing language), then freezes a deterministic dev/held-out split so prompt
iteration can never touch held-out. The split key is a hash of the artifact id,
so it is stable across rebuilds and independent of collection order.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Source types that carry real marketing language worth classifying. Sitemaps
# and robots are navigational, never classified in production, and excluded.
_ELIGIBLE_SOURCE_TYPES = (
    "webpage",
    "exa_web",
    "comparison",
    "news",
    "reviews",
    "google_ads",
    "wayback",
)
# Target per-company counts favor product/positioning surfaces; historical
# (wayback) is required so temporal fields get at least spot coverage.
HELDOUT_FRACTION = 0.6  # held-out is the reported split; larger so numbers are honest


@dataclass
class DatasetItem:
    artifact_id: str
    company: str
    company_id: str
    url: str
    source_type: str
    split: str  # "dev" | "heldout"
    normalized_text: str


def _split_for(artifact_id: str) -> str:
    """Deterministic, order-independent split. Held-out iff hash falls in the
    top HELDOUT_FRACTION of the [0,1) space."""
    h = int(hashlib.sha256(artifact_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "heldout" if h < HELDOUT_FRACTION else "dev"


def assemble_dataset(
    db_path: Any,
    *,
    per_company: int = 18,
    max_companies: int = 3,
    min_chars: int = 200,
) -> list[DatasetItem]:
    """Pull a diverse real-artifact sample from the store, newest runs first."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in _ELIGIBLE_SOURCE_TYPES)
    rows = conn.execute(
        f"""
        SELECT a.artifact_id, a.company_id, a.url, a.source_type, a.normalized_text,
               r.company AS company, a.created_at
        FROM artifacts a JOIN runs r ON a.run_id = r.run_id
        WHERE r.execution_mode IN ('live','cached')
          AND a.source_type IN ({placeholders})
          AND length(a.normalized_text) >= ?
        ORDER BY a.created_at DESC
        """,
        (*_ELIGIBLE_SOURCE_TYPES, min_chars),
    ).fetchall()

    by_company: dict[str, list[sqlite3.Row]] = {}
    seen_hashes: set[str] = set()
    for row in rows:
        # De-dup by content so the same page collected twice isn't double-counted.
        digest = hashlib.sha256(row["normalized_text"].encode()).hexdigest()
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        by_company.setdefault(row["company"], []).append(row)

    items: list[DatasetItem] = []
    for company in list(by_company)[:max_companies]:
        picked = _diverse_pick(by_company[company], per_company)
        for row in picked:
            items.append(
                DatasetItem(
                    artifact_id=row["artifact_id"],
                    company=company,
                    company_id=row["company_id"],
                    url=row["url"] or "",
                    source_type=row["source_type"],
                    split=_split_for(row["artifact_id"]),
                    normalized_text=row["normalized_text"],
                )
            )
    return items


def _diverse_pick(rows: list[sqlite3.Row], n: int) -> list[sqlite3.Row]:
    """Round-robin across source types so one type (usually webpage) can't
    dominate the sample."""
    buckets: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        buckets.setdefault(r["source_type"], []).append(r)
    picked: list[sqlite3.Row] = []
    while len(picked) < n and any(buckets.values()):
        for st in list(buckets):
            if not buckets[st]:
                continue
            picked.append(buckets[st].pop(0))
            if len(picked) >= n:
                break
    return picked


def freeze(items: list[DatasetItem], out_path: Any) -> Path:
    """Write dataset.jsonl (split frozen). Text is not stored here — the eval
    reads artifact text from the store by id — but a short preview aids review."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for it in items:
            fh.write(
                json.dumps(
                    {
                        "artifact_id": it.artifact_id,
                        "company": it.company,
                        "company_id": it.company_id,
                        "url": it.url,
                        "source_type": it.source_type,
                        "split": it.split,
                        "text_preview": it.normalized_text[:200],
                    }
                )
                + "\n"
            )
    return path


def composition(items: list[DatasetItem]) -> dict[str, Any]:
    from collections import Counter

    return {
        "total": len(items),
        "by_company": dict(Counter(i.company for i in items)),
        "by_source_type": dict(Counter(i.source_type for i in items)),
        "by_split": dict(Counter(i.split for i in items)),
    }
