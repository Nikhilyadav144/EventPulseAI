import { useState } from "react";

// Validated categorical order (same as the desktop Cream theme): purple, amber, teal, rose.
export const SERIES = ["#6956a5", "#c47f14", "#1e8fa8", "#b8496e"];

function Tip({ tip }) {
  if (!tip) return null;
  return (
    <div className="chart-tip" style={{ left: tip.x, top: tip.y }}>
      {tip.text}
    </div>
  );
}

const fmt = (v, as) => (v === null || v === undefined ? "—" : typeof as === "function" ? as(v) : as === "pct" ? `${(v * 100).toFixed(0)}%` : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }));

/** Horizontal bars: one value per category. */
export function HBar({ data, label, value, as, color = SERIES[0], max, height = 22, unit = "" }) {
  const [tip, setTip] = useState(null);
  if (!data || !data.length) return <p className="muted small">No data to chart.</p>;
  const m = max ?? (Math.max(...data.map((d) => d[value] || 0), as === "pct" ? 1 : 0) || 1);
  const W = 480, L = 165, R = 54, H = data.length * (height + 8) + 8;
  return (
    <div className="chart" onMouseLeave={() => setTip(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ maxWidth: 600 }} role="img" aria-label={label}>
        {data.map((d, i) => {
          const v = d[value] ?? 0, y = 8 + i * (height + 8), w = ((W - L - R) * v) / m;
          return (
            <g key={i} onMouseMove={(e) => setTip({ x: e.nativeEvent.offsetX + 12, y: e.nativeEvent.offsetY - 8, text: `${d[label]}: ${fmt(d[value], as)}${unit}` })}>
              <rect x={0} y={y - 4} width={W} height={height + 8} fill="transparent" />
              <text x={L - 10} y={y + height / 2 + 4} textAnchor="end" className="ax">
                {String(d[label]).length > 26 ? String(d[label]).slice(0, 25) + "…" : d[label]}
              </text>
              <rect x={L} y={y} width={Math.max(w, v > 0 ? 2 : 0, 0)} height={height} rx={4} fill={color} />
              <text x={L + w + 6} y={y + height / 2 + 4} className="val">
                {fmt(d[value], as)}
              </text>
            </g>
          );
        })}
      </svg>
      <Tip tip={tip} />
    </div>
  );
}

/** Vertical grouped bars: categories on x, one bar per series. */
export function Columns({ categories, series, as, height = 240, yMax }) {
  const [tip, setTip] = useState(null);
  if (!categories || !categories.length) return <p className="muted small">No data to chart.</p>;
  const W = 520, P = { l: 44, r: 12, t: 12, b: 46 };
  const all = series.flatMap((s) => s.values.filter((v) => v !== null && v !== undefined));
  const m = yMax ?? (Math.max(...all, 0) || 1);
  const cw = (W - P.l - P.r) / categories.length, bw = Math.max(4, (cw - 8) / series.length - 2);
  const y = (v) => P.t + (height - P.t - P.b) * (1 - v / m);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * m);
  return (
    <div className="chart" onMouseLeave={() => setTip(null)}>
      <svg viewBox={`0 0 ${W} ${height}`} style={{ maxWidth: 780 }} role="img">
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} className="grid" />
            <text x={P.l - 6} y={y(t) + 4} textAnchor="end" className="ax">
              {fmt(t, as)}
            </text>
          </g>
        ))}
        {categories.map((c, i) => (
          <g key={c}>
            {series.map((s, j) => {
              const v = s.values[i];
              if (v === null || v === undefined) return null;
              const x = P.l + i * cw + 4 + j * (bw + 2);
              return (
                <rect key={j} x={x} y={y(v)} width={bw} height={Math.max(0, y(0) - y(v))} rx={3} fill={s.color || SERIES[j % 4]}
                  onMouseMove={(e) => setTip({ x: e.nativeEvent.offsetX + 12, y: e.nativeEvent.offsetY - 8, text: `${c} · ${s.name}: ${fmt(v, as)}` })} />
              );
            })}
            <text x={P.l + i * cw + cw / 2} y={height - P.b + 16} textAnchor="middle" className="ax">
              {String(c).length > 12 ? String(c).slice(0, 11) + "…" : c}
            </text>
          </g>
        ))}
      </svg>
      {series.length > 1 && (
        <div className="legend">
          {series.map((s, j) => (
            <span key={s.name}>
              <i style={{ background: s.color || SERIES[j % 4] }} /> {s.name}
            </span>
          ))}
        </div>
      )}
      <Tip tip={tip} />
    </div>
  );
}

/** ROC curve (line) with the chance diagonal. */
export function RocChart({ points, auc }) {
  if (!points || !points.fpr) return <p className="muted small">No ROC curve stored for this model.</p>;
  const S = 260, P = 34;
  const x = (v) => P + v * (S - P - 10), y = (v) => S - P - v * (S - P - 10);
  const d = points.fpr.map((f, i) => `${i ? "L" : "M"}${x(f).toFixed(1)},${y(points.tpr[i]).toFixed(1)}`).join(" ");
  return (
    <div className="chart roc">
      <svg viewBox={`0 0 ${S} ${S}`} role="img" aria-label={`ROC curve, AUC ${auc}`}>
        <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} className="diag" />
        <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(0)} className="axis" />
        <line x1={x(0)} y1={y(0)} x2={x(0)} y2={y(1)} className="axis" />
        <path d={d} fill="none" stroke={SERIES[0]} strokeWidth="2" />
        <text x={x(0.5)} y={S - 6} textAnchor="middle" className="ax">False positive rate</text>
        <text x={10} y={y(0.5)} transform={`rotate(-90 10 ${y(0.5)})`} textAnchor="middle" className="ax">True positive rate</text>
        {auc !== undefined && auc !== null && (
          <text x={x(0.62)} y={y(0.12)} className="val">AUC {Number(auc).toFixed(3)}</text>
        )}
      </svg>
    </div>
  );
}

/** Single stacked bar for a two-way split (e.g. will attend / will not attend). */
export function SplitBar({ parts }) {
  const total = parts.reduce((a, p) => a + (p.value || 0), 0);
  if (!total) return <p className="muted small">Nothing to show yet.</p>;
  return (
    <div>
      <div className="splitbar">
        {parts.map((p, i) => (
          <span key={p.label} style={{ width: `${(100 * p.value) / total}%`, background: p.color || SERIES[i] }} title={`${p.label}: ${p.value}`} />
        ))}
      </div>
      <div className="legend">
        {parts.map((p, i) => (
          <span key={p.label}>
            <i style={{ background: p.color || SERIES[i] }} /> {p.label}: {p.value.toLocaleString()} ({((100 * p.value) / total).toFixed(0)}%)
          </span>
        ))}
      </div>
    </div>
  );
}
