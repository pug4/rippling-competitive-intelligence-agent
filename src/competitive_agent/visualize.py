"""Self-contained HTML dashboard from a run's JSON package.

Pre-renders the same charts the React UI shows (source mix, message themes,
competitive stance, message-proof gaps, persona x channel heatmap) as static
inline HTML/CSS — no JS, no external hosts — so the visuals open anywhere and
render identically. This is a rendering of the SAME validated package (§40.6):
no separate analytics.
"""

from __future__ import annotations

import html
from collections import Counter
from typing import Any

_PROOF_LEVEL = {"none": 0, "weak": 1, "moderate": 2, "medium": 2, "strong": 3, "high": 3}
_ATTACK_COLOR = {"high": "#4ade80", "medium": "#fbbf24", "low": "#f87171"}


def _esc(s: Any) -> str:
    return html.escape(str(s))


def _hbar(data: list[tuple[str, int]], color: str) -> str:
    if not data:
        return "<p class='empty'>No data.</p>"
    mx = max(1, max(v for _, v in data))
    rows = []
    for label, value in data:
        pct = value / mx * 100
        rows.append(
            f"<div class='hrow'><div class='hlabel' title='{_esc(label)}'>{_esc(label)}</div>"
            f"<div class='htrack'><div class='hfill' style='width:{pct:.1f}%;background:{color}'></div></div>"
            f"<div class='hval'>{value}</div></div>"
        )
    return f"<div class='hbar'>{''.join(rows)}</div>"


def _proof_bar(strength: str | None, tag: str) -> str:
    lvl = _PROOF_LEVEL.get(str(strength or "none").lower(), 0)
    color = "#4ade80" if lvl >= 3 else "#fbbf24" if lvl == 2 else "#f87171" if lvl >= 1 else "#2a2f3a"
    segs = "".join(
        f"<span class='seg' style='background:{color if i <= lvl else '#1f232c'}'></span>"
        for i in (1, 2, 3)
    )
    return (
        f"<span class='pbar'><span class='ptag'>{_esc(tag)}</span>{segs}"
        f"<span class='plabel'>{_esc(strength or 'none')}</span></span>"
    )


def _gaps(pkg: dict[str, Any], competitor: str, focal: str) -> str:
    gaps = pkg.get("proof_gaps", [])[:8]
    if not gaps:
        return "<p class='empty'>No repeated competitor claim with a proof gap was observed.</p>"
    rows = []
    for g in gaps:
        att = str(g.get("attackability", "low"))
        pill = f"<span class='pill' style='color:{_ATTACK_COLOR.get(att, '#9aa3b2')};border-color:{_ATTACK_COLOR.get(att, '#9aa3b2')}'>{_esc(att)}</span>"
        missing = ", ".join(g.get("missing_proof", []) or []) or "—"
        rows.append(
            f"<div class='gaprow'><div><div class='glabel'>{_esc(g.get('short_label'))}</div>{pill}</div>"
            f"<div class='gbars'>{_proof_bar(g.get('proof_strength'), competitor)}"
            f"{_proof_bar(g.get('focal_proof_strength'), focal)}"
            f"<div class='gclaim'>Missing: {_esc(missing)} · specificity {_esc(g.get('claim_specificity'))}</div></div></div>"
        )
    return "".join(rows)


def _heatmap(pkg: dict[str, Any]) -> str:
    m = pkg.get("persona_channel_matrix", {}) or {}
    personas, channels, cells = m.get("personas", []), m.get("channels", []), m.get("cells", {})
    if not personas or not channels:
        return "<p class='empty'>No persona × channel matrix.</p>"
    mx = max([1] + [(cells.get(p, {}) or {}).get(c, 0) for p in personas for c in channels])
    head = "<div class='hm-corner'></div>" + "".join(f"<div class='hm-col'>{_esc(c)}</div>" for c in channels)
    body = []
    for p in personas:
        body.append(f"<div class='hm-rl'>{_esc(p.replace('_', ' '))}</div>")
        for c in channels:
            v = (cells.get(p, {}) or {}).get(c, 0)
            a = (0.12 + 0.88 * (v / mx)) if v else 0
            body.append(
                f"<div class='hm-cell' style='background:rgba(110,168,254,{a:.2f})' "
                f"title='{_esc(p)} × {_esc(c)}: {v}'>{v or ''}</div>"
            )
    cols = f"140px repeat({len(channels)}, minmax(64px,1fr))"
    return f"<div class='hmwrap'><div class='heatmap' style='grid-template-columns:{cols}'>{head}{''.join(body)}</div></div>"


