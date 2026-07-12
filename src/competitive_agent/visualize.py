"""Self-contained HTML dashboard from a run's JSON package.

Pre-renders the same charts the React UI shows (source mix, message themes,
competitive stance, message-proof gaps, persona x channel heatmap) as static
inline HTML/CSS — no JS, no external hosts — so the visuals open anywhere and
render identically. This is a rendering of the SAME validated package (§40.6):
no separate analytics.
"""

from __future__ import annotations

import html
import re
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
    color = (
        "#4ade80" if lvl >= 3 else "#fbbf24" if lvl == 2 else "#f87171" if lvl >= 1 else "#2a2f3a"
    )
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
    head = "<div class='hm-corner'></div>" + "".join(
        f"<div class='hm-col'>{_esc(c)}</div>" for c in channels
    )
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


_CONF_COLOR = {"high": "#4ade80", "medium": "#fbbf24", "low": "#f87171"}


def _theme_of(change: dict[str, Any]) -> str:
    """Human label for a change: the quoted theme for emergence, else dimension."""
    m = re.search(r"[“\"']([a-z_]+)[”\"']", str(change.get("current_state", "")))
    return m.group(1).replace("_", " ") if m else str(change.get("dimension", "change"))


def _timeline(pkg: dict[str, Any]) -> str:
    changes = pkg.get("change_events", [])
    if not changes:
        return "<p class='empty'>No temporal change met the both-periods evidence bar this run.</p>"
    windows = {w.get("label"): w for w in pkg.get("scope", {}).get("time_windows", [])}
    prior, cur = windows.get("comparison", {}), windows.get("current", {})
    prior_lbl = (
        f"{str(prior.get('start_at'))[:7]} → {str(prior.get('end_at'))[:7]}"
        if prior
        else "prior window"
    )
    cur_lbl = (
        f"{str(cur.get('start_at'))[:7]} → {str(cur.get('end_at'))[:7]}"
        if cur
        else "current window"
    )
    mx = max([1] + [len(c.get("current_evidence_ids", [])) for c in changes])

    rows = []
    for c in changes:
        pe, ce = len(c.get("prior_evidence_ids", [])), len(c.get("current_evidence_ids", []))
        conf, life = str(c.get("confidence", "low")), str(c.get("lifecycle", ""))
        cc = _CONF_COLOR.get(conf, "#9aa3b2")
        cur_w = ce / mx * 100
        caveat = (c.get("alternative_explanations") or [""])[0]
        rows.append(
            f"<div class='tlrow'>"
            f"<div class='tltheme'>{_esc(_theme_of(c))}"
            f"<span class='pill' style='color:{cc};border-color:{cc}'>{_esc(conf)} · {_esc(life)}</span></div>"
            f"<div class='tltrack'>"
            f"<div class='tlprior' title='prior window: not observed in a {pe}-artifact sample'>prior: not observed</div>"
            f"<div class='tlarrow'>→</div>"
            f"<div class='tlbar' style='width:{cur_w:.0f}%' title='present in {ce} current artifacts'></div>"
            f"<div class='tlcount'>{ce} artifacts</div></div>"
            f"<div class='tlcaveat'>⚠ {_esc(caveat)}</div>"
            f"</div>"
        )
    return (
        f"<div class='tlhead'><span>◀ {_esc(prior_lbl)}</span>"
        f"<span class='tlnow'>{_esc(cur_lbl)} ▶</span></div>{''.join(rows)}"
    )


