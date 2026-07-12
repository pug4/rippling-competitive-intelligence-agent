import React, { useEffect, useState } from "react";

const pill = (level) => <span className={`pill ${level}`}>{level}</span>;

function useJson(url) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    if (!url) return;
    setData(null);
    setErr(null);
    fetch(url)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.statusText)))
      .then(setData)
      .catch(setErr);
  }, [url]);
  return [data, err];
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
  const [runs] = useJson("/api/runs");
  const [selected, setSelected] = useState(null);
  const [pkg] = useJson(selected ? `/api/runs/${selected}` : null);

  useEffect(() => {
    if (runs && runs.length && !selected) setSelected(runs[0].run_id);
  }, [runs, selected]);

  return (
    <div className="app">
      <div className="sidebar">
        <h1>Competitive Intel</h1>
        <p className="meta" style={{ color: "var(--muted)", fontSize: 12 }}>Runs</p>
        {!runs && <p className="empty">Loading…</p>}
        {runs && runs.length === 0 && <p className="empty">No runs yet. Run the CLI.</p>}
        {(runs || []).map((r) => (
          <div
            key={r.run_id}
            className={`runitem ${selected === r.run_id ? "active" : ""}`}
            onClick={() => setSelected(r.run_id)}
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
        {!pkg && <p className="empty">Select a run.</p>}
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
            <Positioning pkg={pkg} />
            <StrategyOverTime pkg={pkg} />
            <Coverage pkg={pkg} />
            <Evidence pkg={pkg} />
          </>
        )}
      </div>
    </div>
  );
}
