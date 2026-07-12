import React, { useEffect, useState } from "react";
import { HBar, Heatmap, ProofBar } from "./charts";

const pill = (level) => <span className={`pill ${level}`}>{level}</span>;

// Hover explanation on every section header / metric (focusable for keyboard/
// screen-reader users — the tip is load-bearing, not decoration).
const Info = ({ tip }) => (
  <span className="info" title={tip} tabIndex={0} role="img" aria-label={tip}>ⓘ</span>
);

const normTheme = (s) => String(s || "").toLowerCase().replace(/[\s_-]+/g, " ").trim();

// artifact_id -> {url, title} across BOTH companies' corpora (change events
// carry ART- ids; claims carry EV- ids resolved via evidenceIndex below).
function artifactIndex(pkg) {
  const idx = {};
  (pkg.artifacts || []).forEach((a) => { idx[a.artifact_id] = { url: a.url, title: a.title }; });
  ((pkg.focal_evidence || {}).artifacts || []).forEach((a) => {
    if (!idx[a.artifact_id]) idx[a.artifact_id] = { url: a.url, title: a.title };
  });
  return idx;
}

// evidence_id -> {excerpt, artifact_id, quality} across both corpora.
function evidenceIndex(pkg) {
  const idx = {};
  (pkg.evidence || []).forEach((e) => { idx[e.evidence_id] = e; });
  ((pkg.focal_evidence || {}).evidence || []).forEach((e) => {
    if (!idx[e.evidence_id]) idx[e.evidence_id] = e;
  });
  return idx;
}

// Resolve a list of ART-/EV- ids to SourceDrawer rows — the finding's REAL
// evidence chain, not a fuzzy theme-name match.
function resolveIds(ids, artIdx, evIdx) {
  const out = [];
  (ids || []).forEach((id) => {
    if (artIdx[id]) {
      out.push({ url: artIdx[id].url, excerpt: artIdx[id].title, quality: "artifact" });
    } else if (evIdx[id]) {
      const e = evIdx[id];
      const art = artIdx[e.artifact_id] || {};
      out.push({ url: art.url, excerpt: e.exact_excerpt, quality: e.source_quality || "evidence" });
    }
  });
  return out;
}

// theme -> [{url, excerpt, quality}] — resolves any finding to its EXACT sources.
function themeSourceIndex(pkg) {
  const artUrl = {};
  (pkg.artifacts || []).forEach((a) => { artUrl[a.artifact_id] = a.url; });
  const artTheme = {};
  (pkg.classifications || []).forEach((c) => { if (c.primary_theme) artTheme[c.artifact_id] = normTheme(c.primary_theme); });
  const idx = {};
  (pkg.evidence || []).forEach((e) => {
    const t = artTheme[e.artifact_id];
    if (!t) return;
    (idx[t] = idx[t] || []).push({ url: artUrl[e.artifact_id], excerpt: e.exact_excerpt, quality: e.source_quality });
  });
  (pkg.classifications || []).forEach((c) => {
    if (!c.primary_theme) return;
    const t = normTheme(c.primary_theme);
    const list = (idx[t] = idx[t] || []);
    const u = artUrl[c.artifact_id];
    if (u && !list.some((x) => x.url === u)) list.push({ url: u, excerpt: c.primary_message, quality: "page" });
  });
  return idx;
}

// Click-through: expand any finding to the exact excerpts + links behind it.
function SourceDrawer({ sources, label }) {
  const [open, setOpen] = useState(false);
  if (!sources || sources.length === 0) return null;
  return (
    <div className="srcdrawer">
      <button
        type="button"
        className="srcbtn"
        title="Click to see the exact source pages and verbatim excerpts behind this finding"
        onClick={() => setOpen((o) => !o)}
      >
        {open ? "▾" : "▸"} {label || `see exact sources (${sources.length})`}
      </button>
      {open && (
        <div className="srclist">
          {sources.slice(0, 8).map((s, i) => (
            <div className="srcrow" key={i}>
              <span className="srcq" title="source quality band">{s.quality}</span>{" "}
              <a href={s.url} target="_blank" rel="noreferrer">
                {(s.url || "").replace(/^https?:\/\/(www\.)?/, "").slice(0, 52)} ↗
              </a>
              {s.excerpt && <div className="srcex">“{String(s.excerpt).slice(0, 200)}”</div>}
            </div>
          ))}
          {sources.length > 8 && <div className="srcmore">…{sources.length - 8} more in Sources & evidence</div>}
        </div>
      )}
    </div>
  );
}

/* ------------------------------ chat ---------------------------------- */

