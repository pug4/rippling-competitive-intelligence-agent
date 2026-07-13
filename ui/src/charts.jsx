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

// Compact two-series horizontal bar (competitor vs focal). Values come from
// the spec verbatim; only the bar WIDTH is scaled for display (never the data).
export function GroupedBar({ data, series, colorA = "--bad", colorB = "--good" }) {
  const rows = (data || [])
    .map((d) => {
      const a = d.a != null ? d.a : d.competitor != null ? d.competitor : Array.isArray(d.values) ? d.values[0] : null;
      const b = d.b != null ? d.b : d.focal != null ? d.focal : Array.isArray(d.values) ? d.values[1] : null;
      return {
        label: d.label,
        a: a == null || a === "" ? null : Number(a),
        b: b == null || b === "" ? null : Number(b),
        aDisplay: d.a_display, bDisplay: d.b_display,
      };
    })
    .filter((r) => r.a != null || r.b != null);
  if (rows.length === 0) return <p className="empty">No data.</p>;
  const [aLabel, bLabel] = Array.isArray(series) && series.length === 2 ? series : ["Competitor", "Focal"];
  let max = 1;
  rows.forEach((r) => { max = Math.max(max, r.a || 0, r.b || 0); });
  const bar = (v, disp, color) => (
    <div className="gbar-track">
      <div className="gbar-fill" style={{ width: `${((v || 0) / max) * 100}%`, background: `var(${color})` }} />
      <span className="gbar-num">{disp != null ? disp : v != null ? v : "—"}</span>
    </div>
  );
  return (
    <div className="gbar">
      <div className="gbar-legend">
        <span className="gbar-key"><i style={{ background: `var(${colorA})` }} />{aLabel}</span>
        <span className="gbar-key"><i style={{ background: `var(${colorB})` }} />{bLabel}</span>
      </div>
      {rows.map((r, i) => (
        <div className="gbar-row" key={i}>
          <div className="gbar-label" data-tip={r.label}>{r.label}</div>
          <div className="gbar-bars">
            {bar(r.a, r.aDisplay, colorA)}
            {bar(r.b, r.bDisplay, colorB)}
          </div>
        </div>
      ))}
    </div>
  );
}

