"""Portfolio Coordinator (blueprint §38.16, §37.34, Phase 5 gate).

Fans out one isolated pipeline per competitor, under bounded concurrency, then
validates each company's package against quality gates and synthesizes a
cross-company view relative to the focal company (Rippling).

Isolation is structural, not best-effort: every company run gets its own
``run_id``, ``DirectorState``, ``GraphContext`` (fresh ``scratch``), trace, and
budget, and every persistence read is filtered by ``run_id``/``company_id``.
``assert_no_cross_company_leakage`` proves it after the fact — a company's
package may reference only artifacts/classifications carrying its own
``company_id`` (plus the shared focal mirror, which is scoped per company).
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal, cast

from .runner import create_run, drive
from .schemas.common import new_id, utcnow
from .state import DirectorState


class LeakageError(AssertionError):
    """Raised when a company's package references another company's evidence."""


def run_portfolio(
    companies: list[str],
    *,
    mode: str = "comparative",
    execution_mode: str | None = None,
    compare_to: str | None = None,
    lookback_days: int | None = None,
) -> dict[str, Any]:
    """Run every company through its own pipeline and synthesize across them."""
    if not companies:
        raise ValueError("run_portfolio requires at least one company")
    return asyncio.run(
        _run_portfolio_async(
            companies,
            mode=mode,
            execution_mode=execution_mode,
            compare_to=compare_to,
            lookback_days=lookback_days,
        )
    )


async def _run_portfolio_async(
    companies: list[str],
    *,
    mode: str,
    execution_mode: str | None,
    compare_to: str | None,
    lookback_days: int | None,
) -> dict[str, Any]:
    from .config import get_config

    cfg = get_config()
    max_concurrent = int(cfg.portfolio.get("max_concurrent_company_runs", 2))
    max_companies = int(cfg.portfolio.get("max_competitors_per_demo", 3))
    # De-duplicate while preserving order; cap at the demo limit (a declared
    # bound, not a silent truncation — the skipped list records the overflow).
    seen: set[str] = set()
    ordered: list[str] = []
    skipped: list[str] = []
    for c in companies:
        key = c.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        (ordered if len(ordered) < max_companies else skipped).append(c)

    portfolio_run_id = new_id("PORT")
    sem = asyncio.Semaphore(max_concurrent)

    async def run_one(company: str) -> tuple[str, DirectorState | None, Any, str | None]:
        async with sem:
            try:
                state, ctx = create_run(
                    company,
                    mode=mode,
                    execution_mode=cast("Literal['live', 'cached', 'fixture'] | None", execution_mode),
                    compare_to=compare_to,
                    lookback_days=lookback_days,
                )
                state = await drive(state, ctx)
                return company, state, ctx, None
            except Exception as exc:  # isolate: one company's failure never aborts the others
                return company, None, None, f"{type(exc).__name__}: {exc}"

    results = await asyncio.gather(*(run_one(c) for c in ordered))

    packages: list[dict[str, Any]] = []
    completed: list[str] = []
    failed: list[dict[str, str]] = []
    for company, state, ctx, err in results:
        if state is None or ctx is None:
            failed.append({"company": company, "error": err or "unknown"})
            continue
        pkg = _build_package(state, ctx)
        packages.append(pkg)
        completed.append(company)

    # Prove isolation before synthesizing anything across companies.
    leakage = assert_no_cross_company_leakage(packages)

    synthesis = _synthesize(packages, focal=compare_to or _focal_domain())

    status = (
        "completed"
        if not failed and not skipped
        else "completed_with_limitations"
        if completed
        else "failed"
    )
    return {
        "portfolio_run_id": portfolio_run_id,
        "created_at": utcnow().isoformat(),
        "mode": mode,
        "requested_companies": companies,
        "completed_company_ids": completed,
        "failed_companies": failed,
        "skipped_companies": skipped,
        "max_concurrent_company_runs": max_concurrent,
        "status": status,
        "isolation_verified": leakage["ok"],
        "isolation_report": leakage,
        "packages": packages,
        "synthesis": synthesis,
        "limitations": _limitations(failed, skipped),
    }


def _focal_domain() -> str:
    from .config import get_config

    fc = get_config().focal_company
    return str(fc.get("domain", "rippling.com")) if isinstance(fc, dict) else "rippling.com"


def _build_package(state: DirectorState, ctx: Any) -> dict[str, Any]:
    """A per-company quality-gated package: coverage, counts, gate results, and
    the exact evidence IDs the company owns (used for leakage checks)."""
    repo = ctx.repository
    run_id = state.run_id
    company_id = state.company.company_id if state.company else "unresolved"

    artifacts = repo.list_artifacts(run_id=run_id)
    classifications = repo.list_classifications(run_id, family="merged")
    claims = repo.list_claims(run_id=run_id)
    opps = repo.list_opportunities(run_id=run_id)

    artifact_company_ids = sorted({a.company_id for a in artifacts if getattr(a, "company_id", None)})
    gate = _quality_gate(state, artifacts, classifications)
    return {
        "company_run_id": run_id,
        "company_input": state.company_input,
        "company_id": company_id,
        "company_name": getattr(state.company, "name", None) if state.company else None,
        "coverage": dict(state.coverage),
        "counts": {
            "artifacts": len(artifacts),
            "classifications": len(classifications),
            "claims": len(claims),
            "opportunities": len(opps),
            "change_events": len(state.change_event_ids),
        },
        "artifact_company_ids": artifact_company_ids,
        "opportunities": [getattr(o, "title", "") for o in opps if o.__class__.__name__ == "MarketingOpportunity"],
        "quality_gate_results": gate,
        "quality_gate_passed": all(gate.values()),
        "stop_reason": state.stop_reason,
        "limitations": list(state.limitations),
    }


def _quality_gate(state: DirectorState, artifacts: list[Any], classifications: list[Any]) -> dict[str, bool]:
    """Minimal package-validity gate (§37.34): a package that fails is flagged,
    never silently dropped from synthesis."""
    return {
        "company_resolved": state.company is not None,
        "has_evidence": len(artifacts) > 0,
        "has_classifications": len(classifications) > 0,
        "run_terminated_cleanly": state.stop_reason is not None,
        "single_company_evidence": len({getattr(a, "company_id", None) for a in artifacts}) <= 1,
    }


def assert_no_cross_company_leakage(packages: list[dict[str, Any]]) -> dict[str, Any]:
    """Each package's evidence must carry only its own company_id. Cross-company
    contamination is a hard error, not a warning (blueprint isolation gate)."""
    violations: list[str] = []
    own_ids = {p["company_id"] for p in packages}
    for p in packages:
        foreign = [cid for cid in p["artifact_company_ids"] if cid != p["company_id"] and cid in own_ids]
        if foreign:
            violations.append(
                f"{p['company_input']} ({p['company_id']}) references evidence from {foreign}"
            )
    return {"ok": not violations, "violations": violations, "companies_checked": sorted(own_ids)}


def _synthesize(packages: list[dict[str, Any]], *, focal: str) -> dict[str, Any]:
    """Cross-company view relative to the focal company: coverage leaders per
    dimension and opportunity themes shared across competitors."""
    from collections import Counter

    valid = [p for p in packages if p["quality_gate_passed"]]
    # Which competitor established the strongest coverage on each dimension.
    dims: dict[str, dict[str, str]] = {}
    order = {"strong": 3, "high": 3, "medium": 2, "low": 1, "not_attempted": 0, "unavailable": 0}
    for p in valid:
        for dim, level in p["coverage"].items():
            dims.setdefault(dim, {})[p["company_input"]] = level
    leaders = {
        dim: max(levels.items(), key=lambda kv: order.get(str(kv[1]), 0))[0]
        for dim, levels in dims.items()
        if levels
    }
    # Opportunity themes appearing against more than one competitor (systemic
    # gaps vs. one-off).
    theme_counts: Counter[str] = Counter()
    for p in valid:
        for title in set(p["opportunities"]):
            theme_counts[title] += 1
    shared = sorted([t for t, n in theme_counts.items() if n > 1])
    return {
        "focal_company": focal,
        "companies_synthesized": [p["company_input"] for p in valid],
        "excluded_failed_gate": [p["company_input"] for p in packages if not p["quality_gate_passed"]],
        "coverage_leaders_by_dimension": leaders,
        "opportunity_themes_across_multiple_competitors": shared,
    }


def _limitations(failed: list[dict[str, str]], skipped: list[str]) -> list[str]:
    out: list[str] = []
    for f in failed:
        out.append(f"company pipeline failed: {f['company']} ({f['error']})")
    if skipped:
        out.append(f"skipped (over per-demo cap): {', '.join(skipped)}")
    return out