def _pm_matrix(pkg: dict[str, Any], competitor: str, focal: str) -> str:
    """PRODUCT MARKETING — Attack/Defend matrix. Each repeated competitor claim
    plotted by competitor proof strength (x) vs focal proof strength (y).
    Quadrants tell a PMM where to attack, defend, differentiate, or build proof."""
    gaps = pkg.get("proof_gaps", [])
    if not gaps:
        return "<p class='empty'>No proof gaps to plot.</p>"
    dots = []
    for i, g in enumerate(gaps):
        cx = _PROOF_LEVEL.get(str(g.get("proof_strength", "none")).lower(), 0) / 3 * 100
        cy = _PROOF_LEVEL.get(str(g.get("focal_proof_strength", "none")).lower(), 0) / 3 * 100
        # jitter overlapping points deterministically
        jx = (i % 3 - 1) * 3.5
        jy = ((i // 3) % 3 - 1) * 3.5
        x = min(96, max(4, cx + jx))
        y = min(96, max(4, cy + jy))
        att = str(g.get("attackability", "low"))
        col = _ATTACK_COLOR.get(att, "#9aa3b2")
        label = _esc(g.get("short_label", ""))
        dots.append(
            f"<div class='pmdot' style='left:{x:.0f}%;bottom:{y:.0f}%;background:{col}' "
            f"title='{label}: {competitor} proof {_esc(g.get('proof_strength'))} · {focal} proof "
            f"{_esc(g.get('focal_proof_strength'))} · attackability {att}'><span>{label}</span></div>"
        )
    return (
        "<div class='pmwrap'>"
        f"<div class='pmyaxis'>{_esc(focal)} proof →</div>"
        "<div class='pmgrid'>"
        "<div class='pmq tl'>ATTACK<br><i>they're weak, we're strong</i></div>"
        "<div class='pmq tr'>DIFFERENTIATE<br><i>both strong</i></div>"
        "<div class='pmq bl'>BUILD PROOF<br><i>whitespace — both weak</i></div>"
        "<div class='pmq br'>AT RISK<br><i>they're strong, we're weak</i></div>"
        f"{''.join(dots)}</div></div>"
        f"<div class='pmxaxis'>{_esc(competitor)} proof →</div>"
    )


def _content_map(pkg: dict[str, Any]) -> str:
    """LINKEDIN CONTENT — persona × funnel-stage coverage (whitespace = content
    opportunity) + narrative hooks (status-quo villains + emerging themes)."""
    cls = pkg.get("classifications", [])
    funnel = ["awareness", "consideration", "evaluation", "decision", "retention_expansion"]
    persona_c: Counter[str] = Counter()
    grid: dict[str, Counter[str]] = {}
    for c in cls:
        ps = c.get("personas") or []
        fs = c.get("funnel_stages") or []
        for p in ps:
            persona_c[p] += 1
            grid.setdefault(p, Counter())
            for f in fs:
                grid[p][f] += 1
    personas = [p for p, _ in persona_c.most_common(8)]
    if not personas:
        return "<p class='empty'>No persona/funnel classifications.</p>"
    mx = max([1] + [grid[p][f] for p in personas for f in funnel])
    head = "<div class='hm-corner'></div>" + "".join(
        f"<div class='hm-col'>{_esc(f.replace('_', ' '))}</div>" for f in funnel
    )
    cells = []
    for p in personas:
        cells.append(f"<div class='hm-rl'>{_esc(p.replace('_', ' '))}</div>")
        for f in funnel:
            v = grid[p][f]
            a = (0.12 + 0.88 * (v / mx)) if v else 0
            # low/zero coverage is the OPPORTUNITY — outline empties
            style = (
                f"background:rgba(74,222,128,{a:.2f})"
                if v
                else "background:transparent;border-style:dashed"
            )
            cells.append(
                f"<div class='hm-cell' style='{style}' title='{_esc(p)} × {_esc(f)}: {v}'>{v or ''}</div>"
            )
    cols = f"140px repeat({len(funnel)}, minmax(64px,1fr))"
    grid_html = (
        f"<div class='hmwrap'><div class='heatmap' style='grid-template-columns:{cols}'>"
        f"{head}{''.join(cells)}</div></div>"
    )
    # narrative hooks
    villains: Counter[str] = Counter()
    for c in cls:
        for v in c.get("villain_normalized") or []:
            villains[v] += 1
    emerging = [_theme_of(ce) for ce in pkg.get("change_events", [])]
    hooks = ", ".join(sorted({v for v, _ in villains.most_common(6)})) or "—"
    emerge = ", ".join(dict.fromkeys(emerging)) or "—"
    return (
        grid_html
        + f"<div class='hooks'><b>Narrative hooks (villains to attack):</b> {_esc(hooks)}<br>"
        f"<b>Emerging themes (fresh angles):</b> {_esc(emerge)}</div>"
    )


def _seo_cep(pkg: dict[str, Any], competitor: str, focal: str) -> str:
    """SEO/PAID — category-entry-point (search-intent) ownership. Diverging bars:
    competitor page investment vs focal, color-coded by who owns the intent."""
    ceps = pkg.get("category_entry_points", [])
    if not ceps:
        return "<p class='empty'>No category entry points extracted.</p>"
    own_color = {
        "focal_owns": "#4ade80",
        "contested": "#fbbf24",
        "competitor_advantage": "#f87171",
        "neither": "#9aa3b2",
    }
    mx = max([1] + [max(c.get("competitor_pages", 0), c.get("focal_pages", 0)) for c in ceps])
    rows = []
    for c in sorted(ceps, key=lambda x: -(x.get("competitor_pages", 0) + x.get("focal_pages", 0)))[
        :10
    ]:
        own = str(c.get("ownership", "neither"))
        col = own_color.get(own, "#9aa3b2")
        cw = c.get("competitor_pages", 0) / mx * 100
        fw = c.get("focal_pages", 0) / mx * 100
        rows.append(
            f"<div class='ceprow'>"
            f"<div class='ceplabel' title='{_esc(c.get('cep'))}'>{_esc(str(c.get('cep')).replace('_', ' '))}"
            f"<span class='pill' style='color:{col};border-color:{col}'>{_esc(own.replace('_', ' '))}</span></div>"
            f"<div class='cepbars'>"
            f"<div class='cepside left'><div class='cepfill' style='width:{cw:.0f}%;background:#f87171' "
            f"title='{competitor}: {c.get('competitor_pages', 0)} pages'></div><span>{c.get('competitor_pages', 0)}</span></div>"
            f"<div class='cepside right'><span>{c.get('focal_pages', 0)}</span><div class='cepfill' "
            f"style='width:{fw:.0f}%;background:#6ea8fe' title='{focal}: {c.get('focal_pages', 0)} pages'></div></div>"
            f"</div></div>"
        )
    return (
        f"<div class='cephead'><span>◀ {_esc(competitor)} pages</span>"
        f"<span>{_esc(focal)} pages ▶</span></div>{''.join(rows)}"
    )


def _linkedin_posts(pkg: dict[str, Any], competitor: str) -> str:
    posts = pkg.get("linkedin_posts") or []
    if not posts:
        return (
            "<p class='empty'>No individual LinkedIn posts collected this run "
            "(needs Exa credits + the exa_linkedin source).</p>"
        )
    rows = []
    for p in posts[:15]:
        who = _esc(p.get("author") or "?")
        role = f" · {_esc(p['author_role'])}" if p.get("author_role") else ""
        theme = _esc(p.get("theme") or "—")
        stance = _esc(p.get("competitive_stance") or "—")
        excerpt = _esc((p.get("excerpt") or "")[:220])
        url = _esc(p.get("post_url") or "#")
        rows.append(
            f"<div class='lipost'><div class='lihead'><b>{who}</b>{role} "
            f"<span class='pill'>{theme}</span> <span class='pill'>{stance}</span> "
            f"<a href='{url}' target='_blank' rel='noopener'>view post ↗</a></div>"
            f"<div class='liexcerpt'>{excerpt}</div></div>"
        )
    return (
        f"<div class='sub'>{len(posts)} public posts by {_esc(competitor)} employees, each classified.</div>"
        + "".join(rows)
    )


def _similarweb(pkg: dict[str, Any]) -> str:
    sw = pkg.get("similarweb") or {}
    m = sw.get("metrics") or {}
    if not m:
        return "<p class='empty'>No Similarweb traffic data this run (needs Exa credits).</p>"
    label = "Similarweb" if sw.get("data_source") == "similarweb" else "public-web estimate"
    stats = []
    for key in ("estimated_monthly_visits", "channel_mix", "top_countries", "digital_competitors"):
        if key in m:
            v = m[key].get("value") if isinstance(m[key], dict) else m[key]
            stats.append(
                f"<div class='row'><b>{_esc(key.replace('_', ' '))}:</b> {_esc(v)} <i>(estimated)</i></div>"
            )
    return f"<div class='sub'>Source: {_esc(label)} · all values estimated.</div>" + "".join(stats)


def build_dashboard(pkg: dict[str, Any]) -> str:
    companies = pkg.get("companies", [])
    competitor = (
        companies[0].get("canonical_name")
        if companies
        else pkg.get("scope", {}).get("company_input", "Competitor")
    )
    focal = companies[1].get("canonical_name") if len(companies) > 1 else "Rippling"
    cls = pkg.get("classifications", [])

    src = sorted((pkg.get("source_distribution", {}) or {}).items(), key=lambda kv: -kv[1])
    themes = Counter(c["primary_theme"] for c in cls if c.get("primary_theme")).most_common(8)
    stance = Counter(
        c["competitive_stance"] for c in cls if c.get("competitive_stance")
    ).most_common()
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
.forwho {{ font-size:11px; color:var(--muted); background:var(--panel2); border-radius:6px; padding:8px 10px; margin:6px 0 10px; }}
.forwho b {{ color:var(--accent); }}
/* timeline */
.tlhead {{ display:flex; justify-content:space-between; font-size:11px; color:var(--muted); border-bottom:1px dashed var(--border); padding-bottom:6px; margin-bottom:8px; }}
.tlnow {{ color:var(--accent); }}
.tlrow {{ padding:8px 0; border-bottom:1px solid var(--border); }}
.tltheme {{ font-weight:600; font-size:13px; margin-bottom:4px; }}
.tltrack {{ display:flex; align-items:center; gap:8px; font-size:11px; }}
.tlprior {{ color:var(--muted); width:120px; }} .tlarrow {{ color:var(--muted); }}
.tlbar {{ height:12px; background:#4ade80; border-radius:4px; min-width:8px; opacity:.7; }}
.tlcount {{ color:var(--text); font-variant-numeric:tabular-nums; }}
.tlcaveat {{ font-size:11px; color:var(--muted); margin-top:3px; }}
/* PM matrix */
.pmwrap {{ display:flex; align-items:stretch; }}
.pmyaxis {{ writing-mode:vertical-rl; transform:rotate(180deg); font-size:11px; color:var(--muted); text-align:center; padding:4px; }}
.pmgrid {{ position:relative; flex:1; height:320px; display:grid; grid-template-columns:1fr 1fr; grid-template-rows:1fr 1fr; }}
.pmq {{ border:1px dashed var(--border); font-size:10px; color:var(--muted); padding:6px; text-align:center; }}
.pmq i {{ font-size:9px; }}
.pmq.tl {{ color:#4ade80; }} .pmq.br {{ color:#f87171; }}
.pmdot {{ position:absolute; width:9px; height:9px; border-radius:50%; transform:translate(-50%,50%); }}
.pmdot span {{ position:absolute; left:11px; top:-4px; font-size:9px; color:var(--text); white-space:nowrap; }}
.pmxaxis {{ text-align:center; font-size:11px; color:var(--muted); padding:4px; }}
.hooks {{ font-size:12px; color:var(--muted); margin-top:10px; border-top:1px solid var(--border); padding-top:8px; }}
.hooks b {{ color:var(--text); }}
/* LinkedIn posts */
.lipost {{ padding:8px 0; border-bottom:1px solid var(--border); }}
.lihead {{ font-size:12px; margin-bottom:3px; }} .lihead b {{ color:var(--text); }}
.lihead a {{ color:var(--accent); font-size:11px; margin-left:6px; }}
.liexcerpt {{ font-size:12px; color:var(--muted); }}
/* CEP diverging bars */
.cephead {{ display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-bottom:6px; }}
.ceprow {{ padding:6px 0; border-bottom:1px solid var(--border); }}
.ceplabel {{ font-size:12px; margin-bottom:3px; }}
.cepbars {{ display:grid; grid-template-columns:1fr 1fr; gap:4px; }}
.cepside {{ display:flex; align-items:center; gap:5px; height:14px; }}
.cepside.left {{ flex-direction:row-reverse; }}
.cepfill {{ height:12px; border-radius:3px; min-width:3px; }}
.cepside span {{ font-size:10px; color:var(--muted); font-variant-numeric:tabular-nums; }}
</style>
<h1>Competitive Marketing Intelligence — {_esc(competitor)} <span class='sub'>vs {_esc(focal)}</span></h1>
<p class='sub'>Run <code>{_esc(run.get("run_id"))}</code> · mode {_esc(run.get("mode"))} · {_esc(run.get("execution_mode"))} · generated {_esc(str(run.get("generated_at"))[:19])}</p>
<div class='card'>
  <div class='stat'><b>{es.get("n_artifacts", total)}</b><span>artifacts</span></div>
  <div class='stat'><b>{es.get("n_classifications", len(cls))}</b><span>classifications</span></div>
  <div class='stat'><b>{es.get("n_proof_gaps", len(pkg.get("proof_gaps", [])))}</b><span>proof gaps</span></div>
  <div class='stat'><b>{es.get("n_opportunities", len(pkg.get("opportunities", [])))}</b><span>opportunities</span></div>
  <div class='stat'><b>{es.get("n_change_events", len(pkg.get("change_events", [])))}</b><span>changes over time</span></div>
</div>

<h2>Data at a glance</h2>
<div class='grid2'>
  <div class='card'><div class='title'>Source mix ({total} artifacts)</div>{_hbar(src, "var(--accent)")}</div>
  <div class='card'><div class='title'>Top message themes</div>{_hbar(themes, "#4ade80")}</div>
  <div class='card'><div class='title'>Competitive stance (observed)</div>{_hbar(stance, "#fbbf24")}</div>
</div>

<h2>Message–proof gaps</h2>
<p class='sub'>For each repeated {_esc(competitor)} claim: how strongly {_esc(competitor)} proves it vs. how strongly {_esc(focal)} can. Pill = attackability (green attack · yellow investigate · red don't).</p>
<div class='card'>{_gaps(pkg, competitor, focal)}</div>

<h2>Strategy over time</h2>
<p class='sub'>Emerging themes between the two windows. Bars = current-window presence; the prior sample is small, so these are low-confidence signals with the coverage-asymmetry caveat, never asserted as fact.</p>
<div class='card'>{_timeline(pkg)}</div>

<h2>Product marketing — attack / defend matrix</h2>
<div class='forwho'><b>IC:</b> pick a dot in ATTACK (build the comparison asset now) or BUILD-PROOF (commission the proof point). <b>Exec:</b> read the balance of the portfolio — too many dots bottom-right (AT RISK) is a strategic proof-debt problem to fund over the next quarters.</div>
<div class='card'>{_pm_matrix(pkg, competitor, focal)}</div>

<h2>LinkedIn content marketing — coverage &amp; whitespace</h2>
<div class='forwho'><b>IC:</b> dashed/empty cells are your next posts (that persona is under-served at that funnel stage); use the narrative hooks as angles. <b>Exec:</b> spot funnel-wide gaps (e.g. thin at decision/retention) to shape the content calendar and headcount.</div>
<div class='card'>{_content_map(pkg)}</div>

<h2>SEO / paid performance — category-entry-point ownership</h2>
<div class='forwho'><b>IC:</b> target the yellow (contested) and red (competitor-advantage) intents with paid + comparison pages to capture demand. <b>Exec:</b> the mix of owned vs contested vs lost intents is the category-ownership map to invest against long-term.</div>
<div class='card'>{_seo_cep(pkg, competitor, focal)}</div>

<h2>{_esc(competitor)} LinkedIn employee posts</h2>
<div class='forwho'><b>IC:</b> read what employees actually amplify and reply/counter-message. <b>Exec:</b> the themes employees repeat are the competitor's real, believed narrative — track drift over time.</div>
<div class='card'>{_linkedin_posts(pkg, competitor)}</div>

<h2>Traffic &amp; channel mix (estimated)</h2>
<div class='card'>{_similarweb(pkg)}</div>

<h2>Persona × channel coverage</h2>
<div class='card'>{_heatmap(pkg)}</div>
"""