// Pipe-safe data table. Cells render through React textContent ONLY — never
// innerHTML, so any pipe/markup in the payload is inert. Rows may be arrays
// (already column-ordered) or objects keyed by column name.
export function VizTable({ columns, rows }) {
  const cols = (columns || []).map((c) => String(c));
  const body = (rows || []).map((r) =>
    Array.isArray(r)
      ? r
      : cols.length
        ? cols.map((c) => (r[c] != null ? r[c] : r[c.toLowerCase()] != null ? r[c.toLowerCase()] : ""))
        : Object.values(r || {})
  );
  if (!cols.length && !body.length) return <p className="empty">No table data.</p>;
  return (
    <div className="viztable-wrap">
      <table className="viztable">
        {cols.length > 0 && (
          <thead>
            <tr>{cols.map((c, i) => <th key={i}>{c}</th>)}</tr>
          </thead>
        )}
        <tbody>
          {body.map((row, ri) => (
            <tr key={ri}>
              {row.map((cell, ci) => <td key={ci}>{cell == null ? "" : String(cell)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Minimal SVG sparkline for a single numeric series; falls back to bars when
// there are fewer than two plottable points. Geometry is derived for layout —
// the underlying values are taken from the spec unchanged.
export function Sparkline({ data }) {
  const pts = (data || [])
    .map((d) => (typeof d === "number" ? d : d && d.value != null ? Number(d.value) : null))
    .filter((v) => v != null && !Number.isNaN(v));
  if (pts.length < 2) {
    const rows = (data || []).map((d, i) => ({
      label: d && d.label != null ? d.label : String(i + 1),
      value: typeof d === "number" ? d : Number(d?.value) || 0,
    }));
    return <HBar data={rows} />;
  }
  const w = 260, h = 48, pad = 4;
  const min = Math.min(...pts), max = Math.max(...pts);
  const span = max - min || 1;
  const step = (w - pad * 2) / (pts.length - 1);
  const coords = pts.map((v, i) => [pad + i * step, h - pad - ((v - min) / span) * (h - pad * 2)]);
  const dAttr = coords.map((c, i) => `${i ? "L" : "M"}${c[0].toFixed(1)},${c[1].toFixed(1)}`).join(" ");
  return (
    <svg className="sparkline" viewBox={`0 0 ${w} ${h}`} width="100%" height={h} preserveAspectRatio="none" role="img" aria-label="trend sparkline">
      <path d={dAttr} fill="none" stroke="var(--accent)" strokeWidth="2" />
      {coords.map((c, i) => <circle key={i} cx={c[0]} cy={c[1]} r="2" fill="var(--accent)" />)}
    </svg>
  );
}

// Agent-returned visualization spec -> a dependency-free primitive. Renders
// ONLY the numbers the spec carries (no client-side analytics); the primitives
// scale bar widths / heatmap intensity for display only. Shape:
//   { type, title, caption, data, citations, source_note, ... }
export function VizSpec({ spec }) {
  if (!spec || typeof spec !== "object") return null;
  const type = String(spec.type || "").toLowerCase();
  const data = spec.data;
  let body = null;
  if (type === "bar") {
    const rows = Array.isArray(data)
      ? data.map((d) => ({
          label: d.label, value: d.value == null || d.value === "" ? null : Number(d.value),
          display: d.display, colorVar: d.colorVar, tip: d.tip,
        }))
      : Object.entries(data || {}).map(([label, value]) => ({ label, value: Number(value) }));
    body = <HBar data={rows} unit={spec.unit || ""} />;
  } else if (type === "grouped_bar") {
    // Backend spec nests the payload under data = {groups, series:[{name,values}]}.
    // Fold it into per-row {label, a, b} + [aName, bName]; accept a ready array too.
    let rows = data, seriesNames = spec.series;
    if (data && !Array.isArray(data) && Array.isArray(data.groups)) {
      const s = Array.isArray(data.series) ? data.series : [];
      const s0 = s[0] || { values: [] }, s1 = s[1] || { values: [] };
      rows = data.groups.map((label, i) => ({ label, a: (s0.values || [])[i], b: (s1.values || [])[i] }));
      seriesNames = [s0.name, s1.name];
    }
    body = <GroupedBar data={rows} series={seriesNames} />;
  } else if (type === "table") {
    // Backend nests columns/rows under data; also accept them at the top level.
    const cols = spec.columns || (data && data.columns);
    const trows = spec.rows || (data && data.rows) || (Array.isArray(data) ? data : []);
    body = <VizTable columns={cols} rows={trows} />;
  } else if (type === "heatmap") {
    // Backend nests rows/cols/cells under data; also accept them at the top level.
    const hm = data && !Array.isArray(data) ? data : {};
    body = <Heatmap personas={spec.rows || hm.rows} channels={spec.columns || hm.cols} cells={spec.cells || hm.cells} />;
  } else if (type === "line") {
    body = <Sparkline data={data} />;
  } else if (Array.isArray(data)) {
    body = <HBar data={data.map((d) => ({ label: d.label, value: Number(d.value), display: d.display }))} />;
  } else {
    body = <p className="empty">Unsupported visualization.</p>;
  }
  const cites = spec.citations || [];
  return (
    <div className="vizspec">
      {spec.title && <div className="viz-title">{spec.title}</div>}
      {body}
      {spec.caption && <div className="viz-caption">{spec.caption}</div>}
      {cites.length > 0 && (
        <div className="viz-cites" data-tip="Sources this chart was built from — click to open the underlying page">
          {cites.map((c, i) => {
            const url = typeof c === "string" ? c : c && c.url;
            const label = typeof c === "string" ? c : (c && (c.label || c.title || c.url)) || "";
            return url
              ? <a key={i} className="chip" href={url} target="_blank" rel="noreferrer">{String(label).replace(/^https?:\/\/(www\.)?/, "").slice(0, 40)} ↗</a>
              : <span key={i} className="chip" style={{ cursor: "default" }}>{String(label).slice(0, 40)}</span>;
          })}
        </div>
      )}
      {spec.source_note && <div className="viz-source">{spec.source_note}</div>}
    </div>
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
