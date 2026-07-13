import React, { useEffect, useState } from "react";
import { HBar, Heatmap, ProofBar } from "./charts";

const pill = (level) => <span className={`pill ${level}`}>{level}</span>;

// Hover explanation on every section header / metric (focusable for keyboard/
// screen-reader users — the tip is load-bearing, not decoration).
const Info = ({ tip }) => (
  <span className="info" data-tip={tip} tabIndex={0} role="img" aria-label={tip}>ⓘ</span>
);

// Instant tooltip layer: one fixed div driven by delegated listeners over
// every [data-tip] element — shows immediately (no native-title delay), 13px
// readable text, follows the cursor, viewport-clamped. textContent ONLY (tips
// carry package-derived strings — never innerHTML).
function TooltipLayer() {
  const ref = React.useRef(null);
  React.useEffect(() => {
    const tip = ref.current;
    let anchor = null, raf = 0, lastXY = [0, 0];
    const place = () => {
      raf = 0;
      const [x, y] = lastXY, pad = 12;
      const { width, height } = tip.getBoundingClientRect();
      let left = x + pad, top = y + pad;
      if (left + width > window.innerWidth - 6) left = Math.max(6, x - width - pad);
      if (top + height > window.innerHeight - 6) top = Math.max(6, y - height - pad);
      tip.style.transform = `translate(${left}px, ${top}px)`;
    };
    const show = (el, x, y) => {
      const text = el.getAttribute("data-tip");
      if (!text) return; // conditional tips can be empty strings
      anchor = el;
      tip.textContent = text;
      tip.style.display = "block";
      lastXY = [x, y];
      place();
    };
    const hide = () => { anchor = null; tip.style.display = "none"; };
    const over = (e) => {
      const el = e.target.closest?.("[data-tip]");
      if (el !== anchor) el ? show(el, e.clientX, e.clientY) : hide();
    };
    const move = (e) => {
      if (!anchor) return;
      lastXY = [e.clientX, e.clientY];
      if (!raf) raf = requestAnimationFrame(place);
    };
    const out = (e) => { if (anchor && !anchor.contains(e.relatedTarget)) hide(); };
    const focus = (e) => {
      const el = e.target.closest?.("[data-tip]");
      if (el) { const r = el.getBoundingClientRect(); show(el, r.left, r.bottom + 6); }
    };
    document.addEventListener("mouseover", over);
    document.addEventListener("mousemove", move, { passive: true });
    document.addEventListener("mouseout", out);
    document.addEventListener("focusin", focus);
    document.addEventListener("focusout", hide);
    document.addEventListener("click", hide);
    document.addEventListener("scroll", hide, { capture: true, passive: true });
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("mouseover", over);
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseout", out);
      document.removeEventListener("focusin", focus);
      document.removeEventListener("focusout", hide);
      document.removeEventListener("click", hide);
      document.removeEventListener("scroll", hide, { capture: true });
    };
  }, []);
  return <div id="tooltip" ref={ref} role="tooltip" style={{ display: "none" }} />;
}

// Visible tab intro: the question this tab answers + why it matters — always
// on screen, never hidden behind a hover (PM feedback: justify every screen).
function TabIntro({ q, why }) {
  return (
    <div className="tabintro">
      <b>{q}</b>
      <div className="why">{why}</div>
    </div>
  );
}

// Section header with a number chip + a visible one-line justification.
function Sec({ n, title, why, tip }) {
  return (
    <>
      <h2>
        {n != null && <span className="secno">{n}</span>}
        {title}
        {tip && <Info tip={tip} />}
      </h2>
      {why && <p className="secwhy">{why}</p>}
    </>
  );
}

const normTheme = (s) => String(s || "").toLowerCase().replace(/[\s_-]+/g, " ").trim();

// Minimal rich-text renderer for chat answers — pure React nodes, NO HTML
// injection path: supports **bold**, bullet/numbered lines, and [text](url)
// links (https only).
function renderInline(text, keyBase) {
  const out = [];
  let rest = String(text);
  let k = 0;
  const rx = /\*\*([^*]+)\*\*|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/;
  while (rest) {
    const m = rest.match(rx);
    if (!m) { out.push(rest); break; }
    if (m.index > 0) out.push(rest.slice(0, m.index));
    if (m[1] != null) out.push(<b key={`${keyBase}-${k++}`}>{m[1]}</b>);
    else out.push(<a key={`${keyBase}-${k++}`} href={m[3]} target="_blank" rel="noreferrer">{m[2]}</a>);
    rest = rest.slice(m.index + m[0].length);
  }
  return out;
}

function renderRich(text) {
  const lines = String(text || "").split("\n");
  return lines.map((line, i) => {
    const bullet = line.match(/^\s*(?:[-•*]|\d+[.)])\s+(.*)$/);
    if (bullet) {
      return (
        <div key={i} style={{ display: "flex", gap: 6, margin: "2px 0" }}>
          <span style={{ color: "var(--accent)" }}>•</span>
          <span>{renderInline(bullet[1], i)}</span>
        </div>
      );
    }
    if (line.trim() === "") return <div key={i} style={{ height: 6 }} />;
    return <div key={i}>{renderInline(line, i)}</div>;
  });
}

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

// theme -> [{msg, url, st}] — the competitor's ACTUAL verbatim messaging per
// theme, for the hover popup on any classification (user ask: see the real
// wording + sources without leaving the chart).
function buildMessagingIndexes(pkg) {
  const art = {};
  (pkg.artifacts || []).forEach((a) => { art[a.artifact_id] = { url: a.url, st: a.source_type }; });
  const byArtVert = (pkg.product_vertical_analysis || {}).by_artifact || {};
  const theme = {}, cep = {}, vertical = {}, persona = {};
  const push = (map, key, row) => { if (key) (map[key] = map[key] || []).push(row); };
  (pkg.classifications || []).forEach((c) => {
    if (!c.primary_message) return;
    const a = art[c.artifact_id] || {};
    const row = { msg: c.primary_message, url: a.url, st: a.st };
    if (c.primary_theme) push(theme, normTheme(c.primary_theme), row);
    (c.category_entry_points || []).forEach((x) => push(cep, normTheme(x), row));
    (byArtVert[c.artifact_id] || []).forEach((v) => push(vertical, normTheme(v), row));
    (c.personas || []).forEach((pp) => push(persona, normTheme(pp), row));
  });
  return { theme, cep, vertical, persona };
}

// Multi-line hover popup body shared by every classification type.
function msgTipRows(rows, name, extra) {
  if (!rows || rows.length === 0) return extra || name;
  const lines = rows.slice(0, 4).map((r) => {
    const host = (r.url || "").replace(/^https?:\/\/(www\.)?/, "").split("/")[0] || "source";
    const chan = r.st === "linkedin_post" ? " · LinkedIn" : r.st === "wayback" ? " · archived" : r.st === "news" ? " · news" : "";
    return `• “${String(r.msg).slice(0, 110)}” — ${host}${chan}`;
  });
  const more = rows.length > 4 ? `\n…+${rows.length - 4} more (open the section's sources for all)` : "";
  return `${name.toUpperCase()} — their actual messaging (${rows.length} classified pages/posts):\n${lines.join("\n")}${more}${extra ? `\n\n${extra}` : ""}`;
}

const cepTip = (mi, cepName, extra) =>
  msgTipRows(((mi || {}).cep || {})[normTheme(cepName)], String(cepName || "").replace(/_/g, " "), extra);
const verticalTip = (mi, vert, extra) =>
  msgTipRows(((mi || {}).vertical || {})[normTheme(vert)], String(vert || "").replace(/_/g, " "), extra);
const personaTip = (mi, per, extra) =>
  msgTipRows(((mi || {}).persona || {})[normTheme(per)], String(per || "").replace(/_/g, " "), extra);

