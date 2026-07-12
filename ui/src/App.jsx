import React, { useEffect, useState } from "react";
import { HBar, Heatmap, ProofBar } from "./charts";

const pill = (level) => <span className={`pill ${level}`}>{level}</span>;

// Hover explanation on every section header / metric.
const Info = ({ tip }) => <span className="info" title={tip}>ⓘ</span>;

const normTheme = (s) => String(s || "").toLowerCase().replace(/[\s_-]+/g, " ").trim();

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
        body: JSON.stringify({ question, history, execution_mode: "live", vertical: vertical || null }),
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

function DataVisuals({ pkg }) {
  const cls = pkg.classifications || [];
  const src = pkg.source_distribution || {};
  const srcData = Object.entries(src).map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
  const count = (field) => {
    const c = {};
    cls.forEach((x) => { if (x[field]) c[x[field]] = (c[x[field]] || 0) + 1; });
    return Object.entries(c).map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
  };
  const themeData = count("primary_theme").slice(0, 8);
  const stanceData = count("competitive_stance");
  if (srcData.length === 0 && themeData.length === 0) return null;
  const total = srcData.reduce((s, d) => s + d.value, 0);
  return (
    <>
      <h2>Data at a glance <Info tip="Where the evidence came from, which message themes dominate, and how openly they position against competitors. Counts are observed artifacts, never population claims." /></h2>
      <div className="grid2">
        <div className="card"><div className="title">Source mix ({total} artifacts) <Info tip="Every collected source by type — websites, archives, news, reviews, ads, LinkedIn. Click Sources & evidence for each URL." /></div><HBar data={srcData} /></div>
        <div className="card"><div className="title">Top message themes <Info tip="What the competitor talks about most, classified per page/post by the message classifier." /></div><HBar data={themeData} colorVar="--good" /></div>
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
            <SourceDrawer sources={sources} label={gap ? `sources for the “${gap.short_label}” gap` : undefined} />
          </div>
        );
      })}
    </>
  );
}