def build_dashboard(pkg: dict[str, Any]) -> str:
    companies = pkg.get("companies", [])
    competitor = companies[0].get("canonical_name") if companies else pkg.get("scope", {}).get("company_input", "Competitor")
    focal = companies[1].get("canonical_name") if len(companies) > 1 else "Rippling"
    cls = pkg.get("classifications", [])

    src = sorted((pkg.get("source_distribution", {}) or {}).items(), key=lambda kv: -kv[1])
    themes = Counter(c["primary_theme"] for c in cls if c.get("primary_theme")).most_common(8)
    stance = Counter(c["competitive_stance"] for c in cls if c.get("competitive_stance")).most_common()
    total = sum(v for _, v in src)
    es = pkg.get("eval_summary", {})
    run = pkg.get("run", {})

    return f"""<title>Competitive Intel — {_esc(competitor)} vs {_esc(focal)}</title>
<style>
:root {{ --bg:#0f1115; --panel:#181b22; --panel2:#1f232c; --border:#2a2f3a; --text:#e6e8ec; --muted:#9aa3b2; --accent:#6ea8fe; }}
@media (prefers-color-scheme: light) {{ :root {{ --bg:#f7f8fa; --panel:#fff; --panel2:#eef1f5; --border:#d8dde5; --text:#1a1d23; --muted:#5b6472; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,Segoe UI,Roboto,sans-serif; padding:24px; max-width:1000px; margin:0 auto; }}
h1 {{ font-size:20px; }} h2 {{ font-size:15px; color:var(--accent); border-bottom:1px solid var(--border); padding-bottom:6px; margin-top:28px; }}
.sub {{ color:var(--muted); font-size:12px; }}
.card {{ background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:16px; margin:10px 0; }}
.title {{ font-weight:600; margin-bottom:10px; font-size:13px; }}
.grid2 {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:12px; }}
.stat {{ display:inline-block; margin-right:18px; }} .stat b {{ font-size:20px; }} .stat span {{ color:var(--muted); font-size:11px; display:block; }}
.hbar {{ display:flex; flex-direction:column; gap:5px; }}
.hrow {{ display:grid; grid-template-columns:130px 1fr 34px; align-items:center; gap:8px; font-size:12px; }}
.hlabel {{ color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.htrack {{ background:var(--panel2); border-radius:4px; height:14px; overflow:hidden; }}
.hfill {{ height:100%; border-radius:4px; min-width:2px; }}
.hval {{ text-align:right; font-variant-numeric:tabular-nums; }}
.pill {{ font-size:10px; padding:2px 7px; border-radius:999px; border:1px solid var(--border); }}
.gaprow {{ display:grid; grid-template-columns:130px 1fr; gap:10px; padding:8px 0; border-bottom:1px solid var(--border); align-items:start; }}
.glabel {{ font-weight:600; font-size:13px; margin-bottom:4px; }}
.gbars {{ display:flex; flex-direction:column; gap:4px; }}
.gclaim {{ font-size:12px; color:var(--muted); }}
.pbar {{ display:inline-flex; align-items:center; gap:3px; }}
.ptag {{ font-size:10px; color:var(--muted); width:80px; text-align:right; margin-right:4px; }}
.seg {{ width:16px; height:9px; border-radius:2px; display:inline-block; }}
.plabel {{ font-size:10px; color:var(--muted); margin-left:4px; }}
.hmwrap {{ overflow-x:auto; }} .heatmap {{ display:grid; gap:2px; min-width:480px; }}
.hm-col {{ font-size:10px; color:var(--muted); text-align:center; padding:2px; }}
.hm-rl {{ font-size:11px; color:var(--muted); text-align:right; padding-right:6px; align-self:center; }}
.hm-cell {{ height:26px; display:flex; align-items:center; justify-content:center; font-size:11px; border-radius:3px; border:1px solid var(--border); }}
.empty {{ color:var(--muted); font-style:italic; font-size:13px; }}
</style>
<h1>Competitive Marketing Intelligence — {_esc(competitor)} <span class='sub'>vs {_esc(focal)}</span></h1>
<p class='sub'>Run <code>{_esc(run.get('run_id'))}</code> · mode {_esc(run.get('mode'))} · {_esc(run.get('execution_mode'))} · generated {_esc(str(run.get('generated_at'))[:19])}</p>
<div class='card'>
  <div class='stat'><b>{es.get('n_artifacts', total)}</b><span>artifacts</span></div>
  <div class='stat'><b>{es.get('n_classifications', len(cls))}</b><span>classifications</span></div>
  <div class='stat'><b>{es.get('n_proof_gaps', len(pkg.get('proof_gaps', [])))}</b><span>proof gaps</span></div>
  <div class='stat'><b>{es.get('n_opportunities', len(pkg.get('opportunities', [])))}</b><span>opportunities</span></div>
  <div class='stat'><b>{es.get('n_change_events', len(pkg.get('change_events', [])))}</b><span>changes over time</span></div>
</div>

<h2>Data at a glance</h2>
<div class='grid2'>
  <div class='card'><div class='title'>Source mix ({total} artifacts)</div>{_hbar(src, 'var(--accent)')}</div>
  <div class='card'><div class='title'>Top message themes</div>{_hbar(themes, '#4ade80')}</div>
  <div class='card'><div class='title'>Competitive stance (observed)</div>{_hbar(stance, '#fbbf24')}</div>
</div>

<h2>Message–proof gaps</h2>
<p class='sub'>For each repeated {_esc(competitor)} claim: how strongly {_esc(competitor)} proves it vs. how strongly {_esc(focal)} can. Pill = attackability (green attack · yellow investigate · red don't).</p>
<div class='card'>{_gaps(pkg, competitor, focal)}</div>

<h2>Persona × channel coverage</h2>
<div class='card'>{_heatmap(pkg)}</div>
"""