// Multi-line hover popup: the theme's real messaging excerpts + where each
// comes from. Plain text only (rendered via textContent; pre-line CSS).
function themeTip(msgIdx, theme, extra) {
  const rows = ((msgIdx || {}).theme ? msgIdx.theme : msgIdx || {})[normTheme(theme)] || [];
  return msgTipRows(rows, String(theme || "").replace(/_/g, " "), extra);
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
        data-tip="Click to see the exact source pages and verbatim excerpts behind this finding"
        onClick={() => setOpen((o) => !o)}
      >
        {open ? "▾" : "▸"} {label || `see exact sources (${sources.length})`}
      </button>
      {open && (
        <div className="srclist">
          {sources.slice(0, 8).map((s, i) => (
            <div className="srcrow" key={i}>
              <span className="srcq" data-tip="Source quality: high = independent/archived source, medium = their own site, low = weakly attributed — weigh the excerpt accordingly">{s.quality}</span>{" "}
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
  const artIdx = artifactIndex(pkg);
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
        <label className="chatscope-label" data-tip="Scope the chat's grounded data to one product vertical — per-offering questions get per-offering answers">
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
            <div className="chatbubble">
              {m.role === "assistant" ? renderRich(m.answer || m.content) : (m.answer || m.content)}
            </div>
            {m.role === "assistant" && (m.grounded_in || []).length > 0 && (
              <div className="chatsrc" data-tip="the parts of this run's evidence the answer was grounded in — the chat can only answer from collected data">
                {m.confidence && <span className={`pill ${m.confidence}`}>{m.confidence} confidence</span>}
                {m.grounded_in.slice(0, 6).map((gsrc, gi) => {
                  const hit = artIdx[gsrc];
                  return hit && hit.url
                    ? <a key={gi} className="chip" href={hit.url} target="_blank" rel="noreferrer">{String(gsrc).slice(0, 34)} ↗</a>
                    : <span key={gi} className="chip" style={{ cursor: "default" }}>{String(gsrc).slice(0, 44)}</span>;
                })}
              </div>
            )}
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

// Action-verb tag: execs act on verbs, not jargon. Derived from the gap
// engine's own stance verdict (attack/investigate/reframe/concede).
const ACTION_VERB = {
  attack: ["ATTACK", "--good"],
  investigate: ["INVESTIGATE", "--warn"],
  reframe: ["AVOID / REFRAME", "--bad"],
  concede: ["AVOID", "--bad"],
};
function gapVerb(g) {
  return (
    (g.attackability_detail || {}).overall ||
    (g.attackability === "high" ? "attack" : g.attackability === "medium" ? "investigate" : "reframe")
  );
}
function ActionTag({ verb, tip }) {
  const [label, color] = ACTION_VERB[verb] || [String(verb || "REVIEW").toUpperCase(), "--border"];
  return (
    <span className="atag" style={{ color: `var(${color})`, borderColor: `var(${color})` }}
          data-tip={tip || `recommended stance: ${verb}`}>{label}</span>
  );
}

// OVERVIEW — strategic scorecard: one glance = where to act. Every tile is a
// graph + an action line + a click-through to its deep-dive tab. All numbers
// come from the validated package (no new analytics, §40.6).
function StrategicScorecard({ pkg, go, msgIdx }) {
  const competitor = pkg.companies?.[0]?.canonical_name || "Competitor";
  const focal = pkg.companies?.[1]?.canonical_name || "Rippling";
  const ceps = (pkg.category_entry_points || []).filter((c) => !CEP_PLACEHOLDER.test(String(c.cep)));
  const ownCount = (o) => ceps.filter((c) => c.ownership === o).length;
  const own = {
    competitor_advantage: ownCount("competitor_advantage"),
    contested: ownCount("contested"),
    focal_owns: ownCount("focal_owns"),
    insufficient_sample: ownCount("insufficient_sample") + ownCount("not_compared") + ownCount("neither"),
  };
  const ownTotal = Math.max(1, ceps.length);
  const OWN_META = [
    ["competitor_advantage", "--bad", `${competitor} owns`],
    ["contested", "--warn", "contested"],
    ["focal_owns", "--good", `${focal} owns`],
    ["insufficient_sample", "--border", "too thin to call"],
  ];

  const tc = pkg.theme_comparison || {};
  const hasFocalThemes = Object.keys(tc.focal_shares || {}).length > 0;
  const deltas = hasFocalThemes
    ? Object.keys(tc.competitor_shares || {})
        .map((t) => ({ t, d: (tc.competitor_shares[t] || 0) - ((tc.focal_shares || {})[t] || 0) }))
        .sort((a, b) => b.d - a.d)
    : [];
  // Outlier gate: a delta needs >=4pt of corpus share AND >=3 pages on the
  // leading side — 1-page themes can't drive a CLOSE/PRESS call.
  const leadN = (t, d) => (d > 0 ? (tc.competitor_themes || {})[t] || 0 : (tc.focal_themes || {})[t] || 0);
  const theyLeadAll = deltas.filter((x) => x.d >= 0.04 && leadN(x.t, x.d) >= 3);
  const weLeadAll = deltas.filter((x) => x.d <= -0.04 && leadN(x.t, x.d) >= 3);
  const theyLead = theyLeadAll.slice(0, 3);
  const weLead = weLeadAll.slice(-3).reverse();
  const maxD = Math.max(0.01, ...deltas.map((x) => Math.abs(x.d)));

  const changes = pkg.change_events || [];
  const nEmerging = changes.filter((c) => c.lifecycle === "emerging").length;
  const repositioned = changes.filter((c) => c.lifecycle === "repositioned");
  const nStable = (pkg.temporal_baseline?.stable_themes || []).length;

  const gaps = pkg.proof_gaps || [];
  const verbs = { attack: 0, investigate: 0, reframe: 0 };
  gaps.forEach((g) => {
    const v = gapVerb(g);
    verbs[v === "concede" ? "reframe" : v] = (verbs[v === "concede" ? "reframe" : v] || 0) + 1;
  });

  if (ceps.length === 0 && gaps.length === 0 && changes.length === 0) return null;
  return (
    <>
      <Sec n={1} title="Strategic scorecard"
           why="The whole analysis in four tiles: who owns the buying intents, where they out-message you, what's moving, and how many openings say ATTACK. Click any tile to jump to its deep-dive with sources."
           tip="Ownership is share-normalized (page counts ÷ each company's classified corpus) so corpus size can't fabricate a verdict." />
      <div className="grid2">
        <div className="card sctile" onClick={() => go("performance")} data-tip="open Demand & channels → full ownership map with contributing pages">
          <div className="title">Search-intent ownership ({ceps.length} triggers)</div>
          <div className="scstack">
            {OWN_META.map(([k, color]) => own[k] > 0 && (
              <div key={k} className="scseg" style={{ width: `${(own[k] / ownTotal) * 100}%`, background: `var(${color})` }} data-tip={`${own[k]} ${k.replace(/_/g, " ")}`} />
            ))}
          </div>
          <div className="sclegend">
            {OWN_META.map(([k, color, label]) => (
              <span key={k}><span className="scdot" style={{ background: `var(${color})` }} /> {own[k]} {label}</span>
            ))}
          </div>
          <div className="scaction">→ target the {own.contested} contested intent{own.contested === 1 ? "" : "s"}; defend the {own.focal_owns} you own</div>
        </div>

        {hasFocalThemes && (
          <div className="card sctile" onClick={() => go("product")} data-tip="open Where to win → full key-topics comparison">
            <div className="title">Message-investment deltas (share of corpus)</div>
            {theyLead.map(({ t, d }) => (
              <div className="scdelta" key={t}>
                <span className="sclabel" data-tip={themeTip(msgIdx, t)}>{t.replace(/_/g, " ")}</span>
                <div className="scbarwrap"><div className="scbar comp" style={{ width: `${(d / maxD) * 100}%` }} /></div>
                <span className="atag" style={{ color: "var(--warn)", borderColor: "var(--warn)" }}>CLOSE +{Math.round(d * 100)}pt</span>
              </div>
            ))}
            {weLead.map(({ t, d }) => (
              <div className="scdelta" key={t}>
                <span className="sclabel" data-tip={themeTip(msgIdx, t)}>{t.replace(/_/g, " ")}</span>
                <div className="scbarwrap"><div className="scbar focal" style={{ width: `${(-d / maxD) * 100}%` }} /></div>
                <span className="atag" style={{ color: "var(--good)", borderColor: "var(--good)" }}>PRESS +{Math.round(-d * 100)}pt</span>
              </div>
            ))}
            <div className="scaction">→ {competitor} out-messages {focal} on {theyLeadAll.length} theme{theyLeadAll.length === 1 ? "" : "s"}; {focal} leads on {weLeadAll.length}</div>
          </div>
        )}

        <div className="card sctile" onClick={() => go("changes")} data-tip="open Changes over time → reconciled events + prior-window baseline">
          <div className="title">Theme momentum</div>
          <div className="scmomentum">
            {repositioned.map((c) => (
              <span key={c.change_id} className="atag" style={{ color: "var(--bad)", borderColor: "var(--bad)" }}
                    data-tip={`their dominant ${String(c.dimension).replace(/_/g, " ")} shifted: ${c.prior_state} → ${c.current_state} (low-confidence — see Changes over time)`}>
                SHIFTED: {String(c.prior_state).slice(0, 16)} → {String(c.current_state).slice(0, 16)}
              </span>
            ))}
            <span className="atag" style={{ color: "var(--warn)", borderColor: "var(--warn)" }}
                  data-tip="themes absent from the prior window that appear now — genuinely new messaging (count-based 'expanding' is excluded: with asymmetric windows raw growth is guaranteed)">
              {nEmerging} NEW THEME{nEmerging === 1 ? "" : "S"}
            </span>
            <span className="atag" style={{ color: "var(--muted)", borderColor: "var(--border)" }}
                  data-tip="themes present in BOTH windows — their messaging spine; don't expect these to move">
              {nStable} STABLE
            </span>
          </div>
          <div className="scaction">
            → {repositioned.length > 0
              ? "their story is shifting — read the two-window comparison before it hardens"
              : nEmerging > 0
              ? `watch the ${nEmerging} new theme${nEmerging === 1 ? "" : "s"} — counter before they harden`
              : "no messaging movement detected — their story is static"}
          </div>
        </div>

        <div className="card sctile" onClick={() => go("product")} data-tip="open Where to win → gaps with sources + Action Board">
          <div className="title">Attack surface ({gaps.length} repeated claims)</div>
          <div className="scmomentum">
            <span className="atag" style={{ color: "var(--good)", borderColor: "var(--good)" }}>{verbs.attack || 0} ATTACK</span>
            <span className="atag" style={{ color: "var(--warn)", borderColor: "var(--warn)" }}>{verbs.investigate || 0} INVESTIGATE</span>
            <span className="atag" style={{ color: "var(--bad)", borderColor: "var(--bad)" }}>{verbs.reframe || 0} AVOID</span>
          </div>
          <div className="scaction">
            → {verbs.attack > 0
              ? `${verbs.attack} claim${verbs.attack === 1 ? "" : "s"} they can't prove and ${focal} can — start there`
              : "no clean attack this run — build proof on the investigate list first"}
          </div>
        </div>
      </div>
    </>
  );
}

// EXEC — the Overview tab's tooltip promises "top actions"; deliver them here
// instead of burying the Action Board below six gap rows on another tab.
function TopActions({ pkg, onOpenBoard }) {
  const opps = (pkg.opportunities || []).slice(0, 3);
  const focal = pkg.companies?.[1]?.canonical_name || "Rippling";
  const gapsById = {};
  (pkg.proof_gaps || []).forEach((g) => { gapsById[g.claim_id] = g; });
  if (opps.length === 0) return null;
  return (
    <>
      <Sec n={2} title={`Top actions for ${focal}`}
           why="The three highest-ranked plays this run surfaced — each already carries a metric and a kill rule, so it can go on a campaign board as-is. The full experiment plans live on the Where-to-win tab."
           tip="Ranking blends structural defensibility, proof status, and product comparability — not just gap size." />
      <div className="card">
        {opps.map((o) => (
          <div className="tarow" key={o.opportunity_id}>
            <div className="talabel">
              <b>{o.title}</b> <span className="pill">{o.deliverable_type}</span>
              {(o.supporting_claim_ids || []).map((id) => gapsById[id]).filter(Boolean).some((g) => g.outlier_flag === "thin_theme" || (g.theme_page_count != null && g.theme_page_count < 5)) && (
                <span className="atag" style={{ color: "var(--muted)", borderColor: "var(--border)" }}
                      data-tip="the gap behind this play rests on very few competitor pages — verify their pages before committing spend">THIN — verify first</span>
              )}
            </div>
            <div className="row" style={{ fontSize: 12 }}>{o.message_angle}</div>
            <div className="row" style={{ fontSize: 12, color: "var(--muted)" }}>
              <b>metric:</b> {o.primary_metric || "—"} · <b>kill rule:</b> {o.kill_rule || "—"}
            </div>
          </div>
        ))}
        <button type="button" className="srcbtn" onClick={onOpenBoard}
                data-tip="Full Action Board: proof status, backfire risk, experiment plans, staged gates">
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

function DataVisuals({ pkg, msgIdx }) {
  const cls = pkg.classifications || [];
  const src = pkg.source_distribution || {};
  const srcData = Object.entries(src).map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
  const count = (field) => {
    const c = {};
    cls.forEach((x) => { if (x[field]) c[x[field]] = (c[x[field]] || 0) + 1; });
    return Object.entries(c)
      .map(([label, value]) => ({ label: label.replace(/_/g, " "), rawLabel: label, value }))
      .sort((a, b) => b.value - a.value);
  };
  const allThemes = count("primary_theme");
  const themeData = allThemes.slice(0, 8).map((d) => ({ ...d, tip: themeTip(msgIdx, d.rawLabel) }));
  const stanceData = count("competitive_stance");
  if (srcData.length === 0 && themeData.length === 0) return null;
  const total = srcData.reduce((s, d) => s + d.value, 0);
  const themeTitle = allThemes.length > 8 ? `Top message themes (8 of ${allThemes.length}, primary per page)` : "Top message themes (primary per page)";
  return (
    <>
      <Sec n={4} title="Data at a glance"
           why="What this analysis is built on: the source mix, their loudest themes, and how openly they attack competitors. If a finding surprises you, start here to judge the evidence behind it."
           tip="Counts are observed artifacts from this run — never population claims." />
      <div className="grid2">
        <div className="card"><div className="title">Source mix ({total} artifacts) <Info tip="Every collected source by type — websites, archives, news, reviews, ads, LinkedIn. Click Sources & evidence for each URL." /></div><HBar data={srcData} /></div>
        <div className="card"><div className="title">{themeTitle} <Info tip="What the competitor talks about most — each page/post's PRIMARY theme (temporal counts elsewhere also include supporting themes and say so). Hover any bar for their actual wording + sources." /></div><HBar data={themeData} colorVar="--good" /></div>
        <div className="card"><div className="title">Competitive stance <Info tip="How each page/post positions vs competitors: ignores → implicit contrast → named comparison → direct attack." /></div><HBar data={stanceData} colorVar="--warn" /></div>
      </div>
    </>
  );
}

function Positioning({ pkg, msgIdx }) {
  const dom = pkg.dominant_message || {};
  const cls = pkg.classifications || [];
  const villains = new Set();
  const villainWording = [];
  cls.forEach((c) => {
    (c.villain_normalized || []).forEach((v) => villains.add(v));
    (c.villain_exact_wording || []).forEach((w) => { if (w && villainWording.length < 5 && !villainWording.includes(w)) villainWording.push(w); });
  });
  const villainTip = villainWording.length
    ? `their exact villain wording:\n${villainWording.map((w) => `• “${String(w).slice(0, 120)}”`).join("\n")}`
    : "the status-quo enemies their copy attacks";
  return (
    <>
      <Sec n={3} title="Current public positioning"
           why="Their company-level story in one line — what they lead with and who they attack. Use it to know the narrative you're up against before reading anything else."
           tip="Dominant message = authority-weighted (homepage/platform pages outrank blog posts) and only asserted when repeated across ≥2 source classes." />
      <div className="card">
        {dom.label && <div className="row"><b>Dominant message:</b> {dom.label}</div>}
        {dom.theme && <div className="row"><b>Theme:</b> {dom.theme} <span className="pill">{dom.is_company_level ? "company-level" : "corpus-level"}</span></div>}
        {villains.size > 0 && (
          <div className="row">
            <b data-tip={villainTip}>Villains attacked:</b>{" "}
            <span data-tip={villainTip} style={{ borderBottom: "1px dotted var(--border)" }}>{[...villains].join(", ")}</span>
          </div>
        )}
      </div>
    </>
  );
}

/* ----------------------- product marketing tab ------------------------- */

function GapsSection({ pkg, srcIdx, msgIdx }) {
  const gaps = pkg.proof_gaps || [];
  const focal = pkg.companies?.[1]?.canonical_name || "Rippling";
  const competitor = pkg.companies?.[0]?.canonical_name || "Competitor";
  if (gaps.length === 0) return <p className="empty">No repeated competitor claim with a proof gap observed.</p>;
  return (
    <>
      <Sec n={4} title="Message–proof gaps"
           why={`Every claim ${competitor} repeats, scored on how well they prove it vs how well ${focal} could. The verb tag on each row is the recommended move; click into a row's sources for the exact pages behind the verdict.`}
           tip="Proof strength is the modal per-page rating for the theme (one strong page can't inflate it); THIN SAMPLE flags verdicts on too few pages." />
      <div className="card">
        {gaps.map((g) => (
          <div className="gaprow" key={g.claim_id}>
            <div>
              <div className="gaplabel" data-tip={themeTip(msgIdx, g.short_label, `Their repeated claim: “${g.claim_text}”`)}>{g.short_label}</div>
              <ActionTag verb={gapVerb(g)} tip={`attackability ${g.attackability} → ${gapVerb(g)}`} />
              {(g.outlier_flag === "thin_theme" || (g.theme_page_count != null && g.theme_page_count < 5) || (g.sample_sufficiency && g.sample_sufficiency !== "ok")) && (
                <span className="atag" style={{ color: "var(--muted)", borderColor: "var(--border)" }}
                      data-tip={`this verdict rests on ${g.theme_page_count ?? "too few"} competitor pages${g.outlier_flag === "thin_theme" ? " — below the ≥5-page/≥15% ATTACK floor" : ""} — disclosed, not asserted; verify before spending`}>
                  THIN{g.theme_page_count != null ? ` n=${g.theme_page_count}` : " SAMPLE"}
                </span>
              )}
            </div>
            <div className="gapbars">
              <ProofBar strength={g.proof_strength} label={competitor} />
              <ProofBar strength={g.focal_proof_strength} label={focal} />
              <div className="gapclaim" data-tip="What proof is missing, and how specific the claim is">
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
      <Sec n={5} title={`Action Board — plays for ${focal}`}
           why="The gaps turned into runnable plays, ranked P1–P3. Each card has the full experiment design (metric, guardrails, staged proceed/stop gates, kill rule) — expand it and hand it to the team."
           tip="Every play passed genericness and superiority critics; backfire risk and legal-review flags are stated per card." />
      {opps.map((o, i) => {
        const gap = (o.supporting_claim_ids || []).map((id) => gapsById[id]).find(Boolean);
        const sources = gap ? srcIdx[normTheme(gap.short_label)] : null;
        return (
          <div className="card" key={o.opportunity_id}>
            <div className="title">
              <span className="atag" style={{ color: "var(--accent)", borderColor: "var(--accent)" }}
                    data-tip="priority = the engine's overall ranking (defensibility, proof status, comparability)">P{i + 1}</span>{" "}
              {o.title} <span className="pill" data-tip="What you'd actually ship — demo asset, landing page, battlecard — so the right team can pick it up">{o.deliverable_type}</span>{" "}
              <span data-tip="structural defensibility — how hard this is for the competitor to copy">{pill(o.structural_defensibility)}</span>
            </div>
            <div className="row"><b>Angle:</b> {o.message_angle}</div>
            <div className="row">
              <b data-tip={`${focal}'s own publishable proof for this angle`}>Focal proof:</b> {o.focal_proof_status} ·{" "}
              <b data-tip={`is ${focal} already using this angle?`}>already-saying-it:</b> {o.focal_current_usage} ·{" "}
              <b>legal review:</b> {o.legal_review_required ? "required" : "not required"}
            </div>
            <div className="row"><b>Why it could backfire:</b> {o.why_this_could_backfire}</div>
            <div className="row"><b>Experiment:</b> {o.experiment_hypothesis}</div>
            <div className="row"><b>Kill rule:</b> {o.kill_rule}</div>
            <details className="expplan">
              <summary data-tip="The full experiment design an IC needs to run this: metrics, guardrails, sample floor, iterate/scale rules, and per-stage proceed/stop gates">
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

// PRODUCT MARKETING — attack/defend quadrant: each repeated claim plotted by
// THEIR proof (x) vs OUR proof (y). The quadrant IS the action.
const PROOF_LVL = { none: 0, weak: 1, moderate: 2, medium: 2, strong: 3, high: 3 };
function AttackDefendMatrix({ pkg, msgIdx }) {
  const gaps = pkg.proof_gaps || [];
  if (gaps.length === 0) return null;
  const competitor = pkg.companies?.[0]?.canonical_name || "Competitor";
  const focal = pkg.companies?.[1]?.canonical_name || "Rippling";
  // Color = the STANCE VERB (attack/investigate/avoid) so color, legend tag,
  // and recommendation can never disagree (user: "map doesn't show stances").
  const verbCol = { attack: "--good", investigate: "--warn", reframe: "--bad", concede: "--bad" };
  const dotCol = (g) => `var(${verbCol[gapVerb(g)] || "--border"})`;
  const whyOf = (g) => String(g.actionable_interpretation || "").split(". ")[0];
  const thin = (g) => g.outlier_flag === "thin_theme" || (g.theme_page_count != null && g.theme_page_count < 5) || (g.sample_sufficiency && g.sample_sufficiency !== "ok");
  // Quadrant membership from proof positions (matches dot placement).
  const quad = (g) => {
    const x = PROOF_LVL[String(g.proof_strength || "none").toLowerCase()] || 0;
    const y = PROOF_LVL[String(g.focal_proof_strength || "none").toLowerCase()] || 0;
    return x <= 1 ? (y >= 2 ? "ATTACK" : "BUILD PROOF") : y >= 2 ? "DIFFERENTIATE" : "AT RISK";
  };
  const quadCounts = {};
  gaps.forEach((g) => {
    const q = quad(g);
    (quadCounts[q] = quadCounts[q] || []).push(gapVerb(g).toUpperCase());
  });
  return (
    <>
      <Sec n={1} title="Attack / defend map"
           why={`Each repeated ${competitor} claim, plotted by how well THEY prove it (→) vs how well ${focal} could (↑). The quadrant a dot lands in IS the action: attack, differentiate, build proof, or shore up risk. Numbers match the legend below.`}
           tip="Dot color = attackability (green attack · yellow investigate · red avoid). Positions use each side's observed proof strength from the classified corpus." />
      <div className="card">
        <div className="admwrap">
          <div className="admy">{focal} proof →</div>
          <div className="admgrid">
            <div className="admq tl">ATTACK<br /><i>they're weak, we're strong</i></div>
            <div className="admq tr">DIFFERENTIATE<br /><i>both strong</i></div>
            <div className="admq bl">BUILD PROOF<br /><i>whitespace</i></div>
            <div className="admq br">AT RISK<br /><i>they're strong, we're weak</i></div>
            {gaps.map((g, i) => {
              const cx = (PROOF_LVL[String(g.proof_strength || "none").toLowerCase()] || 0) / 3 * 100;
              const cy = (PROOF_LVL[String(g.focal_proof_strength || "none").toLowerCase()] || 0) / 3 * 100;
              const x = Math.min(94, Math.max(6, cx + ((i % 3) - 1) * 5));
              const y = Math.min(92, Math.max(8, cy + ((Math.floor(i / 3) % 3) - 1) * 6));
              return (
                <div key={g.claim_id} className="admdot"
                     style={{ left: `${x}%`, bottom: `${y}%`, background: dotCol(g) }}
                     data-tip={themeTip(msgIdx, g.short_label,
                       `${i + 1}. ${gapVerb(g).toUpperCase()}${thin(g) ? ` (THIN — n=${g.theme_page_count ?? "?"} pages)` : ""}: ${competitor} ${g.proof_strength} · ${focal} ${g.focal_proof_strength}. ${whyOf(g)}.`)}>
                  {i + 1}
                </div>
              );
            })}
          </div>
        </div>
        <div className="admx">{competitor} proof →</div>
        <div className="row" style={{ fontSize: 12, color: "var(--muted)" }}>
          {Object.entries(quadCounts).map(([q, verbs]) => (
            <span key={q} className="atag" data-tip={`quadrant = proof geometry (their proof vs yours); the tag on each dot = the FINAL stance after thin-sample and strong-page checks — they can differ, and the tag wins`}>
              {q}: {verbs.length} ({[...new Set(verbs)].join("/")})
            </span>
          ))}
        </div>
        <ol className="admlegend">
          {gaps.map((g, i) => (
            <li key={g.claim_id}>
              <span className="admnum" style={{ background: dotCol(g) }}>{i + 1}</span>
              <b data-tip={themeTip(msgIdx, g.short_label)}>{g.short_label}</b> <ActionTag verb={gapVerb(g)} tip={whyOf(g)} />
              {thin(g) && (
                <span className="atag" style={{ color: "var(--muted)", borderColor: "var(--border)" }}
                      data-tip={`this verdict rests on ${g.theme_page_count ?? "few"} competitor pages — below the ≥5-page/≥15%-of-corpus floor; treat as a lead to verify, not a call`}>
                  THIN n={g.theme_page_count ?? "?"}
                </span>
              )}
              <span className="admproof">{competitor} {g.proof_strength} · {focal} {g.focal_proof_strength}</span>
            </li>
          ))}
        </ol>
      </div>
    </>
  );
}

// PRODUCT MARKETING — key related topics per company (side-by-side theme bars).
function KeyTopicsComparison({ pkg, msgIdx, srcIdx }) {
  const [openTheme, setOpenTheme] = useState(null);
  const tc = pkg.theme_comparison || {};
  // No focal mirror = no cross-company comparison — rendering 0-bars would
  // fabricate a measured absence (competitor themes live in Data at a glance).
  if (tc.focal_n_classified === 0) return null;
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
      <Sec n={6} title={`Key topics — ${competitor} vs ${focalName}`}
           why="Reference: the share of each company's site that each theme occupies, side by side. Big share gaps are message-investment gaps — the scorecard's CLOSE GAP / PRESS tags come from here."
           tip="Bars compare SHARE of each company's classified corpus (raw counts alongside), so different corpus sizes stay comparable; themes outside one side's top-10 carry their true count, never a fabricated zero." />
      <div className="card">
        {themes.map((t) => (
          <div key={t}>
          <div className="ktrow" style={{ cursor: "pointer" }} onClick={() => setOpenTheme(openTheme === t ? null : t)}>
            <div className="ktlabel" data-tip={themeTip(msgIdx, t, "click to open the exact source pages")}>{openTheme === t ? "▾ " : ""}{t.replace(/_/g, " ")}</div>
            <div className="ktbars">
              <div className="ktbar comp" style={{ width: `${(w(t, compSh, comp) / max) * 100}%` }}
                   data-tip={`${competitor}: ${lbl(t, compSh, comp)}`} />
              <span className="ktnum">{lbl(t, compSh, comp)}</span>
            </div>
            <div className="ktbars">
              <div className="ktbar focal" style={{ width: `${(w(t, focalSh, focal) / max) * 100}%` }}
                   data-tip={`${focalName}: ${lbl(t, focalSh, focal)}`} />
              <span className="ktnum">{lbl(t, focalSh, focal)}</span>
            </div>
          </div>
          {openTheme === t && (
            <div className="srclist">
              {(srcIdx[normTheme(t)] || []).slice(0, 8).map((sr, i) => (
                <div className="srcrow" key={i}>
                  <a href={sr.url} target="_blank" rel="noreferrer">{(sr.url || "").replace(/^https?:\/\/(www\.)?/, "").slice(0, 56)} ↗</a>
                  {sr.excerpt && <div className="srcex">“{String(sr.excerpt).slice(0, 180)}”</div>}
                </div>
              ))}
              {(srcIdx[normTheme(t)] || []).length === 0 && <div className="srcrow empty">competitor-side sources index this theme only via the focal corpus</div>}
            </div>
          )}
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
function VerticalThemeHeatmap({ pkg, msgIdx }) {
  const verts = (pkg.product_vertical_analysis?.verticals || []).filter((v) => v.theme_counts);
  if (verts.length === 0) return null;
  const themes = [...new Set(verts.flatMap((v) => Object.keys(v.theme_counts || {})))].slice(0, 8);
  if (themes.length === 0) return null;
  const cells = {};
  verts.forEach((v) => { cells[v.vertical] = v.theme_counts || {}; });
  return (
    <>
      <Sec n={7} title="Themes by product vertical"
           why="Reference: what they say INSIDE each product line. A theme that's loud in one vertical and silent in another is a per-offering choice you can counter vertical-by-vertical."
           tip="Cell intensity = classified pages/posts carrying that theme in that vertical (deterministic keyword mapping, method in the JSON)." />
      <div className="card">
        <Heatmap personas={verts.map((v) => v.vertical)} channels={themes} cells={cells}
                 rowTip={(v) => verticalTip(msgIdx, v)}
                 colTip={(t) => themeTip(msgIdx, t)}
                 cellTip={(v, t, n) => themeTip(msgIdx, t, `${String(v).replace(/_/g, " ")} × ${String(t).replace(/_/g, " ")}: ${n} classified pages/posts`)} />
      </div>
    </>
  );
}

// LINKEDIN — what employees post about (theme bar).
function LinkedInThemeBar({ pkg, msgIdx }) {
  const posts = pkg.linkedin_posts || [];
  const [openTheme, setOpenTheme] = useState(null);
  if (posts.length === 0) return null;
  const c = {};
  posts.forEach((p) => { if (p.theme) c[p.theme] = (c[p.theme] || 0) + 1; });
  const data = Object.entries(c).map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
  if (data.length === 0) return null;
  const max = Math.max(1, ...data.map((d) => d.value));
  return (
    <>
      <Sec n={2} title="Post themes — click a theme to read the posts"
           why="What the org actually amplifies in the feed — employee posts are the unofficial roadmap and often move before the website does. Click any bar to see exactly what they're saying and who's saying it."
           tip="Counted across the collected employee/company posts, each individually classified. Hover a bar for the wording; click for the full posts with links." />
      <div className="card">
        {data.map((d) => (
          <div key={d.label}>
            <div className="hbar-row" style={{ cursor: "pointer" }}
                 onClick={() => setOpenTheme(openTheme === d.label ? null : d.label)}
                 data-tip={themeTip(msgIdx, d.label, "click to open the posts")}>
              <div className="hbar-label">{openTheme === d.label ? "▾ " : "▸ "}{d.label.replace(/_/g, " ")}</div>
              <div className="hbar-track"><div className="hbar-fill" style={{ width: `${(d.value / max) * 100}%`, background: "var(--accent)" }} /></div>
              <div className="hbar-val">{d.value}</div>
            </div>
            {openTheme === d.label && (
              <div className="srclist">
                {posts.filter((pp) => pp.theme === d.label).map((pp) => (
                  <div className="srcrow" key={pp.artifact_id}>
                    <b>{pp.author || "?"}</b>{pp.author_role ? ` · ${pp.author_role}` : ""}{" "}
                    <a href={pp.post_url} target="_blank" rel="noreferrer">view post ↗</a>
                    <div className="srcex">“{String(pp.excerpt || pp.primary_message || "").slice(0, 220)}”</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}

// STRATEGY CHANGES — customize the comparison windows for THIS run and see
// the temporal story recomputed live (deterministic server recount; the saved
// report always keeps the run's original windows).
function WindowPicker({ runId, overlay, onOverlay }) {
  const [lookback, setLookback] = useState(365);
  const [currentDays, setCurrentDays] = useState(90);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const apply = async () => {
    setBusy(true); setErr(null);
    try {
      const res = await fetch(`/api/runs/${runId}/rewindow`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lookback_days: lookback, current_days: currentDays }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
      onOverlay(await res.json());
    } catch (e) {
      setErr(String(e.message || e));
    }
    setBusy(false);
  };
  return (
    <div className="card">
      <div className="title">Customize the comparison windows
        <Info tip="Re-slices this run's already-collected artifacts by date and recounts the baseline + change events — pure counting, instant, nothing is re-fetched. Use it to test whether a 'change' depends on where the line is drawn." />
      </div>
      <div className="winpick">
        <div className="nr-field">
          <label data-tip="How far back the 'before' period reaches">History to compare against</label>
          <select className="nr-sel" value={lookback} onChange={(e) => setLookback(Number(e.target.value))}>
            {LOOKBACK_CHOICES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
        <div className="nr-field">
          <label data-tip="How many trailing days count as 'now'">Recent window</label>
          <select className="nr-sel" value={currentDays} onChange={(e) => setCurrentDays(Number(e.target.value))}>
            {CURRENT_CHOICES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
        <button type="button" className="nr-btn" disabled={busy || currentDays >= lookback} onClick={apply}>
          {busy ? "Recounting…" : "Apply windows"}
        </button>
        {overlay && (
          <button type="button" className="nr-btn reset" onClick={() => onOverlay(null)}
                  data-tip="Back to the windows this run was analyzed and saved with">
            Reset to run windows
          </button>
        )}
      </div>
      {currentDays >= lookback && <div className="nr-warn">recent window must be shorter than the history range</div>}
      {err && <div className="nr-warn">could not recount: {err}</div>}
    </div>
  );
}

// STRATEGY CHANGES — prior-vs-current window baseline (what WAS observed then).
function TemporalBaseline({ pkg, msgIdx }) {
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
      <Sec n={1} title="Prior vs current window"
           why={`What their story looked like THEN (${pw.start} → ${pw.end}, ${pw.n_artifacts} dated artifacts) vs now — the baseline every 'change' below is checked against. Stable themes = their spine; don't expect those to move.`}
           tip="Theme counts = artifacts carrying the theme as primary OR supporting (same rule as the change events); windows have different sample sizes, so compare shares." />
      <div className="card">
        <div className="ktlegend" style={{ marginBottom: 8 }}>
          <span><span className="ktbar comp" style={{ width: 14, display: "inline-block", height: 8 }} /> prior ({pw.n_artifacts} artifacts, {pw.start} → {pw.end})</span>
          <span><span className="ktbar focal" style={{ width: 14, display: "inline-block", height: 8 }} /> current ({cw.n_artifacts})</span>
        </div>
        {themes.map((t) => (
          <div className="ktrow" key={t}>
            <div className="ktlabel" data-tip={themeTip(msgIdx, t)}>{t.replace(/_/g, " ")}</div>
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
function ChangesTimeline({ pkg, msgIdx }) {
  const changes = pkg.change_events || [];
  if (changes.length === 0) return null;
  const max = Math.max(1, ...changes.map((c) => (c.current_evidence_ids || []).length));
  const themeOf = (c) =>
    c.lifecycle === "repositioned"
      ? `${c.prior_state} → ${c.current_state}`
      : ((String(c.current_state || "").match(/[“"']([a-z_]+)[”"']/) || [])[1] || c.dimension || "change").replace(/_/g, " ");
  return (
    <>
      <Sec n={2} title="Change timeline"
           why="Each moving theme at a glance: how present it was before (left) vs now (bar). EMERGING = genuinely new; EXPANDING = present before, louder now."
           tip="Prior counts are reconciled against the full corpus at render — an event can never claim 'not observed' about a theme the baseline saw." />
      <div className="card">
        {changes.map((c) => {
          // Real reconciled prior counts — never a hardcoded "not observed"
          // (the baseline above can hold the theme). Fallback keeps old
          // packages rendering.
          const hasCounts = Number.isFinite(c.prior_theme_count) && Number.isFinite(c.prior_window_n);
          const priorLabel = c.lifecycle === "repositioned"
            ? `prior: ${c.prior_state}`
            : hasCounts
            ? (c.prior_theme_count > 0
                ? `prior: ${c.prior_theme_count} of ${c.prior_window_n}`
                : `prior: not observed (${c.prior_window_n} dated)`)
            : "prior: not observed";
          const priorTitle = c.lifecycle === "repositioned"
            ? `their dominant ${String(c.dimension).replace(/_/g, " ")} in the prior window was '${c.prior_state}'; now it's '${c.current_state}' — a repositioning, not an emergence`
            : hasCounts
            ? (c.prior_theme_count > 0
                ? `prior window: theme present in ${c.prior_theme_count} of ${c.prior_window_n} dated artifacts`
                : `prior window: theme absent from all ${c.prior_window_n} dated artifacts`)
            : `prior window: ${(c.prior_evidence_ids || []).length} artifacts sampled`;
          return (
            <div className="tlrow2" key={c.change_id}>
              <div className="tllabel" data-tip={themeTip(msgIdx, themeOf(c), c.current_state)}>{themeOf(c)} {pill(c.confidence)} <span className="pill">{c.lifecycle}</span></div>
              <div className="tlprior2" data-tip={priorTitle}>{priorLabel}</div>
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
      <Sec n={3} title="Audience-affinity competitors (Similarweb, estimated)"
           why="Who their audience actually shops against — a demand-side reality check on the competitor set."
           tip="Affinity is a rank INDEX (0–1, top-normalized), not a percentage audience overlap; all values estimated." />
      <div className="card">
        <HBar data={data} />
        <p className="empty" style={{ fontSize: 11, marginBottom: 0 }}>affinity index (0–1, top-normalized) — not % overlap · all values estimated</p>
      </div>
    </>
  );
}

function VerticalAnalysis({ pkg, msgIdx }) {
  const pva = pkg.product_vertical_analysis || {};
  const verts = pva.verticals || [];
  if (verts.length === 0) return null;
  const competitor = pkg.companies?.[0]?.canonical_name || "Competitor";
  const focalName = pkg.companies?.[1]?.canonical_name || "Rippling";
  const hasFocal = verts.some((v) => v.focal_n_artifacts);
  const pct = (x) => `${Math.round((x || 0) * 100)}%`;
  return (
    <>
      <Sec n={8} title="Positioning by product vertical"
           why="Reference table: pages, LinkedIn posts, themes, and personas per product line — with share columns so even a niche competitor's per-product investment compares fairly. Scope the Overview chat to a vertical for follow-ups."
           tip={`Keyword-derived mapping over products/themes/messages/URLs — deterministic, method disclosed in the JSON.${hasFocal ? " Share columns divide by each company's classified corpus." : ""}`} />
      <div className="card">
        <div className="heatmap-wrap">
          <table className="vtable">
            <thead><tr>
              <th>Vertical</th><th data-tip="pages + posts mapped to this vertical">Pages</th><th>LinkedIn</th>
              {hasFocal && <th data-tip={`${focalName} pages mapped to this vertical (from the focal mirror run)`}>{focalName} pages</th>}
              {hasFocal && <th data-tip="share of each company's classified corpus — corpus-size-normalized">Share (comp / focal)</th>}
              <th>Top themes</th><th>Personas</th><th>Example</th>
            </tr></thead>
            <tbody>
              {verts.map((v) => (
                <tr key={v.vertical} data-tip={v.sample_message ? `sample message: ${v.sample_message}` : ""}>
                  <td><b>{v.vertical.replace(/_/g, " ")}</b></td>
                  <td>{v.n_artifacts}</td>
                  <td>{v.n_linkedin_posts}</td>
                  {hasFocal && <td>{v.focal_n_artifacts || 0}</td>}
                  {hasFocal && <td>{pct(v.competitor_share)} / {pct(v.focal_share)}</td>}
                  <td>{(v.top_themes || []).length
                    ? v.top_themes.map((t, ti) => (
                        <span key={t} data-tip={themeTip(msgIdx, t)} style={{ borderBottom: "1px dotted var(--border)" }}>
                          {t}{ti < v.top_themes.length - 1 ? ", " : ""}
                        </span>
                      ))
                    : "—"}</td>
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

/* ------------------- EDA insight graphics (marketing-ops) --------------- */

const pct = (x) => `${Math.round((x || 0) * 100)}%`;

// Dumbbell row: two dots on a 0-100% track, n labels. The distance IS the story.
function Dumbbell({ label, aPct, bPct, aLabel, bLabel, aColor = "--bad", bColor = "--good", note, tip, thin }) {
  return (
    <div className="dbrow" data-tip={tip || note || `${aLabel} vs ${bLabel}`}>
      <div className="dblabel">
        {label}
        {thin && (
          <span className="atag" style={{ color: "var(--muted)", borderColor: "var(--border)" }}
                data-tip={`small sample (${thin}) — rates on this row are coin-flip territory; verify before acting`}>THIN</span>
        )}
      </div>
      <div className="dbtrack">
        <div className="dbline" style={{ left: `${Math.min(aPct, bPct)}%`, width: `${Math.abs(aPct - bPct)}%` }} />
        <div className="dbdot" style={{ left: `${aPct}%`, background: `var(${aColor})` }} data-tip={aLabel} />
        <div className="dbdot" style={{ left: `${bPct}%`, background: `var(${bColor})` }} data-tip={bLabel} />
      </div>
      <div className="dbvals">{aLabel} · {bLabel}</div>
    </div>
  );
}

function InsightHeader({ block, n, boardTip }) {
  return (
    <>
      <h2>
        {n != null && <span className="secno">{n}</span>}
        <span className="atag" style={{ color: "var(--accent)", borderColor: "var(--accent)" }}>{block.board_column}</span>{" "}
        {block.title}
        <Info tip={boardTip || "EDA-derived: a deterministic cross-cutting join over this run's classified corpus; every number carries its denominator. Full series in the JSON."} />
      </h2>
      <p className="secwhy"><b>Read it in 5s:</b> {block.read_in_5s}</p>
    </>
  );
}

// [ATTACK] compliance voiced vs certification shown, + CEP hit list/guardrail.
function ClaimVsRecord({ pkg, msgIdx }) {
  const b = (pkg.insight_graphics || {}).claim_vs_record;
  if (!b) return null;
  const comp = pkg.companies?.[0]?.canonical_name || "Competitor";
  const focal = pkg.companies?.[1]?.canonical_name || "Rippling";
  const c = b.competitor, f = b.focal;
  return (
    <>
      <InsightHeader block={b} n={2} />
      <div className="card">
        <Dumbbell label={`${comp} (n=${c.n_classified})`} aPct={c.voice_share * 100} bPct={c.cert_rate * 100}
                  aLabel={`voiced ${c.voice_n}/${c.n_classified} = ${pct(c.voice_share)}`}
                  bLabel={`record ${c.cert_n}/${c.voice_n} = ${pct(c.cert_rate)}`} />
        {f && (
          <Dumbbell label={`${focal} (n=${f.n_classified})`} aPct={f.voice_share * 100} bPct={f.cert_rate * 100}
                    aLabel={`voiced ${f.voice_n}/${f.n_classified} = ${pct(f.voice_share)}`}
                    bLabel={`record ${f.cert_n}/${f.voice_n} = ${pct(f.cert_rate)}`}
                    aColor="--accent" bColor="--good" />
        )}
        <div className="row" style={{ fontSize: 12, color: "var(--muted)" }}>
          {comp} substitutes quantified outcomes on {pct(c.quant_standin_rate)} of compliance pages — stories, not records.
        </div>
        {(b.cep_hit_list || []).length > 0 && (
          <div className="row" style={{ fontSize: 12 }}>
            <b>Cert hit list (buy these intents):</b>{" "}
            {b.cep_hit_list.map((h) => (
              <span key={h.cep} className="atag" style={{ color: "var(--good)", borderColor: "var(--good)" }}
                    data-tip={cepTip(msgIdx, h.cep, `${focal} record rate ${pct(h.focal.rate)} (n=${h.focal.n}) vs ${comp} ${pct(h.competitor.rate)} (n=${h.competitor.n})`)}>
                {h.cep.replace(/_/g, " ")} — you {pct(h.focal.rate)} vs them {pct(h.competitor.rate)}{h.competitor.n < 10 || h.focal.n < 10 ? ` (n=${h.focal.n}/${h.competitor.n})` : ""}
              </span>
            ))}
          </div>
        )}
        {(b.unopposed || []).length > 0 && (
          <div className="row" style={{ fontSize: 12 }}>
            <b data-tip="triggers where the focal side has certification content but the competitor has fewer than 3 pages AT ALL — there is no rate to compare; the ground is simply uncontested">Uncontested ground (no competing content):</b>{" "}
            {b.unopposed.map((h) => (
              <span key={h.cep} className="atag" style={{ color: "var(--accent)", borderColor: "var(--accent)" }}
                    data-tip={cepTip(msgIdx, h.cep, `${focal}: ${pct(h.focal.rate)} record rate (n=${h.focal.n}); competitor has only ${h.competitor.n} page(s) on this trigger — no rate comparison possible`)}>
                {h.cep.replace(/_/g, " ")} — you: {pct(h.focal.rate)} record rate; them: {h.competitor.n} page{h.competitor.n === 1 ? "" : "s"}
              </span>
            ))}
          </div>
        )}
        {(b.guardrail || []).length > 0 && (
          <div className="row" style={{ fontSize: 12 }}>
            <b>Guardrail (do NOT attack):</b>{" "}
            {b.guardrail.map((h) => (
              <span key={h.cep} className="atag" style={{ color: "var(--muted)", borderColor: "var(--border)" }}
                    data-tip={`no record edge: ${comp} ${pct(h.competitor.rate)} vs ${focal} ${pct(h.focal.rate)}`}>
                {h.cep.replace(/_/g, " ")} — no edge
              </span>
            ))}
          </div>
        )}
        <div className="scaction">→ {b.action}</div>
      </div>
    </>
  );
}

// [DEFEND] quantified-outcome rate per owned trigger — voice vs proof inversion.
function ProofVsVoice({ pkg, msgIdx }) {
  const b = (pkg.insight_graphics || {}).proof_vs_voice;
  if (!b) return null;
  const comp = pkg.companies?.[0]?.canonical_name || "Competitor";
  const focal = pkg.companies?.[1]?.canonical_name || "Rippling";
  return (
    <>
      <InsightHeader block={b} n={2} />
      <div className="card">
        {b.rows.map((r) => (
          <Dumbbell key={r.cep}
                    tip={cepTip(msgIdx, r.cep)}
                    thin={r.competitor.n < 10 || r.focal.n < 10 ? `n=${r.competitor.n}/${r.focal.n}` : null}
                    label={`${r.cep.replace(/_/g, " ")} (${String(r.ownership).replace(/_/g, " ")})`}
                    aPct={r.focal.rate * 100} bPct={r.competitor.rate * 100}
                    aLabel={`${focal} ${r.focal.quant_n}/${r.focal.n} = ${pct(r.focal.rate)}`}
                    bLabel={`${comp} ${r.competitor.quant_n}/${r.competitor.n} = ${pct(r.competitor.rate)}`}
                    aColor="--accent" bColor="--bad"
                    note={r.competitor.n < 15 || r.focal.n < 15 ? "small cell — n shown" : undefined} />
        ))}
        <Dumbbell label="overall corpus"
                  aPct={b.overall.focal.rate * 100} bPct={b.overall.competitor.rate * 100}
                  aLabel={`${focal} ${b.overall.focal.quant_n}/${b.overall.focal.n} = ${pct(b.overall.focal.rate)}`}
                  bLabel={`${comp} ${b.overall.competitor.quant_n}/${b.overall.competitor.n} = ${pct(b.overall.competitor.rate)}`}
                  aColor="--accent" bColor="--bad" />
        <div className="row" style={{ fontSize: 12, color: "var(--muted)" }}>
          Naming war is one-way: {comp} names {focal} on {b.naming.competitor_names_focal} pages; {focal} names {comp} on {b.naming.focal_names_competitor}.
        </div>
        <div className="scaction">→ {b.action}</div>
      </div>
    </>
  );
}

// [INTERCEPT] decision-stage voids per vertical.
function FunnelVoids({ pkg, msgIdx }) {
  const b = (pkg.insight_graphics || {}).funnel_voids;
  if (!b) return null;
  const comp = pkg.companies?.[0]?.canonical_name || "Competitor";
  const focal = pkg.companies?.[1]?.canonical_name || "Rippling";
  return (
    <>
      <InsightHeader block={b} n={3} />
      <div className="card">
        {b.rows.slice(0, 8).map((r) => {
          const c = r.competitor, f = r.focal;
          const cRate = c.decision_n / Math.max(1, c.n);
          const fRate = f ? f.decision_n / Math.max(1, f.n) : null;
          return (
            <div className="ktrow" key={r.vertical}>
              <div className="ktlabel" data-tip={verticalTip(msgIdx, r.vertical, `evaluation depth: ${comp} ${c.evaluation_n}/${c.n}`)}>
                {r.vertical.replace(/_/g, " ")} {r.void && <span className="atag" style={{ color: "var(--good)", borderColor: "var(--good)" }}>VOID</span>}
              </div>
              <div className="ktbars" data-tip={`${comp}: ${c.decision_n}/${c.n} decision-stage pages`}>
                <div className="ktbar comp" style={{ width: `${cRate * 400}%`, maxWidth: "100%" }} />
                <span className="ktnum">{c.decision_n}/{c.n}</span>
              </div>
              <div className="ktbars" data-tip={f ? `${focal}: ${f.decision_n}/${f.n} decision-stage pages` : "no focal mirror"}>
                {f ? (<><div className="ktbar focal" style={{ width: `${(fRate || 0) * 400}%`, maxWidth: "100%" }} /><span className="ktnum">{f.decision_n}/{f.n}</span></>) : <span className="ktnum">—</span>}
              </div>
            </div>
          );
        })}
        <div className="ktlegend">
          <span><span className="ktbar comp" style={{ width: 14, display: "inline-block", height: 8 }} /> {comp} decision assets</span>{" "}
          <span><span className="ktbar focal" style={{ width: 14, display: "inline-block", height: 8 }} /> {focal}</span>
          <span style={{ color: "var(--muted)" }}> · VOID = they walk buyers to evaluation there, then strand them</span>
        </div>
        <div className="scaction">→ {b.action}</div>
      </div>
    </>
  );
}

// [SEO/CONQUEST] audience affinity vs the comparison-page census.
function AffinityDefense({ pkg }) {
  const b = (pkg.insight_graphics || {}).affinity_defense;
  if (!b) return null;
  const comp = pkg.companies?.[0]?.canonical_name || "Competitor";
  const max = Math.max(0.01, ...b.rows.map((r) => r.affinity));
  return (
    <>
      <InsightHeader block={b} n={3} />
      <div className="card">
        {b.rows.map((r) => (
          <div className="ktrow" key={r.domain} style={{ gridTemplateColumns: "150px 1fr auto" }}>
            <div className="ktlabel" data-tip={`${r.mentions} mention(s) in ${comp}'s classified corpus`}>{r.domain}</div>
            <div className="ktbars">
              <div className="ktbar" style={{ width: `${(r.affinity / max) * 100}%`, background: r.defended ? "var(--accent)" : "var(--muted)", opacity: r.defended ? 0.9 : 0.55 }} />
              <span className="ktnum">{r.affinity.toFixed(2)}</span>
            </div>
            <span className="atag" style={r.defended
              ? { color: "var(--accent)", borderColor: "var(--accent)" }
              : { color: "var(--good)", borderColor: "var(--good)" }}
              data-tip={r.defended ? `${comp} has a vs-page for this domain` : "no comparison page — this SERP is open"}>
              {r.defended ? "DEFENDED" : "OPEN SERP"}
            </span>
          </div>
        ))}
        {(b.orphan_comparison_slugs || []).length > 0 && (
          <div className="row" style={{ fontSize: 12, color: "var(--muted)" }}>
            vs-pages spent outside the top-affinity audience: {b.orphan_comparison_slugs.join(", ")}
          </div>
        )}
        <div className="row" style={{ fontSize: 11, color: "var(--muted)" }}>
          affinity = estimated audience-overlap index (0–1, top-normalized), not lost-deal share
        </div>
        <div className="scaction">→ {b.action}</div>
      </div>
    </>
  );
}

// [WHITESPACE] what the feed shows that the indexed site doesn't.
function ChannelProofSplit({ pkg }) {
  const b = (pkg.insight_graphics || {}).channel_proof_split;
  if (!b) return null;
  const comp = pkg.companies?.[0]?.canonical_name || "Competitor";
  const c = b.competitor;
  const rows = [
    { label: `${comp} product demos`, li: c.demo_linkedin / Math.max(1, c.linkedin_n), web: c.demo_web / Math.max(1, c.web_n), liN: `${c.demo_linkedin}/${c.linkedin_n}`, webN: `${c.demo_web}/${c.web_n}` },
    { label: `${comp} quantified outcomes`, li: c.quant_linkedin / Math.max(1, c.linkedin_n), web: c.quant_web / Math.max(1, c.web_n), liN: `${c.quant_linkedin}/${c.linkedin_n}`, webN: `${c.quant_web}/${c.web_n}` },
  ];
  return (
    <>
      <InsightHeader block={b} n={1} />
      <div className="card">
        <div className="ktrow" style={{ fontWeight: 600, fontSize: 11, color: "var(--muted)" }}>
          <div className="ktlabel">proof type</div><div>LinkedIn feed</div><div>indexed website</div>
        </div>
        {rows.map((r) => (
          <div className="ktrow" key={r.label}>
            <div className="ktlabel">{r.label}</div>
            <div className="ktbars" data-tip={`${r.liN} of LinkedIn posts`}>
              <div className="ktbar comp" style={{ width: `${r.li * 100}%` }} /><span className="ktnum">{r.liN} = {pct(r.li)}</span>
            </div>
            <div className="ktbars" data-tip={`${r.webN} of website pages`}>
              <div className="ktbar focal" style={{ width: `${r.web * 100}%` }} /><span className="ktnum">{r.webN} = {pct(r.web)}</span>
            </div>
          </div>
        ))}
        <div className="row" style={{ fontSize: 12 }}>
          <span className="atag" style={{ color: "var(--warn)", borderColor: "var(--warn)" }}>
            {c.no_public_pricing_web}/{c.web_n} pages hide pricing
          </span>
          <span className="atag" style={{ color: "var(--warn)", borderColor: "var(--warn)" }}>
            {c.no_cta_web}/{c.web_n} pages have no CTA
          </span>
        </div>
        <div className="scaction">→ {b.action}</div>
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
      <Sec n={3} title={`${competitor} LinkedIn employee posts (showing ${Math.min(20, posts.length)} of ${posts.length})`}
           why="The individual posts, each classified for theme, stance, and product vertical — click through to verify any of them on LinkedIn."
           tip="Exa-extracted post text + real post link; login-wall boilerplate is stripped from excerpts." />
      <div className="card">
        {posts.slice(0, 20).map((p) => (
          <div className="gaprow" key={p.artifact_id} style={{ gridTemplateColumns: "1fr" }}>
            <div className="row">
              <b>{p.author || "?"}</b>{p.author_role ? ` · ${p.author_role}` : ""}{" "}
              <span className="pill" data-tip="The one theme this post pushes hardest — feed themes that outrun the website are early strategy signals">{p.theme || "—"}</span>
              <span className="pill" data-tip="How openly this post attacks: ignores → implicit contrast → named comparison → direct attack">{p.competitive_stance || "—"}</span>
              {(p.verticals || []).map((v) => (
                <span className="pill vert" key={v} data-tip="product vertical this post touches">{v.replace(/_/g, " ")}</span>
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

function PersonaChannelHeatmap({ pkg, msgIdx, n = 4 }) {
  const m = pkg.persona_channel_matrix || {};
  if (!m.personas?.length) return null;
  return (
    <>
      <Sec n={n} title="Persona × channel coverage"
           why="Who they talk to, where. Empty cells are audiences no channel is serving — content whitespace you can own first."
           tip="Cell intensity = number of classified artifacts; an empty cell means not observed, not proof of absence." />
      <div className="card"><Heatmap personas={m.personas} channels={m.channels} cells={m.cells || {}}
        rowTip={(p) => personaTip(msgIdx, p, "how they talk to this buyer — the actual wording")} /></div>
    </>
  );
}

/* ------------------------- strategy changes tab ------------------------ */

function StrategyOverTime({ pkg, srcIdx }) {
  const all = pkg.change_events || [];
  const repositionedFirst = [...all].sort((a, b) =>
    (a.lifecycle === "repositioned" ? 0 : 1) - (b.lifecycle === "repositioned" ? 0 : 1));
  const changes = repositionedFirst;
  const artIdx = artifactIndex(pkg);
  const evIdx = evidenceIndex(pkg);
  return (
    <>
      <Sec n={3} title={`Change events, with evidence (${changes.length})`}
           why="The full story per change: prior state, current state, honest alternative explanations, and the exact evidence pages. Read the alternatives before acting — archive absence is not real-world absence."
           tip="Events are reconciled against the full corpus; emerging/expanding signals stay low-confidence by design." />
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
        // For window_sample events the prior ids are the sample the theme was
        // ABSENT from — mixing them into one drawer made absence-sample pages
        // indistinguishable from current evidence (verifier). Current-only
        // drawer; the evidence line above discloses the absence sample.
        const drawerIds = c.prior_evidence_role === "window_sample"
          ? (c.current_evidence_ids || [])
          : [...(c.prior_evidence_ids || []), ...(c.current_evidence_ids || [])];
        const exact = resolveIds(drawerIds, artIdx, evIdx);
        return (
          <div className="card" key={c.change_id}>
            <div className="title">
              {c.dimension} <span data-tip="confidence — low means treat as a signal, not a fact">{pill(c.confidence)}</span>{" "}
              <span className="pill" data-tip="emerging = absent from the prior window; expanding = present before, more artifacts now; repositioned = replaced a prior dominant message">{c.lifecycle}</span>
            </div>
            <div className="row"><b>Prior:</b> {c.prior_state}</div>
            <div className="row"><b>Current:</b> {c.current_state}</div>
            <div className="row" style={{ color: "var(--muted)", fontSize: 12 }}>
              <b>Evidence:</b> {evidenceLine}
            </div>
            {c.alternative_explanations?.length > 0 && (
              <div className="row" data-tip="honest alternative readings of this signal"><b>Alt. explanations:</b> {c.alternative_explanations.join("; ")}</div>
            )}
            {exact.length > 0
              ? <SourceDrawer sources={exact} label={c.prior_evidence_role === "window_sample" ? `current evidence pages (${exact.length})` : `exact evidence pages (${exact.length})`} />
              : theme && <SourceDrawer sources={srcIdx[theme]} label={`sources for “${theme}”`} />}
          </div>
        );
      })}
    </>
  );
}

/* ----------------------- performance marketing tab --------------------- */

const CEP_PLACEHOLDER = /^(not[_ ]observed|\(?unspecified\)?|unknown|none|n\/?a|unclassified)/i;
const CEP_GROUPS = ["competitor_advantage", "contested", "focal_owns", "insufficient_sample", "not_compared", "neither"];

function CepRow({ c, competitor, focal, artIdx, msgIdx }) {
  const [open, setOpen] = useState(false);
  const own = { focal_owns: "--good", contested: "--warn", competitor_advantage: "--bad", insufficient_sample: "--border", not_compared: "--border", neither: "--border" };
  const cs = c.competitor_share != null ? c.competitor_share : null;
  const fs = c.focal_share != null ? c.focal_share : null;
  const clabel = cs != null ? `${c.competitor_pages} (${Math.round(cs * 100)}%)` : String(c.competitor_pages);
  // focal_pages null = no focal mirror collected — never a fabricated 0.
  const flabel = c.focal_pages == null ? "—" : fs != null ? `${c.focal_pages} (${Math.round(fs * 100)}%)` : String(c.focal_pages);
  const pages = resolveIds(
    [...(c.competitor_example_artifact_ids || []), ...(c.focal_example_artifact_ids || [])],
    artIdx, {}
  );
  return (
    <div className="gaprow" style={{ gridTemplateColumns: "220px 1fr" }}>
      <div>
        <div className="gaplabel" style={{ fontSize: 12 }} data-tip={cepTip(msgIdx, c.cep, c.ownership_basis)}>{String(c.cep).replace(/_/g, " ").slice(0, 60)}</div>
        <span className="pill" style={{ color: `var(${own[c.ownership] || "--border"})`, borderColor: `var(${own[c.ownership] || "--border"})` }}
              data-tip={c.ownership_basis || "who currently owns this buying intent"}>{String(c.ownership).replace(/_/g, " ")}</span>
      </div>
      <div className="gapbars" data-tip={c.ownership_basis || `${competitor}: ${clabel} · ${focal}: ${flabel}`}>
        <HBar data={[
          { label: competitor, value: cs != null ? cs : c.competitor_pages, display: clabel },
          { label: focal, value: fs != null ? fs : c.focal_pages, display: flabel },
        ]} />
        {pages.length > 0 && (
          <>
            <button type="button" className="srcbtn" onClick={() => setOpen((o) => !o)}
                    data-tip="The pages that carry this buying trigger (up to 5 competitor + 3 focal examples)">
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

function CepOwnership({ pkg, msgIdx }) {
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
      <Sec n={1} title="Search-intent ownership"
           why={`The buying triggers ("opening a new country", "consolidating HR tools") and who owns the content for each. Target the contested group; defend what you own; ignore what's too thin to call. All ${ceps.length} triggers shown, grouped by verdict.`}
           tip="Ownership is SHARE-normalized (pages ÷ each company's classified corpus): contested needs <2x share ratio, an ownership call needs ≥2x AND ≥3 pages; thinner reads say 'insufficient sample' instead of asserting." />
      <div className="card">
        {groups.map(([g, rows]) => (
          <div key={g}>
            <div className="cepgroup" data-tip={
              g === "insufficient_sample"
                ? "too few pages on either side to call ownership — disclosed, not asserted"
                : `${rows.length} trigger(s) where the share test says: ${g.replace(/_/g, " ")}`
            }>
              {String(g).replace(/_/g, " ")} ({rows.length})
            </div>
            {rows.map((c) => <CepRow key={c.cep} c={c} competitor={competitor} focal={focal} artIdx={artIdx} msgIdx={msgIdx} />)}
          </div>
        ))}
        {legacy.map((c) => <CepRow key={c.cep} c={c} competitor={competitor} focal={focal} artIdx={artIdx} msgIdx={msgIdx} />)}
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
      <Sec n={4} title={`Traffic estimates (${label})`}
           why="The demand behind the content: estimated visits and where their traffic comes from — sizing context for every play on this tab."
           tip="Estimated via Exa's Similarweb partner (or a labeled public-web estimate). Every value is an estimate — never measured analytics." />
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
      <Sec n={5} title="Commercial motion"
           why="How they sell, read from public signals: demo-led vs self-serve CTAs, how openly they price, which segments they chase. Mismatches with your motion are positioning openings."
           tip="Pricing disclosure = the most-open level observed on ≥2 pages (noise-guarded). Never CAC/conversion/spend — those aren't publicly knowable." />
      <div className="card">
        <div className="row"><b>Primary motion:</b> {cm.primary_motion} <span className="pill">{cm.confidence}</span></div>
        <div className="row">
          <b data-tip="The full observed mix, not just the most-open level — a single label can contradict the Changes tab when disclosure is shifting">Pricing disclosure:</b>{" "}
          {cm.pricing_disclosure_mix && Object.keys(cm.pricing_disclosure_mix).length > 1
            ? `mixed — ${Object.entries(cm.pricing_disclosure_mix).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k.replace(/_/g, " ")} on ${v}`).join(", ")} (most-open: ${cm.pricing_disclosure})`
            : cm.pricing_disclosure}
        </div>
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
      <Sec n={4} title={`Evidence explorer (${arts.length} artifacts)`}
           why="Every source this run collected — URL, timestamp, extracted text. Everything above traces back to a row here."
           tip="Wayback rows carry their real archive capture dates; normalized text is truncated for size (full text in the run store)." />
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
      <Sec n={1} title="Coverage & limitations"
           why="How much ground this run actually covered, dimension by dimension — read this before trusting any verdict. 'Not attempted' means we never looked, not that nothing exists."
           tip="Coverage levels are recomputed from the collected corpus at render; every limitation is disclosed, never hidden." />
      <div className="cov">
        {entries.map(([k, v]) => (
          <div className={`cell ${v === "not_attempted" ? "na" : ""}`} key={k}
               data-tip={v === "not_attempted"
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
      <Sec n={3} title="Data honesty — disclosed absences"
           why="What we tried and found nothing, what failed, and what we excluded — so you know the analysis isn't hiding its misses. 'Searched and found nothing' is itself a finding."
           tip="Annotations mark negatives superseded by later successful calls; excluded junk is counted and disclosed." />
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
      <Sec n={2} title={`Claims ledger (${claims.length})`}
           why="The auditable spine: every strategic claim with why we believe it, what would change our mind, and the verbatim evidence behind it. Expand any claim to check it yourself."
           tip="Claims are judged against their cited evidence; performance metrics (ROAS/CAC/spend) are banned — never estimated." />
      <div className="card">
        {claims.map((c) => {
          const support = resolveIds(c.evidence_ids || [], artIdx, evIdx);
          const contra = resolveIds(c.contradicting_evidence_ids || [], artIdx, evIdx);
          return (
            <details key={c.claim_id}>
              <summary>
                <span className={`pill ${c.claim_confidence}`}>{c.claim_confidence}</span>{" "}
                <span className="pill" data-tip="observed = directly quoted from a source; supported_inference = follows from the evidence but not stated verbatim; hypothesis = plausible — act only after verification">{c.status}</span>{" "}
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
  const lastUrl = React.useRef(null);
  useEffect(() => {
    // Clear stale data when the URL actually changes (switching runs must
    // never keep showing the previous run's package) — but NOT on a refresh
    // bump of the same URL, which would flash the whole page empty.
    if (lastUrl.current !== url) {
      lastUrl.current = url;
      setData(null);
      setErr(null);
    }
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

// One button runs EVERYTHING (comparative mode already includes the current
// snapshot, the time comparison, the focal mirror, LinkedIn, and traffic) —
// the old mode dropdown just made people choose between subsets.
const LOOKBACK_CHOICES = [
  [180, "6 months of history"],
  [365, "1 year of history"],
  [730, "2 years of history"],
];
const CURRENT_CHOICES = [
  [30, "last 30 days"],
  [60, "last 60 days"],
  [90, "last 90 days"],
];

function NewRunForm({ onSubmit, focalDefault }) {
  const [company, setCompany] = useState("");
  const [compareTo, setCompareTo] = useState("");
  const [exec, setExec] = useState("live"); // live by default — real data
  const [lookback, setLookback] = useState(365);
  const [currentDays, setCurrentDays] = useState(90);
  const [includeLinkedin, setIncludeLinkedin] = useState(true);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    if (!company.trim()) return;
    setBusy(true);
    await onSubmit({
      company: company.trim(),
      compare_to: compareTo.trim() || null,
      mode: "comparative", // the full analysis — subsumes snapshot + longitudinal
      execution_mode: exec,
      lookback_days: lookback,
      current_days: currentDays,
      include_linkedin: includeLinkedin,
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
      <button className="nr-btn" disabled={busy || !company.trim()}>
        {busy ? "Starting…" : "Run full analysis (a few min)"}
      </button>
      <div className="nr-help">
        Runs everything in one go: current positioning, changes over time, the
        comparison vs {compareTo.trim() || focalDefault || "rippling.com"}, LinkedIn posts, and traffic.
      </div>
      <details className="nr-adv">
        <summary>Advanced options</summary>
        <div className="nr-field">
          <label data-tip="Which company to benchmark the competitor against — every ownership and proof verdict is relative to this company">Compare against</label>
          <input className="nr-in" placeholder={`default: ${focalDefault || "rippling.com"}`}
                 value={compareTo} onChange={(e) => setCompareTo(e.target.value)} />
        </div>
        <div className="nr-field">
          <label data-tip="How far back the 'before' period reaches — archived pages, old posts, and news inside this range become the prior window">History to compare against</label>
          <select className="nr-sel" value={lookback} onChange={(e) => setLookback(Number(e.target.value))}>
            {LOOKBACK_CHOICES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
        <div className="nr-field">
          <label data-tip="How many trailing days count as 'now' — everything newer than this is the current window, everything older (within the history range) is the prior window">Recent window</label>
          <select className="nr-sel" value={currentDays} onChange={(e) => setCurrentDays(Number(e.target.value))}>
            {CURRENT_CHOICES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
        <div className="nr-field">
          <label data-tip="Individual employee/company posts collected via the Exa Agent and classified one by one. Uses Exa credits — untick to save them on runs where the feed doesn't matter." style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
            <input type="checkbox" checked={includeLinkedin} onChange={(e) => setIncludeLinkedin(e.target.checked)} style={{ margin: 0 }} />
            Collect LinkedIn posts (uses Exa credits)
          </label>
        </div>
        <div className="nr-field">
          <label data-tip="live fetches real public data; fixture is a deterministic synthetic test run">Data source</label>
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
      </details>
    </form>
  );
}

function JobsList({ jobs, onSelect }) {
  if (!jobs || jobs.length === 0) return null;
  const active = jobs.filter((j) => j.status === "pending" || j.status === "running");
  const recent = jobs.filter((j) => j.status === "error").slice(0, 3);
  if (active.length === 0 && recent.length === 0) return null;
  return (
    <div className="jobs">
      {active.map((j) => (
        <div
          className={`jobrow ${j.run_id ? "clickable" : ""}`}
          key={j.job_id}
          onClick={() => j.run_id && onSelect && onSelect(j.run_id)}
          data-tip={j.run_id ? "Click to watch this run live — pages, sources, and themes stream in as it works" : undefined}
        >
          <span className="spinner" /> {j.company}{j.compare_to ? ` vs ${j.compare_to}` : ""}
          <span className="jobmeta">
            {j.phase || `${j.execution_mode} · ${j.status}`}
            {j.classified != null && j.artifacts != null ? ` · ${j.classified}/${j.artifacts} pages classified` : ""}
          </span>
        </div>
      ))}
      {recent.map((j) => (
        <div className="jobrow err" key={j.job_id}>✕ {j.company} — {String(j.error).slice(0, 60)}</div>
      ))}
    </div>
  );
}

/* ------------------------------ live run view ---------------------------- */

const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u || ""; } };

// Streaming progress panel for an in-flight (or interrupted) run. Polls the
// cheap /live snapshot every 4s — pure DB reads server-side, no model calls —
// so the corpus, source mix, and early themes visibly GROW while the agent
// works. Auto-hands off to the full dashboard when the report lands.
function LiveRunView({ runId, onReady, onJobStarted, onDismissed }) {
  const [live, setLive] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const readyFired = React.useRef(false);
  useEffect(() => {
    readyFired.current = false;
    let stop = false;
    const load = () =>
      fetch(`/api/runs/${runId}/live`)
        .then((r) => (r.ok ? r.json() : Promise.reject(r.statusText)))
        .then((d) => {
          if (stop) return;
          setLive(d); setErr(null);
          if (d.report_ready && !readyFired.current) { readyFired.current = true; onReady && onReady(); }
        })
        .catch((e) => { if (!stop) setErr(String(e)); });
    load();
    const t = setInterval(load, 4000);
    return () => { stop = true; clearInterval(t); };
  }, [runId]);

  const resume = async () => {
    setBusy(true);
    try {
      const res = await fetch(`/api/runs/${runId}/resume`, { method: "POST" });
      if (res.ok) {
        const job = await res.json();
        onJobStarted && onJobStarted(job);
        setLive((l) => (l ? { ...l, status: "running" } : l));
      } else {
        const e = await res.json().catch(() => ({}));
        alert("Could not resume: " + (e.detail || res.statusText));
      }
    } catch { alert("Could not reach the API — is `make api` running?"); }
    setBusy(false);
  };
  const dismiss = async () => {
    if (!window.confirm("Remove this run from the queue? Collected data is kept, but the run won't be resumed.")) return;
    setBusy(true);
    try {
      const res = await fetch(`/api/runs/${runId}/dismiss`, { method: "POST" });
      if (res.ok) { onDismissed && onDismissed(); }
      else { const e = await res.json().catch(() => ({})); alert("Could not dismiss: " + (e.detail || res.statusText)); }
    } catch { alert("Could not reach the API — is `make api` running?"); }
    setBusy(false);
  };

  if (err && !live) return <p className="empty">Could not load live progress ({err}) — is `make api` running?</p>;
  if (!live) return <p className="empty">Connecting to the run…</p>;

  const c = live.counts || {};
  const spend = live.spend || {};
  const spent = (Number(spend.tool_usd) || 0) + (Number(spend.model_usd) || 0);
  const running = live.status === "running";
  const maxSrc = Math.max(1, ...(live.source_mix || []).map((s) => s.n));
  const maxTheme = Math.max(1, ...(live.top_themes || []).map((t) => t.n));
  return (
    <>
      <h1>
        {live.company} {live.compare_to ? `vs ${live.compare_to}` : ""}{" "}
        <span className={`badge ${live.execution_mode}`}>{live.execution_mode}</span>
        <span className={`badge lv-${live.status}`}>
          {running ? "in progress" : live.status === "needs_input" ? "needs input" : live.status === "complete" ? "finishing up" : "interrupted"}
        </span>
      </h1>

      {live.status === "interrupted" && (
        <div className="banner warn" role="note">
          This run's worker stopped (most likely a server restart) — all progress below is saved.
          Resume it from the last checkpoint; nothing already collected is re-fetched or re-paid for.
          <span style={{ marginLeft: 10 }}>
            <button className="lvbtn" disabled={busy} onClick={resume}>{busy ? "…" : "Resume run"}</button>
            <button className="lvbtn ghost" disabled={busy} onClick={dismiss}>Dismiss</button>
          </span>
        </div>
      )}
      {live.status === "needs_input" && (
        <div className="banner warn" role="note">
          The agent paused with a question{live.pending_question ? `: “${live.pending_question}”` : "."}{" "}
          Resuming continues with the company name as given.
          <span style={{ marginLeft: 10 }}>
            <button className="lvbtn" disabled={busy} onClick={resume}>{busy ? "…" : "Continue run"}</button>
            <button className="lvbtn ghost" disabled={busy} onClick={dismiss}>Dismiss</button>
          </span>
        </div>
      )}

      <div className="card liveview">
        <div className="title">
          {running && <span className="livedot running" />}
          {live.status === "complete" ? "Analysis finished — writing the report" : live.phase}
          {live.iteration != null && <span className="lvcycle"> · research cycle {live.iteration}</span>}
          <Info tip="What the agent is doing right now, from its latest checkpoint. This page refreshes itself every few seconds — safe to leave and come back, the run keeps going server-side even if you close the tab." />
        </div>
        <div className="lvpills">
          <span className="lvpill" data-tip="Pages, posts, and records collected so far — every one becomes classified evidence or is disclosed as excluded">{c.artifacts ?? 0} sources collected</span>
          <span className="lvpill" data-tip="Sources fully classified so far (themes, personas, funnel stage, proof, buying triggers)">{c.classified ?? 0} classified</span>
          <span className="lvpill" data-tip="Verbatim quotes extracted and containment-verified against their source pages">{c.evidence_quotes ?? 0} verified quotes</span>
          {spend.budget_usd != null && (
            <span className="lvpill" data-tip="Model + tool spend so far vs this run's research budget — the run stops for an explainable reason, budget included">
              ${spent.toFixed(2)} of ${Number(spend.budget_usd).toFixed(2)} budget
            </span>
          )}
        </div>
        {live.mirror && (
          <div className="lvmirror" data-tip="The focal company runs through the SAME pipeline in an isolated run, so every 'them vs us' number uses symmetric methodology">
            ↳ Focal mirror ({live.mirror.company}): {live.mirror.phase} — {live.mirror.counts?.artifacts ?? 0} sources, {live.mirror.counts?.classified ?? 0} classified
          </div>
        )}
      </div>

      <div className="lvgrid">
        <div className="card">
          <div className="title">Coming in now <Info tip="The most recently collected sources — newest first. Each will be individually classified and quote-verified." /></div>
          {(live.latest_artifacts || []).length === 0 && <p className="empty">No sources yet — collection starts after planning.</p>}
          {(live.latest_artifacts || []).map((a, i) => (
            <div className="lvfeedrow" key={i}>
              <span className={`lvsrc ${a.source_type}`}>{a.source_type}</span>
              <a href={a.url} target="_blank" rel="noreferrer" className="lvfeedtitle" data-tip={a.url}>
                {a.title || hostOf(a.url) || "untitled"}
              </a>
            </div>
          ))}
        </div>
        <div className="card">
          <div className="title">Source mix so far <Info tip="Where the corpus is coming from. A healthy run mixes current website pages with archived history, news, and posts — imbalances are disclosed in the final report." /></div>
          {(live.source_mix || []).map((s) => (
            <div className="lvbarrow" key={s.source}>
              <span className="lvbarlabel">{s.source}</span>
              <span className="lvbartrack"><span className="lvbarfill" style={{ width: `${(s.n / maxSrc) * 100}%` }} /></span>
              <span className="lvbarn">{s.n}</span>
            </div>
          ))}
          {(live.source_mix || []).length === 0 && <p className="empty">Nothing collected yet.</p>}
        </div>
        <div className="card">
          <div className="title">Early themes <Info tip="Live counts of each page's primary message theme, straight from the classifier. An early read — the final report normalizes these for corpus size and reconciles them against the focal company before any verdict." /></div>
          {(live.top_themes || []).map((t) => (
            <div className="lvbarrow" key={t.theme}>
              <span className="lvbarlabel">{String(t.theme).replace(/_/g, " ")}</span>
              <span className="lvbartrack"><span className="lvbarfill theme" style={{ width: `${(t.n / maxTheme) * 100}%` }} /></span>
              <span className="lvbarn">{t.n}</span>
            </div>
          ))}
          {(live.top_themes || []).length === 0 && <p className="empty">Themes appear once classification starts.</p>}
        </div>
        <div className="card">
          <div className="title">Recent activity <Info tip="The agent's own decision trace, humanized — every step, alternative, failure, and skip is recorded in trace.jsonl" /></div>
          {(live.recent_activity || []).map((ev, i) => (
            <div className="lvactrow" key={i}>
              <span className="lvts">{String(ev.ts).slice(11, 19)}</span> {ev.text}
            </div>
          ))}
          {(live.recent_activity || []).length === 0 && <p className="empty">Waiting for the first trace events…</p>}
        </div>
      </div>
      <p className="empty" style={{ fontSize: 12 }}>
        Live numbers are checkpoint-fresh, not final — the report recomputes everything deterministically
        when the run finishes, and this page switches to it automatically.
      </p>
    </>
  );
}

/* --------------------------------- app --------------------------------- */

const TABS = [
  { id: "overview", label: "Overview — start here", tip: "The scorecard, the top plays, and a chat grounded in this run's evidence" },
  { id: "product", label: "Where to win", tip: "Their claims vs their proof — the attack/defend map, the gaps, and the runnable plays" },
  { id: "linkedin", label: "LinkedIn signals", tip: "What their people amplify in the feed — often before the website catches up" },
  { id: "changes", label: "Changes over time", tip: "Did their strategy actually move? Reconciled changes with customizable windows" },
  { id: "performance", label: "Demand & channels", tip: "Which buying intents each side owns, plus traffic and how they sell" },
  { id: "sources", label: "Evidence & trust", tip: "Coverage, the claim ledger, disclosed absences, and every raw source" },
];

export default function App() {
  const [refresh, setRefresh] = useState(0);
  // Separate refresh counter for the (potentially large) package fetch — it
  // must NOT re-download on every 3s jobs tick, only when a report lands.
  const [pkgRefresh, setPkgRefresh] = useState(0);
  const [runs] = useJson("/api/runs", refresh);
  const [selected, setSelected] = useState(null);
  const [pkg] = useJson(selected ? `/api/runs/${selected}` : null, pkgRefresh);
  const [jobs, setJobs] = useState([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [tab, setTab] = useState("overview");
  // Custom-window overlay for the Changes-over-time tab (exploratory; the
  // saved report always keeps the run's original windows).
  const [winOverlay, setWinOverlay] = useState(null);

  useEffect(() => {
    if (runs && runs.length && !selected) setSelected(runs[0].run_id);
  }, [runs, selected]);
  useEffect(() => { setTab("overview"); setWinOverlay(null); }, [selected]);

  // Seed jobs on mount — without this, a page refresh mid-run showed nothing
  // until the user started ANOTHER run (the poller only armed on submit).
  useEffect(() => {
    fetch("/api/jobs")
      .then((r) => (r.ok ? r.json() : []))
      .then((js) => { if (Array.isArray(js) && js.length) setJobs(js); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const active =
      jobs.some((j) => j.status === "pending" || j.status === "running") ||
      (runs || []).some((r) => r.in_progress && r.live_status === "running");
    if (!active) return;
    const t = setInterval(() => {
      fetch("/api/jobs")
        .then((r) => r.json())
        .then((js) => { setJobs(js); setRefresh((n) => n + 1); })
        .catch(() => {});
    }, 3000);
    return () => clearInterval(t);
  }, [jobs, runs]);

  // After submitting a run, jump to its live view as soon as the run_id
  // lands in the job (one poll tick) — the user watches it start immediately.
  const followJob = React.useRef(null);
  useEffect(() => {
    if (!followJob.current) return;
    const j = jobs.find((x) => x.job_id === followJob.current);
    if (j?.run_id) {
      followJob.current = null;
      setSelected(j.run_id);
      setRefresh((n) => n + 1);
    }
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
        followJob.current = job.job_id;
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
  const msgIdx = pkg ? buildMessagingIndexes(pkg) : {};
  const selEntry = (runs || []).find((r) => r.run_id === selected);
  const showLive = !pkg && selEntry?.in_progress;

  return (
    <div className={`app ${menuOpen ? "menu-open" : ""}`}>
      <div className="topbar">
        <button className="hamburger" onClick={() => setMenuOpen((o) => !o)} aria-label="menu">☰</button>
        <span className="topbar-title">Competitive Intel</span>
      </div>
      <div className="sidebar">
        <h1>Competitive Intel</h1>
        <NewRunForm onSubmit={submitRun} focalDefault={focalDefault} />
        <JobsList jobs={jobs} onSelect={(rid) => { setSelected(rid); setMenuOpen(false); }} />
        <p className="meta" style={{ color: "var(--muted)", fontSize: 12, marginTop: 12 }}>Runs</p>
        {!runs && <p className="empty">Loading…</p>}
        {runs && runs.length === 0 && <p className="empty">No runs yet — add one above.</p>}
        {(runs || []).map((r) => r.in_progress ? (
          <div
            key={r.run_id}
            className={`runitem inprogress ${selected === r.run_id ? "active" : ""}`}
            onClick={() => { setSelected(r.run_id); setMenuOpen(false); }}
            data-tip={r.live_status === "running"
              ? "Running now — click to watch pages, sources, and themes stream in. Progress survives page refreshes and server restarts."
              : r.live_status === "needs_input"
                ? "The agent paused with a question — click to answer or resume"
                : "This run's worker stopped but its progress is saved — click to resume from the last checkpoint"}
          >
            <div className="co">
              <span className={`livedot ${r.live_status}`} />
              {r.company_input} {r.compare_to ? `vs ${r.compare_to}` : ""}
            </div>
            <div className="meta">
              <span className={`badge ${r.execution_mode}`}>{r.execution_mode}</span>
              <span className={`badge lv-${r.live_status}`}>
                {r.live_status === "running" ? "in progress" : r.live_status === "needs_input" ? "needs input" : "interrupted"}
              </span>
            </div>
            <div className="meta">{r.phase}{r.iteration != null ? ` · cycle ${r.iteration}` : ""}</div>
          </div>
        ) : (
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
        {showLive && (
          <LiveRunView
            key={selected}
            runId={selected}
            onReady={() => { setRefresh((n) => n + 1); setPkgRefresh((n) => n + 1); }}
            onJobStarted={(job) => { setJobs((prev) => [job, ...prev]); setRefresh((n) => n + 1); }}
            onDismissed={() => { setSelected(null); setRefresh((n) => n + 1); }}
          />
        )}
        {!pkg && !showLive && (
          <p className="empty">{selected ? "Loading the report…" : "Select a run, or add a new analysis from the panel."}</p>
        )}
        {pkg && (
          <>
            <h1>
              {pkg.scope?.company_input} {pkg.scope?.compare_to ? `vs ${pkg.scope.compare_to}` : ""}
              {" "}
              <span className={`badge ${pkg.run?.execution_mode}`}>{pkg.run?.execution_mode}</span>
              {(pkg.run?.stop_reason_label || pkg.run?.stop_reason) && (
                <span className="badge" data-tip={`raw stop reason: ${pkg.run?.stop_reason}`}>
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
                  data-tip={t.tip}
                  onClick={() => setTab(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {tab === "overview" && (
              <>
                <TabIntro q="What did we find, and what should Rippling do about it?"
                          why="Start with the scorecard (the whole analysis as actions), take the top plays, then ask the chat anything — it answers only from this run's collected evidence and cites its sources." />
                {pkg.bottom_line && (
                  <div className="card" style={{ borderLeft: "3px solid var(--good)" }}>
                    <div className="title">The bottom line <Info tip="Composed only from this run's verified numbers — the top-ranked play, the ownership split, and what's moving. No model prose; every clause traces to a chart below." /></div>
                    <div className="row" style={{ color: "var(--text)" }}>{pkg.bottom_line}</div>
                  </div>
                )}
                <StrategicScorecard pkg={pkg} go={setTab} msgIdx={msgIdx} />
                <TopActions pkg={pkg} onOpenBoard={() => setTab("product")} />
                <Positioning pkg={pkg} msgIdx={msgIdx} />
                <DataVisuals pkg={pkg} msgIdx={msgIdx} />
                {pkg.corpus_normalization && (
                  <p className="empty" style={{ fontSize: 12 }}
                     data-tip="the trust envelope: how much data this run rests on and what's missing — full detail on Evidence & trust">
                    Basis: {pkg.run?.execution_mode} run, {pkg.run?.stop_reason_label || ""} · {pkg.corpus_normalization.competitor?.n_classified} vs {pkg.corpus_normalization.focal?.n_classified} classified pages · {Object.values(pkg.coverage || {}).filter((v) => v === "not_attempted").length} dimensions not attempted — see Evidence & trust.
                  </p>
                )}
                <ChatPanel key={selected} runId={selected} pkg={pkg} />
              </>
            )}
            {tab === "product" && (
              <>
                <TabIntro q="Where can we beat them — and where should we not try?"
                          why="Follow the numbers: the map shows where their claims outrun their proof (1), the EDA joins turn that into specific openings (2–3), the gaps score every repeated claim (4), and the Action Board turns the best ones into runnable plays (5). Sections 6–8 are the reference detail behind them." />
                <AttackDefendMatrix pkg={pkg} msgIdx={msgIdx} />
                <ClaimVsRecord pkg={pkg} msgIdx={msgIdx} />
                <FunnelVoids pkg={pkg} msgIdx={msgIdx} />
                <GapsSection pkg={pkg} srcIdx={srcIdx} msgIdx={msgIdx} />
                <Opportunities pkg={pkg} srcIdx={srcIdx} />
                <KeyTopicsComparison pkg={pkg} msgIdx={msgIdx} srcIdx={srcIdx} />
                <VerticalThemeHeatmap pkg={pkg} msgIdx={msgIdx} />
                <VerticalAnalysis pkg={pkg} msgIdx={msgIdx} />
              </>
            )}
            {tab === "linkedin" && ((pkg.linkedin_posts || []).length > 0 || (pkg.insight_graphics || {}).channel_proof_split ? (
              <>
                <TabIntro q="What are their people saying that their website isn't?"
                          why="Employee posts are the leading indicator — launches, demos, and themes show up here first. Use this tab to catch the story early and to see which audiences their feed serves that yours doesn't." />
                <ChannelProofSplit pkg={pkg} />
                <LinkedInThemeBar pkg={pkg} msgIdx={msgIdx} />
                <LinkedInPosts pkg={pkg} />
                <PersonaChannelHeatmap pkg={pkg} msgIdx={msgIdx} n={4} />
              </>
            ) : (
              <>
                <TabIntro q="LinkedIn wasn't collected on this run"
                          why="Post collection failed or was turned off (see Evidence & trust → tool failures / data honesty for the exact reason). Re-run with 'Collect LinkedIn posts' enabled to fill this tab. The persona × channel view below still works from the website corpus." />
                <PersonaChannelHeatmap pkg={pkg} msgIdx={msgIdx} n={1} />
              </>
            ))}
            {tab === "changes" && (() => {
              // Shim the package with the custom-window recount when active —
              // the three components read only these two fields.
              const shim = winOverlay
                ? { ...pkg, temporal_baseline: winOverlay.temporal_baseline, change_events: winOverlay.change_events }
                : pkg;
              return (
                <>
                  <TabIntro q="Did their strategy actually change — and when?"
                            why="Every 'change' here survives a reconciliation against the full corpus: real archive/publish dates, both windows checked, honest caveats. Adjust the windows below to test how sensitive the story is to where you draw the line." />
                  <WindowPicker runId={selected} overlay={winOverlay} onOverlay={setWinOverlay} />
                  {winOverlay && (
                    <div className="banner" role="note">
                      Exploring custom windows ({String(winOverlay.time_windows[1].start_at).slice(0, 10)} →{" "}
                      {String(winOverlay.time_windows[0].end_at).slice(0, 10)}) — the saved report uses the run's
                      original windows.
                      {(winOverlay.reconciliation_notes || []).length > 0 &&
                        ` ${winOverlay.reconciliation_notes.length} event(s) re-reconciled under these windows.`}
                    </div>
                  )}
                  <TemporalBaseline pkg={shim} msgIdx={msgIdx} />
                  <ChangesTimeline pkg={shim} msgIdx={msgIdx} />
                  <StrategyOverTime pkg={shim} srcIdx={srcIdx} />
                </>
              );
            })()}
            {tab === "performance" && (
              <>
                <TabIntro q="Which buying moments do they own — and where is the demand?"
                          why="Work top to bottom: who owns each buying intent (1), whether that ownership is backed by proof or just page volume (2), which comparison SERPs are undefended (3), then the traffic and sales-motion context behind it all (4–5)." />
                <CepOwnership pkg={pkg} msgIdx={msgIdx} />
                <ProofVsVoice pkg={pkg} msgIdx={msgIdx} />
                {(pkg.insight_graphics || {}).affinity_defense
                  ? <AffinityDefense pkg={pkg} />
                  : <AffinityBar pkg={pkg} />}
                <Similarweb pkg={pkg} />
                <CommercialMotion pkg={pkg} />
              </>
            )}
            {tab === "sources" && (
              <>
                <TabIntro q="Why should you trust any of this?"
                          why="Everything above traces to here: what we covered (and honestly didn't), every claim with its evidence and counter-evidence, what we tried and found nothing, and every raw source with its timestamp." />
                <Coverage pkg={pkg} />
                <ClaimsLedger pkg={pkg} />
                <DataHonesty pkg={pkg} />
                <Evidence pkg={pkg} />
              </>
            )}
          </>
        )}
      </div>
      <TooltipLayer />
    </div>
  );
}
