import React, { useEffect, useState } from "react";
import { HBar, Heatmap, ProofBar } from "./charts";

const pill = (level) => <span className={`pill ${level}`}>{level}</span>;

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
      <h2>Data at a glance</h2>
      <div className="grid2">
        <div className="card"><div className="title">Source mix ({total} artifacts)</div><HBar data={srcData} /></div>
        <div className="card"><div className="title">Top message themes</div><HBar data={themeData} colorVar="--good" /></div>
        <div className="card"><div className="title">Competitive stance (observed)</div><HBar data={stanceData} colorVar="--warn" /></div>
      </div>
    </>
  );
}

function GapsVisual({ pkg }) {
  const gaps = pkg.proof_gaps || [];
  if (gaps.length === 0) return null;
  const focal = pkg.companies?.[1]?.canonical_name || "Rippling";
  const competitor = pkg.companies?.[0]?.canonical_name || "Competitor";
  return (
    <>
      <h2>Message–proof gaps (visual)</h2>
      <p className="empty" style={{ fontSize: 12 }}>
        For each repeated competitor claim: how strongly {competitor} proves it vs. how strongly {focal} can.
        Pill color = attackability (green = attack, yellow = investigate, red = don't).
      </p>
      <div className="card">
        {gaps.slice(0, 8).map((g) => (
          <div className="gaprow" key={g.claim_id}>
            <div>
              <div className="gaplabel">{g.short_label}</div>
              {pill(g.attackability)}
            </div>
            <div className="gapbars">
              <ProofBar strength={g.proof_strength} label={competitor} />
              <ProofBar strength={g.focal_proof_strength} label={focal} />
              <div className="gapclaim">
                Missing: {(g.missing_proof || []).join(", ") || "—"} · specificity {g.claim_specificity}
              </div>
            </div>
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
      <h2>Persona × channel coverage (heatmap)</h2>
      <div className="card"><Heatmap personas={m.personas} channels={m.channels} cells={m.cells || {}} /></div>
    </>
  );
}

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

function ActionBoard({ pkg }) {
  const changes = pkg.change_events || [];
  const gaps = pkg.proof_gaps || [];
  const opps = pkg.opportunities || [];
  const focal = pkg.companies?.[1]?.canonical_name || "Rippling";
  return (
    <>
      <h2>Action Board</h2>
      <h3>Largest strategy changes</h3>
      {changes.length === 0 && <p className="empty">No temporal change met the both-periods evidence bar.</p>}
      {changes.slice(0, 3).map((c) => (
        <div className="card" key={c.change_id}>
          <div className="title">
            {c.dimension} {pill(c.confidence)}
            <span className="pill">{c.lifecycle}</span>
          </div>
          <div className="row">
            <b>Prior:</b> {c.prior_state}
          </div>
          <div className="row">
            <b>Current:</b> {c.current_state}
          </div>
          {c.alternative_explanations?.length > 0 && (
            <div className="row">
              <b>Alt. explanations:</b> {c.alternative_explanations.join("; ")}
            </div>
          )}
        </div>
      ))}

      <h3>Strongest message–proof gaps</h3>
      {gaps.length === 0 && <p className="empty">No repeated competitor claim with a proof gap observed.</p>}
      {gaps.slice(0, 3).map((g) => (
        <div className="card" key={g.claim_id}>
          <div className="title">
            “{g.claim_text}” {pill(g.attackability)}
          </div>
          <div className="row">
            <b>Competitor proof:</b> {g.proof_strength} · <b>missing:</b> {(g.missing_proof || []).join(", ") || "—"}
          </div>
          <div className="row">{g.actionable_interpretation}</div>
        </div>
      ))}

      <h3>{focal}-relative recommended actions</h3>
      {opps.length === 0 && <p className="empty">No opportunity survived generation.</p>}
      {opps.slice(0, 3).map((o) => (
        <div className="card" key={o.opportunity_id}>
          <div className="title">
            {o.title} <span className="pill">{o.deliverable_type}</span> {pill(o.structural_defensibility)}
          </div>
          <div className="row"><b>Angle:</b> {o.message_angle}</div>
          <div className="row">
            <b>Focal proof:</b> {o.rippling_proof_status} · <b>already-saying-it:</b> {o.rippling_current_usage} · <b>legal review:</b> {String(o.legal_review_required)}
          </div>
          <div className="row"><b>Why it could backfire:</b> {o.why_this_could_backfire}</div>
          <div className="row"><b>Experiment:</b> {o.experiment_hypothesis}</div>
          <div className="row"><b>Kill rule:</b> {o.kill_rule}</div>
        </div>
      ))}
    </>
  );
}

function Positioning({ pkg }) {
  const cls = pkg.classifications || [];
  if (cls.length === 0) return null;
  const counts = {};
  cls.forEach((c) => { if (c.primary_message) counts[c.primary_message] = (counts[c.primary_message] || 0) + 1; });
  const dominant = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
  const villains = new Set();
  const stances = new Set();
  cls.forEach((c) => { (c.villain_normalized || []).forEach((v) => villains.add(v)); if (c.competitive_stance) stances.add(c.competitive_stance); });
  return (
    <>
      <h2>Current public positioning</h2>
      <div className="card">
        {dominant && <div className="row"><b>Dominant message:</b> {dominant[0]} (observed in {dominant[1]} artifacts)</div>}
        {villains.size > 0 && <div className="row"><b>Villains attacked:</b> {[...villains].join(", ")}</div>}
        {stances.size > 0 && <div className="row"><b>Competitive stance:</b> {[...stances].join(", ")}</div>}
      </div>
    </>
  );
}

function StrategyOverTime({ pkg }) {
  const changes = pkg.change_events || [];
  return (
    <>
      <h2>Strategy over time ({changes.length})</h2>
      <p className="empty" style={{ fontSize: 12 }}>
        A change is only asserted with evidence in <b>both</b> periods (real archive
        capture dates). Emerging themes are low-confidence; a coverage-asymmetry
        caveat is always attached — archive absence is not real-world absence.
      </p>
      {changes.length === 0 && (
        <p className="empty">No temporal change met the both-periods evidence bar on this run.</p>
      )}
      {changes.map((c) => (
        <div className="card" key={c.change_id}>
          <div className="title">
            {c.dimension} {pill(c.confidence)} <span className="pill">{c.lifecycle}</span>
          </div>
          <div className="row"><b>Prior:</b> {c.prior_state}</div>
          <div className="row"><b>Current:</b> {c.current_state}</div>
          <div className="row" style={{ color: "var(--muted)", fontSize: 12 }}>
            <b>Evidence:</b> {(c.prior_evidence_ids || []).length} prior · {(c.current_evidence_ids || []).length} current
          </div>
          {c.alternative_explanations?.length > 0 && (
            <div className="row"><b>Alt. explanations:</b> {c.alternative_explanations.join("; ")}</div>
          )}
        </div>
      ))}
    </>
  );
}

function Evidence({ pkg }) {
  const arts = pkg.artifacts || [];
  return (
    <>
      <h2>Evidence explorer ({arts.length} artifacts)</h2>
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
      <h2>Coverage & limitations</h2>
      <div className="cov">
        {entries.map(([k, v]) => (
          <div className="cell" key={k}><span>{k}</span><span>{v}</span></div>
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

export default function App() {
  const [refresh, setRefresh] = useState(0);
  const [runs] = useJson("/api/runs", refresh);
  const [selected, setSelected] = useState(null);
  const [pkg] = useJson(selected ? `/api/runs/${selected}` : null);
  const [jobs, setJobs] = useState([]);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (runs && runs.length && !selected) setSelected(runs[0].run_id);
  }, [runs, selected]);

  // Poll jobs while any is active; refresh the run list so completed runs appear.
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
            <ActionBoard pkg={pkg} />
            <DataVisuals pkg={pkg} />
            <GapsVisual pkg={pkg} />
            <Positioning pkg={pkg} />
            <StrategyOverTime pkg={pkg} />
            <PersonaChannelHeatmap pkg={pkg} />
            <Coverage pkg={pkg} />
            <Evidence pkg={pkg} />
          </>
        )}
      </div>
    </div>
  );
}
