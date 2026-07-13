import React from "react";

// Horizontal bar chart from [{label, value, colorVar?}]. Dependency-free.
export function HBar({ data, unit = "", colorVar = "--accent" }) {
  const rows = (data || []).filter((d) => d && d.value != null);
  if (rows.length === 0) return <p className="empty">No data.</p>;
  const max = Math.max(1, ...rows.map((d) => d.value));
  return (
    <div className="hbar">
      {rows.map((d) => (
        <div className="hbar-row" key={d.label}>
          <div className="hbar-label" data-tip={d.tip || d.label}>{d.label}</div>
          <div className="hbar-track">
            <div
              className="hbar-fill"
              style={{ width: `${(d.value / max) * 100}%`, background: `var(${d.colorVar || colorVar})` }}
            />
          </div>
          <div className="hbar-val">{d.display != null ? d.display : `${d.value}${unit}`}</div>
        </div>
      ))}
    </div>
  );
}

// Proof strength as a 0-3 segmented bar (none/weak/moderate/strong).
const PROOF_LEVEL = { none: 0, weak: 1, moderate: 2, medium: 2, strong: 3, high: 3 };
export function ProofBar({ strength, label }) {
  const level = PROOF_LEVEL[(strength || "none").toLowerCase()] ?? 0;
  const color = level >= 3 ? "--good" : level === 2 ? "--warn" : level >= 1 ? "--bad" : "--border";
  return (
    <span className="proofbar" data-tip={`${label || ""} ${strength || "none"}`}>
      {label && <span className="proofbar-tag">{label}</span>}
      {[1, 2, 3].map((i) => (
        <span
          key={i}
          className="proofseg"
          style={{ background: i <= level ? `var(${color})` : "var(--panel2)" }}
        />
      ))}
      <span className="proofbar-label">{strength || "none"}</span>
    </span>
  );
}

// Personas × channels coverage heatmap; intensity scales with cell count.
export function Heatmap({ personas, channels, cells, rowTip, colTip, cellTip }) {
  // Drop all-zero columns — an empty channel column is noise, not whitespace.
  const liveChannels = (channels || []).filter((c) => (personas || []).some((p) => ((cells || {})[p] || {})[c] > 0));
  channels = liveChannels.length ? liveChannels : channels;
  if (!personas?.length || !channels?.length) return <p className="empty">No matrix data.</p>;
  let max = 1;
  personas.forEach((p) => channels.forEach((c) => { max = Math.max(max, (cells[p] || {})[c] || 0); }));
  return (
    <div className="heatmap-wrap">
      <div className="heatmap" style={{ gridTemplateColumns: `160px repeat(${channels.length}, minmax(64px, 1fr))` }}>
        <div className="hm-corner" />
        {channels.map((c) => <div className="hm-col" key={c} data-tip={colTip ? colTip(c) : c}>{c}</div>)}
        {personas.map((p) => (
          <React.Fragment key={p}>
            <div className="hm-rowlabel" data-tip={rowTip ? rowTip(p) : p}>{p.replace(/_/g, " ")}</div>
            {channels.map((c) => {
              const v = (cells[p] || {})[c] || 0;
              const a = v ? 0.12 + 0.88 * (v / max) : 0;
              return (
                <div
                  className="hm-cell"
                  key={c}
                  style={{ background: v ? `rgba(110,168,254,${a})` : "transparent" }}
                  data-tip={cellTip ? cellTip(p, c, v) : `${p} × ${c}: ${v}`}
                >
                  {v || ""}
                </div>
              );
            })}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}