function ChatPanel({ runId, pkg }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [vertical, setVertical] = useState("");
  const competitor = pkg.companies?.[0]?.canonical_name || "this competitor";
  const verticals = (pkg.product_vertical_analysis?.verticals || []).map((v) => v.vertical);

  const send = async (q) => {
    const question = (q || input).trim();
    if (!question || busy) return;
    setInput("");
    const history = messages.map((m) => ({ role: m.role, content: m.content || m.answer || "" }));
    setMessages((m) => [...m, { role: "user", content: question }]);
    setBusy(true);
    try {
      const res = await fetch(`/api/runs/${runId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          history,
          // Fixture runs chat against the fixture gateway (keyless deployments).
          execution_mode: pkg.run?.execution_mode === "fixture" ? "fixture" : "live",
          vertical: vertical || null,
        }),
      });
      const data = res.ok ? await res.json() : { answer: "Chat error: " + res.statusText, suggested_followups: [] };
      setMessages((m) => [...m, { role: "assistant", ...data }]);
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", answer: "Could not reach the chat API.", suggested_followups: [] }]);
    }
    setBusy(false);
  };

  return (
    <div className="chat card">
      <div className="title">
        💬 Ask about {competitor}
        <Info tip="Grounded analysis chatbot: answers ONLY from this run's collected data (every source, excerpt, claim + justification). Cites sources, asks a clarifying question back when your question is ambiguous, and flags when it needs deeper research." />
      </div>
      <div className="chatscope">
        <label className="chatscope-label" title="Scope the chat's grounded data to one product vertical — per-offering questions get per-offering answers">
          Focus:
        </label>
        <select className="nr-sel" value={vertical} onChange={(e) => setVertical(e.target.value)}>
          <option value="">all product verticals</option>
          {verticals.map((v) => (
            <option key={v} value={v}>{v.replace(/_/g, " ")}</option>
          ))}
        </select>
      </div>
      <div className="chatlog">
        {messages.length === 0 && (
          <div className="chathint">
            Try: “What's their most attackable gap?” · “What are employees posting on LinkedIn?” · “How do they position payroll vs IT?”
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chatmsg ${m.role}`}>
            <div className="chatbubble">{m.answer || m.content}</div>
            {m.role === "assistant" && m.clarifying_question && (
              <div className="clarify">❓ {m.clarifying_question} <span className="clarifyhint">(reply below to refine)</span></div>
            )}
            {m.role === "assistant" && m.needs_deeper_research && (
              <div className="chatnote">Needs deeper research — run a focused deep-dive.</div>
            )}
            {m.role === "assistant" && (m.suggested_followups || []).length > 0 && (
              <div className="chipwrap">
                {m.suggested_followups.map((f, j) => (
                  <button key={j} className="chip" onClick={() => send(f)} disabled={busy}>{f}</button>
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="chatmsg assistant"><div className="chatbubble"><span className="spinner" /> thinking…</div></div>}
      </div>
      <form className="chatform" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <input className="nr-in" placeholder={vertical ? `Ask about ${competitor} ${vertical.replace(/_/g, " ")}…` : `Ask about ${competitor}…`}
               value={input} onChange={(e) => setInput(e.target.value)} />
        <button className="nr-btn" disabled={busy || !input.trim()}>Ask</button>
      </form>
    </div>
  );
}

/* --------------------------- overview tab ------------------------------ */

// EXEC — the Overview tab's tooltip promises "top actions"; deliver them here
// instead of burying the Action Board below six gap rows on another tab.
function TopActions({ pkg, onOpenBoard }) {
  const opps = (pkg.opportunities || []).slice(0, 3);
  const focal = pkg.companies?.[1]?.canonical_name || "Rippling";
  if (opps.length === 0) return null;
  return (
    <>
      <h2>Top actions for {focal} <Info tip="The engine's top-ranked defensible plays — full experiment plans (metrics, guardrails, staged gates) on the Product marketing tab's Action Board." /></h2>
      <div className="card">
        {opps.map((o) => (
          <div className="tarow" key={o.opportunity_id}>
            <div className="talabel"><b>{o.title}</b> <span className="pill">{o.deliverable_type}</span></div>
            <div className="row" style={{ fontSize: 12 }}>{o.message_angle}</div>
            <div className="row" style={{ fontSize: 12, color: "var(--muted)" }}>
              <b>metric:</b> {o.primary_metric || "—"} · <b>kill rule:</b> {o.kill_rule || "—"}
            </div>
          </div>
        ))}
        <button type="button" className="srcbtn" onClick={onOpenBoard}
                title="Full Action Board: proof status, backfire risk, experiment plans, staged gates">
          open the full Action Board →
        </button>
      </div>
    </>
  );
}

// Corpus-size asymmetry disclosure — rendered only when the package says so
// (one shared rule for UI, dashboard, and brief; §40.6: UI adds no analytics).
function AsymmetryBanner({ pkg }) {
  const cn = pkg.corpus_normalization || {};
  if (!cn.show_banner) return null;
  return (
    <div className="banner" role="note">
      ⚠ <b>Corpus-size asymmetry:</b> {cn.competitor?.name} {cn.competitor?.n_classified} classified
      artifacts vs {cn.focal?.name || "focal"} {cn.focal?.n_classified} (ratio {cn.asymmetry_ratio}).{" "}
      {cn.normalization_note}
    </div>
  );
}

function DataVisuals({ pkg }) {
  const cls = pkg.classifications || [];
  const src = pkg.source_distribution || {};
  const srcData = Object.entries(src).map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
  const count = (field) => {
    const c = {};
    cls.forEach((x) => { if (x[field]) c[x[field]] = (c[x[field]] || 0) + 1; });
    return Object.entries(c).map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
  };
  const allThemes = count("primary_theme");
  const themeData = allThemes.slice(0, 8);
  const stanceData = count("competitive_stance");
  if (srcData.length === 0 && themeData.length === 0) return null;
  const total = srcData.reduce((s, d) => s + d.value, 0);
  const themeTitle = allThemes.length > 8 ? `Top message themes (8 of ${allThemes.length})` : "Top message themes";
  return (
    <>
      <h2>Data at a glance <Info tip="Where the evidence came from, which message themes dominate, and how openly they position against competitors. Counts are observed artifacts, never population claims." /></h2>
      <div className="grid2">
        <div className="card"><div className="title">Source mix ({total} artifacts) <Info tip="Every collected source by type — websites, archives, news, reviews, ads, LinkedIn. Click Sources & evidence for each URL." /></div><HBar data={srcData} /></div>
        <div className="card"><div className="title">{themeTitle} <Info tip="What the competitor talks about most, classified per page/post by the message classifier." /></div><HBar data={themeData} colorVar="--good" /></div>
        <div className="card"><div className="title">Competitive stance <Info tip="How each page/post positions vs competitors: ignores → implicit contrast → named comparison → direct attack." /></div><HBar data={stanceData} colorVar="--warn" /></div>
      </div>
    </>
  );
}

function Positioning({ pkg }) {
  const dom = pkg.dominant_message || {};
  const cls = pkg.classifications || [];
  const villains = new Set();
  cls.forEach((c) => (c.villain_normalized || []).forEach((v) => villains.add(v)));
  return (
    <>
      <h2>Current public positioning <Info tip="The company-level dominant message — chosen by page authority × prominence (homepage/platform outrank a blog post), only when repeated across ≥2 source classes." /></h2>
      <div className="card">
        {dom.label && <div className="row"><b>Dominant message:</b> {dom.label}</div>}
        {dom.theme && <div className="row"><b>Theme:</b> {dom.theme} <span className="pill">{dom.is_company_level ? "company-level" : "corpus-level"}</span></div>}
        {villains.size > 0 && <div className="row"><b>Villains attacked:</b> {[...villains].join(", ")}</div>}
      </div>
    </>
  );
}

/* ----------------------- product marketing tab ------------------------- */

function GapsSection({ pkg, srcIdx }) {
  const gaps = pkg.proof_gaps || [];
  const focal = pkg.companies?.[1]?.canonical_name || "Rippling";
  const competitor = pkg.companies?.[0]?.canonical_name || "Competitor";
  if (gaps.length === 0) return <p className="empty">No repeated competitor claim with a proof gap observed.</p>;
  return (
    <>
      <h2>Message–proof gaps <Info tip={`Claims ${competitor} repeats but proves weakly — vs how strongly ${focal} could prove the equivalent. Pill = attackability: green attack · yellow investigate · red don't (reframe/concede). Click a row's sources to see the exact pages.`} /></h2>
      <div className="card">
        {gaps.map((g) => (
          <div className="gaprow" key={g.claim_id}>
            <div>
              <div className="gaplabel" title={g.claim_text}>{g.short_label}</div>
              {pill(g.attackability)}
            </div>
            <div className="gapbars">
              <ProofBar strength={g.proof_strength} label={competitor} />
              <ProofBar strength={g.focal_proof_strength} label={focal} />
              <div className="gapclaim" title="What proof is missing, and how specific the claim is">
                Missing: {(g.missing_proof || []).join(", ") || "—"} · specificity {g.claim_specificity}
              </div>
              <div className="gapclaim">{g.actionable_interpretation}</div>
              <SourceDrawer sources={srcIdx[normTheme(g.short_label)]} />
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function Opportunities({ pkg, srcIdx }) {
  const opps = pkg.opportunities || [];
  const gapsById = {};
  (pkg.proof_gaps || []).forEach((g) => { gapsById[g.claim_id] = g; });
  const focal = pkg.companies?.[1]?.canonical_name || "Rippling";
  if (opps.length === 0) return <p className="empty">No opportunity survived generation.</p>;
  return (
    <>
      <h2>{focal}-relative recommended actions <Info tip={`Defensible marketing plays generated from the gaps — each with proof status, backfire risk, an experiment hypothesis, and a kill rule. 'Already-saying-it' = whether ${focal} currently uses this angle.`} /></h2>
      {opps.map((o) => {
        const gap = (o.supporting_claim_ids || []).map((id) => gapsById[id]).find(Boolean);
        const sources = gap ? srcIdx[normTheme(gap.short_label)] : null;
        return (
          <div className="card" key={o.opportunity_id}>
            <div className="title">
              {o.title} <span className="pill" title="deliverable type">{o.deliverable_type}</span>{" "}
              <span title="structural defensibility — how hard this is for the competitor to copy">{pill(o.structural_defensibility)}</span>
            </div>
            <div className="row"><b>Angle:</b> {o.message_angle}</div>
            <div className="row">
              <b title={`${focal}'s own publishable proof for this angle`}>Focal proof:</b> {o.focal_proof_status} ·{" "}
              <b title={`is ${focal} already using this angle?`}>already-saying-it:</b> {o.focal_current_usage} ·{" "}
              <b>legal review:</b> {String(o.legal_review_required)}
            </div>
            <div className="row"><b>Why it could backfire:</b> {o.why_this_could_backfire}</div>
            <div className="row"><b>Experiment:</b> {o.experiment_hypothesis}</div>
            <div className="row"><b>Kill rule:</b> {o.kill_rule}</div>
            <details className="expplan">
              <summary title="The full experiment design an IC needs to run this: metrics, guardrails, sample floor, iterate/scale rules, and per-stage proceed/stop gates">
                Experiment plan (metrics, guardrails, staged gates)
              </summary>
              <div className="row"><b>Primary metric:</b> {o.primary_metric || "—"}</div>
              <div className="row"><b>Guardrails:</b> {(o.guardrail_metrics || []).join(", ") || "—"}</div>
              <div className="row"><b>Minimum sample:</b> {o.minimum_sample_rule || "—"}</div>
              <div className="row"><b>Iterate rule:</b> {o.iterate_rule || "—"}</div>
              <div className="row"><b>Scale rule:</b> {o.scale_rule || "—"}</div>
              {(o.staged_plan || []).map((s, i) => (
                <div className="stage" key={i}>
                  <b>{String(s.stage || "").replace(/_/g, " ")}</b> — {s.objective}
                  <div className="stgok">✓ proceed if: {(s.proceed_if || []).join("; ") || "—"}</div>
                  <div className="stgno">✕ stop/reframe if: {(s.stop_or_reframe_if || []).join("; ") || "—"}</div>
                </div>
              ))}
            </details>
            <SourceDrawer sources={sources} label={gap ? `sources for the “${gap.short_label}” gap` : undefined} />
          </div>
        );
      })}
    </>
  );
}

// PRODUCT MARKETING — key related topics per company (side-by-side theme bars).
function KeyTopicsComparison({ pkg }) {
  const tc = pkg.theme_comparison || {};
  const comp = tc.competitor_themes || {};
  const focal = tc.focal_themes || {};
  const compSh = tc.competitor_shares || {};
  const focalSh = tc.focal_shares || {};
  const useShares = Object.keys(compSh).length > 0 || Object.keys(focalSh).length > 0;
  const competitor = pkg.companies?.[0]?.canonical_name || "Competitor";
  const focalName = pkg.companies?.[1]?.canonical_name || "Rippling";
  const weight = (t) => (useShares ? (compSh[t] || 0) + (focalSh[t] || 0) : (comp[t] || 0) + (focal[t] || 0));
  const allThemes = [...new Set([...Object.keys(comp), ...Object.keys(focal)])]
    .sort((a, b) => weight(b) - weight(a));
  const themes = allThemes.slice(0, 9);
  if (themes.length === 0) return null;
  const w = (t, sh, n) => (useShares ? sh[t] || 0 : n[t] || 0);
  const max = Math.max(0.0001, ...themes.map((t) => Math.max(w(t, compSh, comp), w(t, focalSh, focal))));
  const lbl = (t, sh, n) => (useShares ? `${n[t] || 0} (${Math.round((sh[t] || 0) * 100)}%)` : String(n[t] || 0));
  return (
    <>
      <h2>Key topics — {competitor} vs {focalName}
        <Info tip={`Message themes each company leads with. Bars compare each theme's SHARE of that company's classified corpus (raw counts alongside) — share-normalized so different corpus sizes stay comparable. Themes outside one side's top-10 carry their TRUE count, never a fabricated zero.`} />
      </h2>
      <div className="card">
        {themes.map((t) => (
          <div className="ktrow" key={t}>
            <div className="ktlabel" title={t}>{t.replace(/_/g, " ")}</div>
            <div className="ktbars">
              <div className="ktbar comp" style={{ width: `${(w(t, compSh, comp) / max) * 100}%` }}
                   title={`${competitor}: ${lbl(t, compSh, comp)}`} />
              <span className="ktnum">{lbl(t, compSh, comp)}</span>
            </div>
            <div className="ktbars">
              <div className="ktbar focal" style={{ width: `${(w(t, focalSh, focal) / max) * 100}%` }}
                   title={`${focalName}: ${lbl(t, focalSh, focal)}`} />
              <span className="ktnum">{lbl(t, focalSh, focal)}</span>
            </div>
          </div>
        ))}
        <div className="ktlegend">
          <span><span className="ktbar comp" style={{ width: 14, display: "inline-block", height: 8 }} /> {competitor}</span>{" "}
          <span><span className="ktbar focal" style={{ width: 14, display: "inline-block", height: 8 }} /> {focalName}</span>
          {allThemes.length > 9 && <span style={{ color: "var(--muted)" }}> · showing top 9 of {allThemes.length} themes</span>}
          {useShares && <span style={{ color: "var(--muted)" }}> · count (share of corpus)</span>}
        </div>
      </div>
    </>
  );
}

// PRODUCT MARKETING — vertical × theme heatmap (a graph per topic per vertical).
function VerticalThemeHeatmap({ pkg }) {
  const verts = (pkg.product_vertical_analysis?.verticals || []).filter((v) => v.theme_counts);
  if (verts.length === 0) return null;
  const themes = [...new Set(verts.flatMap((v) => Object.keys(v.theme_counts || {})))].slice(0, 8);
  if (themes.length === 0) return null;
  const cells = {};
  verts.forEach((v) => { cells[v.vertical] = v.theme_counts || {}; });
  return (
    <>
      <h2>Themes by product vertical
        <Info tip="Which message themes dominate INSIDE each product vertical — intensity = classified pages/posts. A theme strong in one vertical and absent in another is a per-offering positioning choice you can counter vertical-by-vertical." />
      </h2>
      <div className="card">
        <Heatmap personas={verts.map((v) => v.vertical)} channels={themes} cells={cells} />
      </div>
    </>
  );
}

// LINKEDIN — what employees post about (theme bar).
function LinkedInThemeBar({ pkg }) {
  const posts = pkg.linkedin_posts || [];
  if (posts.length === 0) return null;
  const c = {};
  posts.forEach((p) => { if (p.theme) c[p.theme] = (c[p.theme] || 0) + 1; });
  const data = Object.entries(c).map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
  if (data.length === 0) return null;
  return (
    <>
      <h2>Post themes <Info tip="Message themes across the collected employee/company LinkedIn posts — what the org actually amplifies." /></h2>
      <div className="card"><HBar data={data} colorVar="--accent" /></div>
    </>
  );
}

// STRATEGY CHANGES — prior-vs-current window baseline (what WAS observed then).
function TemporalBaseline({ pkg }) {
  const tb = pkg.temporal_baseline || {};
  const pw = tb.prior_window;
  if (!pw) return null;
  const cw = tb.current_window || {};
  const themes = [...new Set([...Object.keys(pw.themes || {}), ...Object.keys(cw.themes || {})])]
    .sort((a, b) => ((cw.themes?.[b] || 0) + (pw.themes?.[b] || 0)) - ((cw.themes?.[a] || 0) + (pw.themes?.[a] || 0)))
    .slice(0, 9);
  const max = Math.max(1, ...themes.map((t) => Math.max(pw.themes?.[t] || 0, cw.themes?.[t] || 0)));
  return (
    <>
      <h2>Prior vs current window
        <Info tip={`What WAS observed in the prior window (${pw.start} → ${pw.end}: ${pw.n_artifacts} artifacts with real archive/publish dates) vs the current window — not just the emergences. Stable themes persist across both; receded themes appeared prior but not now.`} />
      </h2>
      <div className="card">
        <div className="ktlegend" style={{ marginBottom: 8 }}>
          <span><span className="ktbar comp" style={{ width: 14, display: "inline-block", height: 8 }} /> prior ({pw.n_artifacts} artifacts, {pw.start} → {pw.end})</span>
          <span><span className="ktbar focal" style={{ width: 14, display: "inline-block", height: 8 }} /> current ({cw.n_artifacts})</span>
        </div>
        {themes.map((t) => (
          <div className="ktrow" key={t}>
            <div className="ktlabel" title={t}>{t.replace(/_/g, " ")}</div>
            <div className="ktbars">
              <div className="ktbar comp" style={{ width: `${((pw.themes?.[t] || 0) / max) * 100}%` }} />
              <span className="ktnum">{pw.themes?.[t] || 0}</span>
            </div>
            <div className="ktbars">
              <div className="ktbar focal" style={{ width: `${((cw.themes?.[t] || 0) / max) * 100}%` }} />
              <span className="ktnum">{cw.themes?.[t] || 0}</span>
            </div>
          </div>
        ))}
        <div className="row" style={{ marginTop: 8 }}>
          {(tb.stable_themes || []).length > 0 && <span><b>Stable:</b> {tb.stable_themes.join(", ")} · </span>}
          {(tb.emerged_themes || []).length > 0 && <span><b>Emerged:</b> {tb.emerged_themes.join(", ")} · </span>}
          {(tb.receded_themes || []).length > 0 && <span><b>Receded:</b> {tb.receded_themes.join(", ")}</span>}
        </div>
        <p className="empty" style={{ fontSize: 11, marginBottom: 0 }}>{tb.note}</p>
      </div>
    </>
  );
}

// STRATEGY CHANGES — visual timeline (prior → current evidence bars).
function ChangesTimeline({ pkg }) {
  const changes = pkg.change_events || [];
  if (changes.length === 0) return null;
  const max = Math.max(1, ...changes.map((c) => (c.current_evidence_ids || []).length));
  const themeOf = (c) => ((String(c.current_state || "").match(/[“"']([a-z_]+)[”"']/) || [])[1] || c.dimension || "change").replace(/_/g, " ");
  return (
    <>
      <h2>Change timeline <Info tip="Each emerging theme: prior-window presence (left) vs current-window artifact count (bar). Low confidence = signal, not fact — caveats on each card below." /></h2>
      <div className="card">
        {changes.map((c) => {
          // Real reconciled prior counts — never a hardcoded "not observed"
          // (the baseline above can hold the theme). Fallback keeps old
          // packages rendering.
          const hasCounts = Number.isFinite(c.prior_theme_count) && Number.isFinite(c.prior_window_n);
          const priorLabel = hasCounts
            ? (c.prior_theme_count > 0
                ? `prior: ${c.prior_theme_count} of ${c.prior_window_n}`
                : `prior: not observed (${c.prior_window_n} dated)`)
            : "prior: not observed";
          const priorTitle = hasCounts
            ? (c.prior_theme_count > 0
                ? `prior window: theme present in ${c.prior_theme_count} of ${c.prior_window_n} dated artifacts`
                : `prior window: theme absent from all ${c.prior_window_n} dated artifacts`)
            : `prior window: ${(c.prior_evidence_ids || []).length} artifacts sampled`;
          return (
            <div className="tlrow2" key={c.change_id}>
              <div className="tllabel" title={c.current_state}>{themeOf(c)} {pill(c.confidence)} <span className="pill">{c.lifecycle}</span></div>
              <div className="tlprior2" title={priorTitle}>{priorLabel}</div>
              <div className="tltrack2">
                <div className="tlbar2" style={{ width: `${((c.current_evidence_ids || []).length / max) * 100}%` }} />
                <span className="ktnum">{(c.current_evidence_ids || []).length} now</span>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

// PERFORMANCE — audience-affinity competitors (Similarweb graph).
function AffinityBar({ pkg }) {
  const comps = ((pkg.similarweb || {}).metrics || {}).digital_competitors;
  const val = comps && (comps.value != null ? comps.value : comps);
  if (!Array.isArray(val) || val.length === 0) return null;
  const focal = pkg.companies?.[1]?.canonical_name || "Rippling";
  const data = val.slice(0, 10).map((c) => ({
    label: c.domain,
    value: Math.round((c.affinity || 0) * 100),
    display: (c.affinity || 0).toFixed(2),
    colorVar: String(c.domain || "").includes(focal.toLowerCase()) ? "--good" : "--accent",
  }));
  return (
    <>
      <h2>Audience-affinity competitors (Similarweb, estimated)
        <Info tip="Sites this competitor's web audience also visits, by Similarweb's affinity INDEX (0–1, normalized to the top competitor = 1.00). It is a rank index, NOT a percentage audience overlap. Where the focal company ranks here is a demand-side competitive signal." />
      </h2>
      <div className="card">
        <HBar data={data} />
        <p className="empty" style={{ fontSize: 11, marginBottom: 0 }}>affinity index (0–1, top-normalized) — not % overlap · all values estimated</p>
      </div>
    </>
  );
}

function VerticalAnalysis({ pkg }) {
  const pva = pkg.product_vertical_analysis || {};
  const verts = pva.verticals || [];
  if (verts.length === 0) return null;
  const competitor = pkg.companies?.[0]?.canonical_name || "Competitor";
  const focalName = pkg.companies?.[1]?.canonical_name || "Rippling";
  const hasFocal = verts.some((v) => v.focal_n_artifacts);
  const pct = (x) => `${Math.round((x || 0) * 100)}%`;
  return (
    <>
      <h2>Positioning by product vertical <Info tip={`How the competitor positions in EACH product category (payroll, EOR, HRIS, IT, spend…). Keyword-derived mapping over products/themes/messages/URLs — deterministic, method disclosed in the JSON. ${hasFocal ? `Share columns divide by each company's classified corpus so a niche competitor's per-product investment compares fairly against ${focalName}'s.` : ""}`} /></h2>
      <div className="card">
        <div className="heatmap-wrap">
          <table className="vtable">
            <thead><tr>
              <th>Vertical</th><th title="pages + posts mapped to this vertical">Pages</th><th>LinkedIn</th>
              {hasFocal && <th title={`${focalName} pages mapped to this vertical (from the focal mirror run)`}>{focalName} pages</th>}
              {hasFocal && <th title="share of each company's classified corpus — corpus-size-normalized">Share (comp / focal)</th>}
              <th>Top themes</th><th>Personas</th><th>Example</th>
            </tr></thead>
            <tbody>
              {verts.map((v) => (
                <tr key={v.vertical} title={v.sample_message ? `sample message: ${v.sample_message}` : ""}>
                  <td><b>{v.vertical.replace(/_/g, " ")}</b></td>
                  <td>{v.n_artifacts}</td>
                  <td>{v.n_linkedin_posts}</td>
                  {hasFocal && <td>{v.focal_n_artifacts || 0}</td>}
                  {hasFocal && <td>{pct(v.competitor_share)} / {pct(v.focal_share)}</td>}
                  <td>{(v.top_themes || []).join(", ") || "—"}</td>
                  <td>{(v.personas || []).map((p) => p.replace(/_/g, " ")).join(", ") || "—"}</td>
                  <td>{(v.example_urls || [])[0] ? <a href={v.example_urls[0]} target="_blank" rel="noreferrer">page ↗</a> : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="empty" style={{ fontSize: 11, marginBottom: 0 }}>
          {competitor} mapped across {verts.length} verticals · hover a row for a sample message · scope the chat with the Focus selector.
        </p>
      </div>
    </>
  );
}

/* ---------------------------- linkedin tab ----------------------------- */

function LinkedInPosts({ pkg }) {
  const posts = pkg.linkedin_posts || [];
  const competitor = pkg.companies?.[0]?.canonical_name || "Competitor";
  if (posts.length === 0) {
    return (
      <p className="empty">
        No individual LinkedIn posts collected this run — needs Exa credits + the exa_linkedin source enabled.
      </p>
    );
  }
  return (
    <>
      <h2>{competitor} LinkedIn employee posts (showing {Math.min(20, posts.length)} of {posts.length}) <Info tip="Individual public posts by the company + its employees (Exa-extracted text + real post link), each run through the full classifier — theme, stance, audience, product vertical. Login-wall boilerplate is stripped from excerpts; click 'view post' to review the post on LinkedIn." /></h2>
      <div className="card">
        {posts.slice(0, 20).map((p) => (
          <div className="gaprow" key={p.artifact_id} style={{ gridTemplateColumns: "1fr" }}>
            <div className="row">
              <b>{p.author || "?"}</b>{p.author_role ? ` · ${p.author_role}` : ""}{" "}
              <span className="pill" title="classified message theme">{p.theme || "—"}</span>
              <span className="pill" title="competitive stance">{p.competitive_stance || "—"}</span>
              {(p.verticals || []).map((v) => (
                <span className="pill vert" key={v} title="product vertical this post touches">{v.replace(/_/g, " ")}</span>
              ))}{" "}
              <a href={p.post_url} target="_blank" rel="noreferrer">view post ↗</a>
            </div>
            <div className="row" style={{ color: "var(--muted)" }}>{p.excerpt}</div>
          </div>
        ))}
      </div>
    </>
  );
}

function PersonaChannelHeatmap({ pkg }) {
  const m = pkg.persona_channel_matrix || {};
  if (!m.personas?.length) return null;
  return (
    <>
      <h2>Persona × channel coverage <Info tip="Which buyer personas the competitor reaches on which channels — intensity = number of classified artifacts. Empty cells are content whitespace you can own." /></h2>
      <div className="card"><Heatmap personas={m.personas} channels={m.channels} cells={m.cells || {}} /></div>
    </>
  );
}

/* ------------------------- strategy changes tab ------------------------ */

function StrategyOverTime({ pkg, srcIdx }) {
  const changes = pkg.change_events || [];
  const artIdx = artifactIndex(pkg);
  const evIdx = evidenceIndex(pkg);
  return (
    <>
      <h2>Strategy over time ({changes.length}) <Info tip="Temporal changes reconciled against the FULL final corpus (mid-run detections are re-checked at render, so an event can never contradict the baseline above). Emerging/expanding themes stay low-confidence with a coverage-asymmetry caveat — archive absence is not real-world absence." /></h2>
      {changes.length === 0 && (
        <p className="empty">No temporal change met the both-periods evidence bar on this run.</p>
      )}
      {changes.map((c) => {
        const theme = normTheme(c.theme || (String(c.current_state || "").match(/[“"']([a-z_]+)[”"']/) || [])[1] || "");
        // Honest evidence line: "window_sample" prior ids are the sample the
        // theme was ABSENT from — never present them as pages containing it.
        const nPrior = (c.prior_evidence_ids || []).length;
        const nCur = (c.current_evidence_ids || []).length;
        const evidenceLine = c.prior_evidence_role === "window_sample"
          ? `prior sample ${c.prior_window_n ?? nPrior} artifacts (theme absent) · ${nCur} current`
          : `${Number.isFinite(c.prior_theme_count) ? c.prior_theme_count : nPrior} prior · ${nCur} current`;
        const exact = resolveIds([...(c.prior_evidence_ids || []), ...(c.current_evidence_ids || [])], artIdx, evIdx);
        return (
          <div className="card" key={c.change_id}>
            <div className="title">
              {c.dimension} <span title="confidence — low means treat as a signal, not a fact">{pill(c.confidence)}</span>{" "}
              <span className="pill" title="lifecycle state">{c.lifecycle}</span>
            </div>
            <div className="row"><b>Prior:</b> {c.prior_state}</div>
            <div className="row"><b>Current:</b> {c.current_state}</div>
            <div className="row" style={{ color: "var(--muted)", fontSize: 12 }}>
              <b>Evidence:</b> {evidenceLine}
            </div>
            {c.alternative_explanations?.length > 0 && (
              <div className="row" title="honest alternative readings of this signal"><b>Alt. explanations:</b> {c.alternative_explanations.join("; ")}</div>
            )}
            {exact.length > 0
              ? <SourceDrawer sources={exact} label={`exact evidence pages (${exact.length})`} />
              : theme && <SourceDrawer sources={srcIdx[theme]} label={`sources for “${theme}”`} />}
          </div>
        );
      })}
    </>
  );
}

/* ----------------------- performance marketing tab --------------------- */

const CEP_PLACEHOLDER = /^(not[_ ]observed|\(?unspecified\)?|unknown|none|n\/?a|unclassified)/i;
const CEP_GROUPS = ["competitor_advantage", "contested", "focal_owns", "insufficient_sample", "neither"];

function CepRow({ c, competitor, focal, artIdx }) {
  const [open, setOpen] = useState(false);
  const own = { focal_owns: "--good", contested: "--warn", competitor_advantage: "--bad", insufficient_sample: "--border", neither: "--border" };
  const cs = c.competitor_share != null ? c.competitor_share : null;
  const fs = c.focal_share != null ? c.focal_share : null;
  const clabel = cs != null ? `${c.competitor_pages} (${Math.round(cs * 100)}%)` : String(c.competitor_pages);
  const flabel = fs != null ? `${c.focal_pages} (${Math.round(fs * 100)}%)` : String(c.focal_pages);
  const pages = resolveIds(
    [...(c.competitor_example_artifact_ids || []), ...(c.focal_example_artifact_ids || [])],
    artIdx, {}
  );
  return (
    <div className="gaprow" style={{ gridTemplateColumns: "220px 1fr" }}>
      <div>
        <div className="gaplabel" style={{ fontSize: 12 }}>{String(c.cep).replace(/_/g, " ").slice(0, 60)}</div>
        <span className="pill" style={{ color: `var(${own[c.ownership] || "--border"})`, borderColor: `var(${own[c.ownership] || "--border"})` }}
              title={c.ownership_basis || "who currently owns this buying intent"}>{String(c.ownership).replace(/_/g, " ")}</span>
      </div>
      <div className="gapbars" title={c.ownership_basis || `${competitor}: ${clabel} · ${focal}: ${flabel}`}>
        <HBar data={[
          { label: competitor, value: cs != null ? cs : c.competitor_pages, display: clabel },
          { label: focal, value: fs != null ? fs : c.focal_pages, display: flabel },
        ]} />
        {pages.length > 0 && (
          <>
            <button type="button" className="srcbtn" onClick={() => setOpen((o) => !o)}
                    title="The pages that carry this buying trigger (up to 5 competitor + 3 focal examples)">
              {open ? "▾" : "▸"} contributing pages ({pages.length})
            </button>
            {open && (
              <div className="srclist">
                {pages.map((p, i) => (
                  <div className="srcrow" key={i}>
                    <a href={p.url} target="_blank" rel="noreferrer">{(p.url || "").replace(/^https?:\/\/(www\.)?/, "").slice(0, 60)} ↗</a>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function CepOwnership({ pkg }) {
  const artIdx = artifactIndex(pkg);
  // Defensive placeholder filter (synthesis filters at the source; old
  // packages may still carry a literal "not_observed" row).
  const ceps = (pkg.category_entry_points || []).filter((c) => !CEP_PLACEHOLDER.test(String(c.cep)));
  const focal = pkg.companies?.[1]?.canonical_name || "Rippling";
  const competitor = pkg.companies?.[0]?.canonical_name || "Competitor";
  if (ceps.length === 0) return null;
  const groups = CEP_GROUPS.map((g) => [g, ceps.filter((c) => c.ownership === g)]).filter(([, rows]) => rows.length > 0);
  const legacy = ceps.filter((c) => !CEP_GROUPS.includes(c.ownership));
  return (
    <>
      <h2>Search-intent ownership (category entry points) <Info tip={`Buying triggers ("opening a new country", "consolidating HR tools") and who owns them. Ownership is SHARE-normalized (pages ÷ that company's classified corpus) so corpus size can't fabricate a verdict: contested needs <2x share ratio, an ownership call needs ≥2x AND ≥3 pages, thinner reads are disclosed as insufficient sample. All ${ceps.length} triggers shown, grouped.`} /></h2>
      <div className="card">
        {groups.map(([g, rows]) => (
          <div key={g}>
            <div className="cepgroup" title={
              g === "insufficient_sample"
                ? "too few pages on either side to call ownership — disclosed, not asserted"
                : `${rows.length} trigger(s) where the share test says: ${g.replace(/_/g, " ")}`
            }>
              {String(g).replace(/_/g, " ")} ({rows.length})
            </div>
            {rows.map((c) => <CepRow key={c.cep} c={c} competitor={competitor} focal={focal} artIdx={artIdx} />)}
          </div>
        ))}
        {legacy.map((c) => <CepRow key={c.cep} c={c} competitor={competitor} focal={focal} artIdx={artIdx} />)}
      </div>
    </>
  );
}

function Similarweb({ pkg }) {
  const sw = pkg.similarweb || {};
  const m = sw.metrics || {};
  if (Object.keys(m).length === 0) {
    return <p className="empty">No traffic estimates this run (needs Exa credits — Similarweb rides the Exa Agent).</p>;
  }
  const label = sw.data_source === "similarweb" ? "Similarweb" : "public-web estimate";
  return (
    <>
      <h2>Traffic & channel mix ({label}) <Info tip="Estimated demand-side view via Exa's Similarweb partner (or a labeled public-web estimate when the partner is unavailable). Every value is an estimate — never presented as measured analytics." /></h2>
      <div className="card">
        {/* digital_competitors is an array of objects — String() renders
            "[object Object]"; the AffinityBar above is its real rendering. */}
        {["estimated_monthly_visits", "channel_mix", "top_countries", "traffic_trend"].map((k) => {
          if (m[k] == null) return null;
          const v = m[k].value != null ? m[k].value : m[k];
          let text;
          if (k === "estimated_monthly_visits" && typeof v === "number") text = v.toLocaleString();
          else if (Array.isArray(v)) text = v.map((x) => (typeof x === "object" ? Object.values(x).join(" ") : String(x))).join(", ");
          else if (typeof v === "object") text = Object.entries(v).map(([kk, vv]) => `${kk}: ${vv}`).join(" · ");
          else text = String(v);
          return (
            <div className="row" key={k}>
              <b>{k.replace(/_/g, " ")}:</b> {text} <i>(estimated)</i>
            </div>
          );
        })}
        <div className="row" style={{ color: "var(--muted)", fontSize: 12 }}>
          digital competitors: see the affinity chart above
        </div>
      </div>
    </>
  );
}

function CommercialMotion({ pkg }) {
  const cm = pkg.commercial_motion || {};
  if (!cm.primary_motion) return null;
  return (
    <>
      <h2>Commercial motion <Info tip="Public-signal inference of how they sell: CTA mix (demo vs free-trial), pricing disclosure (best-evidence: the most-open level observed on ≥2 pages), segment focus. Never CAC/conversion/spend — those aren't publicly knowable." /></h2>
      <div className="card">
        <div className="row"><b>Primary motion:</b> {cm.primary_motion} <span className="pill">{cm.confidence}</span></div>
        <div className="row"><b title="most-disclosing pricing level observed (noise-guarded)">Pricing disclosure:</b> {cm.pricing_disclosure}</div>
        {cm.dominant_ctas && <div className="row"><b>Dominant CTAs:</b> {Object.entries(cm.dominant_ctas).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(" · ")}</div>}
        {cm.segment_focus && <div className="row"><b>Segment focus:</b> {Object.entries(cm.segment_focus).map(([k, v]) => `${k} (${v})`).join(" · ")}</div>}
        <div className="row" style={{ color: "var(--muted)", fontSize: 12 }}>{cm.basis}</div>
      </div>
    </>
  );
}

/* ----------------------------- sources tab ----------------------------- */

function Evidence({ pkg }) {
  const arts = pkg.artifacts || [];
  return (
    <>
      <h2>Evidence explorer ({arts.length} artifacts) <Info tip="Every collected source: URL, retrieval timestamp (archive capture date for Wayback), and the extracted text. Everything in the analysis traces back here." /></h2>
      {arts.map((a) => (
        <details key={a.artifact_id}>
          <summary>
            <span className="badge">{a.source_type}</span> {a.title || a.url}
          </summary>
          <div className="row"><b>URL:</b> <a href={a.url} target="_blank" rel="noreferrer">{a.url}</a></div>
          <div className="row"><b>Retrieved:</b> {a.retrieved_at}{a.archive_capture_at ? ` · archived ${a.archive_capture_at}` : ""}</div>
          <div className="excerpt">{(a.normalized_text || "").slice(0, 500)}</div>
        </details>
      ))}
    </>
  );
}

function Coverage({ pkg }) {
  const cov = pkg.coverage || {};
  // ALL dimensions render — not_attempted cells were silently filtered while
  // the tooltip promised "absences are findings, never hidden" (red-team).
  const entries = Object.entries(cov);
  return (
    <>
      <h2>Coverage & limitations <Info tip="Which research dimensions reached which coverage level, and every disclosed limitation — absences are findings, never hidden. 'not attempted' = this run never collected for that dimension (absence of collection, not absence of activity)." /></h2>
      <div className="cov">
        {entries.map(([k, v]) => (
          <div className={`cell ${v === "not_attempted" ? "na" : ""}`} key={k}
               title={v === "not_attempted"
                 ? `${k.replace(/_/g, " ")}: not attempted this run — absence of collection, not absence of activity`
                 : `research dimension: ${k.replace(/_/g, " ")}`}>
            <span>{k}</span><span>{v === "not_attempted" ? "not attempted" : v}</span>
          </div>
        ))}
      </div>
      {(pkg.limitations || []).length > 0 && (
        <>
          <h3>Limitations</h3>
          <ul>{pkg.limitations.map((l, i) => <li key={i} className="row">{l}</li>)}</ul>
        </>
      )}
    </>
  );
}

// IC — the disclosed absences: negatives, tool failures, unclassified. These
// were computed and exported but rendered nowhere (red-team honesty gap).
function DataHonesty({ pkg }) {
  const negs = pkg.negative_observations || [];
  const fails = pkg.tool_failures || [];
  const uncls = pkg.unclassified_artifacts || [];
  const recon = pkg.change_event_reconciliation || [];
  if (negs.length + fails.length + uncls.length + recon.length === 0) return null;
  return (
    <>
      <h2>Data honesty — disclosed absences <Info tip="What was ATTEMPTED but returned nothing, what failed, and what couldn't be classified. Negative observations are findings: 'searched X, found nothing' is different from 'never looked'. Annotations mark negatives superseded by later successful calls." /></h2>
      <div className="card">
        {negs.length > 0 && (
          <>
            <div className="title">Negative observations ({negs.length})</div>
            {negs.map((n, i) => <div className="row" key={i} style={{ fontSize: 12 }}>• {n}</div>)}
          </>
        )}
        {fails.length > 0 && (
          <>
            <div className="title" style={{ marginTop: 10 }}>Tool failures ({fails.length})</div>
            {fails.map((f, i) => (
              <div className="row" key={i} style={{ fontSize: 12 }}>
                • {f.source_name || f.action_type || "tool"}: {String(f.last_error || f.error_type || "failed").slice(0, 120)} ({f.attempts || 1} attempt{(f.attempts || 1) > 1 ? "s" : ""})
              </div>
            ))}
          </>
        )}
        {uncls.length > 0 && (
          <>
            <div className="title" style={{ marginTop: 10 }}>Unclassified artifacts ({uncls.length})</div>
            {uncls.map((u, i) => (
              <div className="row" key={i} style={{ fontSize: 12 }}>• {u.source_type} — {u.reason} <a href={u.url} target="_blank" rel="noreferrer">↗</a></div>
            ))}
          </>
        )}
        {recon.length > 0 && (
          <>
            <div className="title" style={{ marginTop: 10 }}>Change-event reconciliation ({recon.length})</div>
            {recon.map((r, i) => <div className="row" key={i} style={{ fontSize: 12 }}>• {r}</div>)}
          </>
        )}
      </div>
    </>
  );
}

// IC — the 20-claim ledger with its full evidence chain (was in the JSON only).
function ClaimsLedger({ pkg }) {
  const claims = pkg.claims || [];
  if (claims.length === 0) return null;
  const artIdx = artifactIndex(pkg);
  const evIdx = evidenceIndex(pkg);
  return (
    <>
      <h2>Claims ledger ({claims.length}) <Info tip="Every strategic claim with its status, confidence reasoning, alternative explanations, and the exact evidence rows (verbatim excerpts + source links) that support or contradict it. This is the auditable spine of the analysis." /></h2>
      <div className="card">
        {claims.map((c) => {
          const support = resolveIds(c.evidence_ids || [], artIdx, evIdx);
          const contra = resolveIds(c.contradicting_evidence_ids || [], artIdx, evIdx);
          return (
            <details key={c.claim_id}>
              <summary>
                <span className={`pill ${c.claim_confidence}`}>{c.claim_confidence}</span>{" "}
                <span className="pill" title="claim status after the evidence judge">{c.status}</span>{" "}
                {String(c.statement || "").slice(0, 110)}
              </summary>
              <div className="row" style={{ fontSize: 12 }}><b>Statement:</b> {c.statement}</div>
              {c.confidence_reason && <div className="row" style={{ fontSize: 12 }}><b>Why this confidence:</b> {c.confidence_reason}</div>}
              {(c.alternative_explanations || []).length > 0 && (
                <div className="row" style={{ fontSize: 12 }}><b>Alternative explanations:</b> {c.alternative_explanations.join("; ")}</div>
              )}
              {support.length > 0 && (
                <div className="srclist">
                  <div className="row" style={{ fontSize: 12 }}><b>Supporting evidence ({support.length}):</b></div>
                  {support.slice(0, 6).map((s, i) => (
                    <div className="srcrow" key={i}>
                      <span className="srcq">{s.quality}</span>{" "}
                      <a href={s.url} target="_blank" rel="noreferrer">{(s.url || "").replace(/^https?:\/\/(www\.)?/, "").slice(0, 52)} ↗</a>
                      {s.excerpt && <div className="srcex">“{String(s.excerpt).slice(0, 180)}”</div>}
                    </div>
                  ))}
                </div>
              )}
              {contra.length > 0 && (
                <div className="srclist">
                  <div className="row" style={{ fontSize: 12 }}><b>Contradicting evidence ({contra.length}):</b></div>
                  {contra.slice(0, 4).map((s, i) => (
                    <div className="srcrow" key={i}>
                      <a href={s.url} target="_blank" rel="noreferrer">{(s.url || "").replace(/^https?:\/\/(www\.)?/, "").slice(0, 52)} ↗</a>
                      {s.excerpt && <div className="srcex">“{String(s.excerpt).slice(0, 180)}”</div>}
                    </div>
                  ))}
                </div>
              )}
            </details>
          );
        })}
      </div>
    </>
  );
}

/* ------------------------------ scaffolding ---------------------------- */

function useJson(url, refresh) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    fetch(url)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.statusText)))
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setErr(e); });
    return () => { cancelled = true; };
  }, [url, refresh]);
  return [data, err];
}

const MODES = ["comparative", "snapshot", "longitudinal"];
function NewRunForm({ onSubmit, focalDefault }) {
  const [company, setCompany] = useState("");
  const [compareTo, setCompareTo] = useState("");
  const [mode, setMode] = useState("comparative");
  const [exec, setExec] = useState("live"); // live by default — real data
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    if (!company.trim()) return;
    setBusy(true);
    await onSubmit({
      company: company.trim(),
      compare_to: compareTo.trim() || null,
      mode,
      execution_mode: exec,
    });
    setBusy(false);
    setCompany("");
    setCompareTo("");
  };
  return (
    <form className="newrun" onSubmit={submit}>
      <div className="nr-title">+ New analysis</div>
      <input className="nr-in" placeholder="competitor name or domain" value={company}
             onChange={(e) => setCompany(e.target.value)} />
      <input className="nr-in" placeholder={`compare to (default ${focalDefault || "rippling.com"})`}
             value={compareTo} onChange={(e) => setCompareTo(e.target.value)} />
      <div className="nr-opts">
        <select className="nr-sel" value={mode} onChange={(e) => setMode(e.target.value)}>
          {MODES.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <select className="nr-sel" value={exec} onChange={(e) => setExec(e.target.value)}>
          <option value="live">live (real data)</option>
          <option value="cached">cached</option>
          <option value="fixture">fixture (synthetic test)</option>
        </select>
      </div>
      {exec !== "live" && (
        <div className="nr-warn">
          {exec === "fixture" ? "⚠ synthetic test data — not real public info" : "replays previously fetched data"}
        </div>
      )}
      <button className="nr-btn" disabled={busy || !company.trim()}>
        {busy ? "Starting…" : exec === "live" ? "Run live analysis (a few min)" : "Run analysis"}
      </button>
    </form>
  );
}

function JobsList({ jobs }) {
  if (!jobs || jobs.length === 0) return null;
  const active = jobs.filter((j) => j.status === "pending" || j.status === "running");
  const recent = jobs.filter((j) => j.status === "error").slice(0, 3);
  if (active.length === 0 && recent.length === 0) return null;
  return (
    <div className="jobs">
      {active.map((j) => (
        <div className="jobrow" key={j.job_id}>
          <span className="spinner" /> {j.company}{j.compare_to ? ` vs ${j.compare_to}` : ""}
          <span className="jobmeta">{j.execution_mode} · {j.status}</span>
        </div>
      ))}
      {recent.map((j) => (
        <div className="jobrow err" key={j.job_id}>✕ {j.company} — {String(j.error).slice(0, 60)}</div>
      ))}
    </div>
  );
}

/* --------------------------------- app --------------------------------- */

const TABS = [
  { id: "overview", label: "Overview", tip: "Executive summary: chat, top actions, and the data at a glance" },
  { id: "product", label: "Product marketing", tip: "Message–proof gaps, recommended plays, per-vertical positioning" },
  { id: "linkedin", label: "LinkedIn", tip: "Individual employee/company posts, classified, with links" },
  { id: "changes", label: "Strategy changes", tip: "What changed between the prior and current windows — honestly labeled" },
  { id: "performance", label: "Performance marketing", tip: "Search-intent ownership, traffic estimates, commercial motion" },
  { id: "sources", label: "Sources & evidence", tip: "Every source URL, timestamp, and extracted text" },
];

export default function App() {
  const [refresh, setRefresh] = useState(0);
  const [runs] = useJson("/api/runs", refresh);
  const [selected, setSelected] = useState(null);
  const [pkg] = useJson(selected ? `/api/runs/${selected}` : null);
  const [jobs, setJobs] = useState([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [tab, setTab] = useState("overview");

  useEffect(() => {
    if (runs && runs.length && !selected) setSelected(runs[0].run_id);
  }, [runs, selected]);
  useEffect(() => { setTab("overview"); }, [selected]);

  // Seed jobs on mount — without this, a page refresh mid-run showed nothing
  // until the user started ANOTHER run (the poller only armed on submit).
  useEffect(() => {
    fetch("/api/jobs")
      .then((r) => (r.ok ? r.json() : []))
      .then((js) => { if (Array.isArray(js) && js.length) setJobs(js); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const active = jobs.some((j) => j.status === "pending" || j.status === "running");
    if (!active) return;
    const t = setInterval(() => {
      fetch("/api/jobs")
        .then((r) => r.json())
        .then((js) => { setJobs(js); setRefresh((n) => n + 1); })
        .catch(() => {});
    }, 3000);
    return () => clearInterval(t);
  }, [jobs]);

  const submitRun = async (form) => {
    try {
      const res = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (res.ok) {
        const job = await res.json();
        setJobs((prev) => [job, ...prev]);
      } else {
        const e = await res.json().catch(() => ({ detail: res.statusText }));
        alert("Error: " + (e.detail || res.statusText));
      }
    } catch (e) {
      alert("Could not reach the API — is `make api` running?");
    }
  };

  const focalDefault = (runs && runs.find((r) => r.compare_to)?.compare_to) || "rippling.com";
  const srcIdx = pkg ? themeSourceIndex(pkg) : {};

  return (
    <div className={`app ${menuOpen ? "menu-open" : ""}`}>
      <div className="topbar">
        <button className="hamburger" onClick={() => setMenuOpen((o) => !o)} aria-label="menu">☰</button>
        <span className="topbar-title">Competitive Intel</span>
      </div>
      <div className="sidebar">
        <h1>Competitive Intel</h1>
        <NewRunForm onSubmit={submitRun} focalDefault={focalDefault} />
        <JobsList jobs={jobs} />
        <p className="meta" style={{ color: "var(--muted)", fontSize: 12, marginTop: 12 }}>Runs</p>
        {!runs && <p className="empty">Loading…</p>}
        {runs && runs.length === 0 && <p className="empty">No runs yet — add one above.</p>}
        {(runs || []).map((r) => (
          <div
            key={r.run_id}
            className={`runitem ${selected === r.run_id ? "active" : ""}`}
            onClick={() => { setSelected(r.run_id); setMenuOpen(false); }}
          >
            <div className="co">{r.company_input} {r.compare_to ? `vs ${r.compare_to}` : ""}</div>
            <div className="meta">
              <span className={`badge ${r.execution_mode}`}>{r.execution_mode}</span>
              <span className="badge">{r.mode}</span>
            </div>
            <div className="meta">{r.opportunities} actions · {r.proof_gaps} gaps · {r.change_events} changes</div>
          </div>
        ))}
      </div>
      <div className="main">
        {!pkg && <p className="empty">Select a run, or add a new analysis from the panel.</p>}
        {pkg && (
          <>
            <h1>
              {pkg.scope?.company_input} {pkg.scope?.compare_to ? `vs ${pkg.scope.compare_to}` : ""}
              {" "}
              <span className={`badge ${pkg.run?.execution_mode}`}>{pkg.run?.execution_mode}</span>
              {(pkg.run?.stop_reason_label || pkg.run?.stop_reason) && (
                <span className="badge" title={`raw stop reason: ${pkg.run?.stop_reason}`}>
                  {pkg.run?.stop_reason_label || pkg.run?.stop_reason}
                </span>
              )}
            </h1>
            {pkg.run?.execution_mode === "fixture" && (
              <p className="empty">Fixture mode — synthetic, deterministic data.</p>
            )}
            <AsymmetryBanner pkg={pkg} />
            <div className="tabs" role="tablist">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  role="tab"
                  aria-selected={tab === t.id}
                  className={`tab ${tab === t.id ? "active" : ""}`}
                  title={t.tip}
                  onClick={() => setTab(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {tab === "overview" && (
              <>
                <ChatPanel key={selected} runId={selected} pkg={pkg} />
                <TopActions pkg={pkg} onOpenBoard={() => setTab("product")} />
                <Positioning pkg={pkg} />
                <DataVisuals pkg={pkg} />
              </>
            )}
            {tab === "product" && (
              <>
                <KeyTopicsComparison pkg={pkg} />
                <VerticalThemeHeatmap pkg={pkg} />
                <GapsSection pkg={pkg} srcIdx={srcIdx} />
                <Opportunities pkg={pkg} srcIdx={srcIdx} />
                <VerticalAnalysis pkg={pkg} />
              </>
            )}
            {tab === "linkedin" && (
              <>
                <LinkedInThemeBar pkg={pkg} />
                <LinkedInPosts pkg={pkg} />
                <PersonaChannelHeatmap pkg={pkg} />
              </>
            )}
            {tab === "changes" && (
              <>
                <TemporalBaseline pkg={pkg} />
                <ChangesTimeline pkg={pkg} />
                <StrategyOverTime pkg={pkg} srcIdx={srcIdx} />
              </>
            )}
            {tab === "performance" && (
              <>
                <AffinityBar pkg={pkg} />
                <CepOwnership pkg={pkg} />
                <Similarweb pkg={pkg} />
                <CommercialMotion pkg={pkg} />
              </>
            )}
            {tab === "sources" && (
              <>
                <Coverage pkg={pkg} />
                <ClaimsLedger pkg={pkg} />
                <DataHonesty pkg={pkg} />
                <Evidence pkg={pkg} />
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