function VerticalAnalysis({ pkg }) {
  const pva = pkg.product_vertical_analysis || {};
  const verts = pva.verticals || [];
  if (verts.length === 0) return null;
  const competitor = pkg.companies?.[0]?.canonical_name || "Competitor";
  return (
    <>
      <h2>Positioning by product vertical <Info tip="How the competitor positions in EACH product category (payroll, EOR, HRIS, IT, spend…). Keyword-derived mapping over products/themes/messages/URLs — deterministic, method disclosed in the JSON. Prevents one category's narrative from masking another's." /></h2>
      <div className="card">
        <div className="heatmap-wrap">
          <table className="vtable">
            <thead><tr><th>Vertical</th><th title="pages + posts mapped to this vertical">Pages</th><th>LinkedIn</th><th>Top themes</th><th>Personas</th><th>Example</th></tr></thead>
            <tbody>
              {verts.map((v) => (
                <tr key={v.vertical} title={v.sample_message ? `sample message: ${v.sample_message}` : ""}>
                  <td><b>{v.vertical.replace(/_/g, " ")}</b></td>
                  <td>{v.n_artifacts}</td>
                  <td>{v.n_linkedin_posts}</td>
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
      <h2>{competitor} LinkedIn employee posts ({posts.length}) <Info tip="Individual public posts by the company + its employees (Exa-extracted text + real post link), each run through the full classifier — theme, stance, audience, product vertical. Click 'view post' to review it on LinkedIn." /></h2>
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
  return (
    <>
      <h2>Strategy over time ({changes.length}) <Info tip="Temporal changes between the prior and current windows. A change is only asserted with evidence in BOTH periods (real archive capture dates); emerging themes are low-confidence with a coverage-asymmetry caveat — archive absence is not real-world absence." /></h2>
      {changes.length === 0 && (
        <p className="empty">No temporal change met the both-periods evidence bar on this run.</p>
      )}
      {changes.map((c) => {
        const theme = normTheme((String(c.current_state || "").match(/[“"']([a-z_]+)[”"']/) || [])[1] || "");
        return (
          <div className="card" key={c.change_id}>
            <div className="title">
              {c.dimension} <span title="confidence — low means treat as a signal, not a fact">{pill(c.confidence)}</span>{" "}
              <span className="pill" title="lifecycle state">{c.lifecycle}</span>
            </div>
            <div className="row"><b>Prior:</b> {c.prior_state}</div>
            <div className="row"><b>Current:</b> {c.current_state}</div>
            <div className="row" style={{ color: "var(--muted)", fontSize: 12 }}>
              <b>Evidence:</b> {(c.prior_evidence_ids || []).length} prior · {(c.current_evidence_ids || []).length} current
            </div>
            {c.alternative_explanations?.length > 0 && (
              <div className="row" title="honest alternative readings of this signal"><b>Alt. explanations:</b> {c.alternative_explanations.join("; ")}</div>
            )}
            {theme && <SourceDrawer sources={srcIdx[theme]} label={`sources for “${theme}”`} />}
          </div>
        );
      })}
    </>
  );
}

/* ----------------------- performance marketing tab --------------------- */

function CepOwnership({ pkg }) {
  const ceps = pkg.category_entry_points || [];
  const focal = pkg.companies?.[1]?.canonical_name || "Rippling";
  const competitor = pkg.companies?.[0]?.canonical_name || "Competitor";
  if (ceps.length === 0) return null;
  const own = { focal_owns: "--good", contested: "--warn", competitor_advantage: "--bad", neither: "--border" };
  return (
    <>
      <h2>Search-intent ownership (category entry points) <Info tip={`Buying triggers ("opening a new country", "consolidating HR tools") and who owns the content for them: page investment by ${competitor} vs ${focal}. Target contested/lost intents with paid + comparison pages.`} /></h2>
      <div className="card">
        {ceps.slice(0, 10).map((c) => (
          <div className="gaprow" key={c.cep} style={{ gridTemplateColumns: "220px 1fr" }}>
            <div>
              <div className="gaplabel" style={{ fontSize: 12 }}>{String(c.cep).replace(/_/g, " ").slice(0, 60)}</div>
              <span className="pill" style={{ color: `var(${own[c.ownership] || "--border"})`, borderColor: `var(${own[c.ownership] || "--border"})` }}
                    title="who currently owns this buying intent">{String(c.ownership).replace(/_/g, " ")}</span>
            </div>
            <div className="gapbars" title={`${competitor}: ${c.competitor_pages} pages · ${focal}: ${c.focal_pages} pages`}>
              <HBar data={[{ label: competitor, value: c.competitor_pages }, { label: focal, value: c.focal_pages }]} />
            </div>
          </div>
        ))}
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
        {["estimated_monthly_visits", "channel_mix", "top_countries", "digital_competitors", "traffic_trend"].map((k) =>
          m[k] != null ? (
            <div className="row" key={k}>
              <b>{k.replace(/_/g, " ")}:</b> {String(m[k].value != null ? m[k].value : m[k])} <i>(estimated)</i>
            </div>
          ) : null
        )}
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
  const entries = Object.entries(cov).filter(([, v]) => v !== "not_attempted");
  return (
    <>
      <h2>Coverage & limitations <Info tip="Which research dimensions reached which coverage level, and every disclosed limitation — absences are findings, never hidden." /></h2>
      <div className="cov">
        {entries.map(([k, v]) => (
          <div className="cell" key={k} title={`research dimension: ${k.replace(/_/g, " ")}`}><span>{k}</span><span>{v}</span></div>
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
            </h1>
            {pkg.run?.execution_mode === "fixture" && (
              <p className="empty">Fixture mode — synthetic, deterministic data.</p>
            )}
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
                <ChatPanel runId={selected} pkg={pkg} />
                <Positioning pkg={pkg} />
                <DataVisuals pkg={pkg} />
              </>
            )}
            {tab === "product" && (
              <>
                <GapsSection pkg={pkg} srcIdx={srcIdx} />
                <Opportunities pkg={pkg} srcIdx={srcIdx} />
                <VerticalAnalysis pkg={pkg} />
              </>
            )}
            {tab === "linkedin" && (
              <>
                <LinkedInPosts pkg={pkg} />
                <PersonaChannelHeatmap pkg={pkg} />
              </>
            )}
            {tab === "changes" && <StrategyOverTime pkg={pkg} srcIdx={srcIdx} />}
            {tab === "performance" && (
              <>
                <CepOwnership pkg={pkg} />
                <Similarweb pkg={pkg} />
                <CommercialMotion pkg={pkg} />
              </>
            )}
            {tab === "sources" && (
              <>
                <Coverage pkg={pkg} />
                <Evidence pkg={pkg} />
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
