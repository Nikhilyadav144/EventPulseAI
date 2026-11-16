import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";

// ---------------------------------------------------------------- data hook
export function useApi(path, deps = []) {
  const [state, set] = useState({ data: null, error: null, loading: true });
  const seq = useRef(0);
  const load = useCallback(async () => {
    if (!path) return;
    const n = ++seq.current;
    set((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await api(path);
      if (n === seq.current) set({ data, error: null, loading: false });
    } catch (e) {
      if (n === seq.current) set({ data: null, error: e, loading: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  useEffect(() => {
    load();
  }, [load]);
  return { ...state, reload: load };
}

// ---------------------------------------------------------------- formatting
export const pct = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(d)}%`);
export const num = (v, d = 0) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
export const f3 = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(3));
const LABELS = { ORIGINAL_HYBRID: "Original dataset", SYNTHETIC: "Synthetic", MANUAL: "Entered in app", UPLOAD: "Uploaded file",
  ORIGINAL_SIMULATED: "Simulated (dataset)" };
export const srcLabel = (s) => LABELS[s] || s;
export const titleCase = (s) => String(s).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

// ---------------------------------------------------------------- primitives
export function Card({ title, eyebrow, subtitle, actions, children, className = "", tight }) {
  return (
    <section className={`card ${tight ? "tight" : ""} ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <div>
            {eyebrow && <p className="eyebrow">{eyebrow}</p>}
            {title && <h2>{title}</h2>}
            {subtitle && <p className="muted small">{subtitle}</p>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Kpi({ label, value, caption, tone = "purple", hint }) {
  return (
    <div className={`kpi tone-${tone}`} title={hint || ""}>
      <span className="kpi-label">{label}</span>
      <strong className="kpi-value">{value}</strong>
      {caption && <span className="kpi-caption">{caption}</span>}
    </div>
  );
}

export const Grid = ({ cols = 3, children, className = "" }) => (
  <div className={`grid g${cols} ${className}`}>{children}</div>
);

export function Badge({ children, tone = "neutral" }) {
  return <span className={`badge b-${tone}`}>{children}</span>;
}

const TONES = { Completed: "good", Scheduled: "blue", Cancelled: "critical", Registered: "purple", Waitlisted: "gold",
  Upcoming: "blue", Ongoing: "purple", Undated: "neutral", Attended: "good", Absent: "gold", "Not recorded": "neutral",
  critical: "critical", warning: "gold", info: "blue", SIMULATED: "gold", RECORDED: "good" };
export const toneOf = (v) => (String(v).includes("simulated") ? "gold" : TONES[v] || "neutral");
export const StatusBadge = ({ value }) => (value ? <Badge tone={toneOf(value)}>{value}</Badge> : <span className="muted">—</span>);

export function Notice({ kind = "info", children }) {
  if (!children) return null;
  return <div className={`notice n-${kind}`} role={kind === "error" ? "alert" : "status"}>{children}</div>;
}

export function Loading({ label = "Loading…" }) {
  return (
    <div className="loading" aria-busy="true">
      <span className="spinner" /> {label}
    </div>
  );
}

export function ErrorState({ error, onRetry }) {
  if (!error) return null;
  return (
    <Notice kind="error">
      <b>{error.status === 0 ? "Backend unavailable." : "Something went wrong."}</b> {error.message}{" "}
      {onRetry && (
        <button className="link" onClick={onRetry}>
          Try again
        </button>
      )}
    </Notice>
  );
}

export function Empty({ title, children }) {
  return (
    <div className="empty">
      <b>{title}</b>
      {children && <p>{children}</p>}
    </div>
  );
}

export function Button({ kind = "primary", busy, children, ...p }) {
  return (
    <button className={`btn btn-${kind}`} disabled={busy || p.disabled} {...p}>
      {busy && <span className="spinner sm" />}
      {children}
    </button>
  );
}

export function Field({ label, hint, children, wide }) {
  return (
    <label className={`field ${wide ? "wide" : ""}`}>
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

export function Select({ value, onChange, options, placeholder, ...p }) {
  return (
    <select value={value ?? ""} onChange={(e) => onChange(e.target.value)} {...p}>
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map((o) =>
        typeof o === "object" ? (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ) : (
          <option key={o} value={o}>
            {o}
          </option>
        ),
      )}
    </select>
  );
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t} role="tab" aria-selected={value === t} className={value === t ? "on" : ""} onClick={() => onChange(t)}>
          {t}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- table
export function DataTable({ rows, columns, render = {}, pageSize = 15, search = true, onRow, selected, rowKey, empty, maxCols }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState(null);
  const [page, setPage] = useState(0);
  const cols = useMemo(() => {
    const c = columns || (rows && rows.length ? Object.keys(rows[0]) : []);
    return maxCols ? c.slice(0, maxCols) : c;
  }, [columns, rows, maxCols]);
  const filtered = useMemo(() => {
    let r = rows || [];
    if (q) {
      const s = q.toLowerCase();
      r = r.filter((x) => cols.some((c) => String(x[c] ?? "").toLowerCase().includes(s)));
    }
    if (sort) {
      const [c, dir] = sort;
      r = [...r].sort((a, b) => {
        const x = a[c], y = b[c];
        if (x === y) return 0;
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        return (x > y ? 1 : -1) * dir;
      });
    }
    return r;
  }, [rows, q, sort, cols]);
  useEffect(() => setPage(0), [q, rows]);
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const view = filtered.slice(page * pageSize, (page + 1) * pageSize);
  if (!rows || !rows.length) return <Empty title={empty || "No records"} />;
  return (
    <div className="table-wrap">
      {search && (
        <div className="table-tools">
          <input type="search" placeholder="Search this table…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search table" />
          <span className="muted small">{filtered.length.toLocaleString()} rows</span>
        </div>
      )}
      <div className="scroll-x">
        <table>
          <thead>
            <tr>
              {cols.map((c) => (
                <th key={c} onClick={() => setSort(sort && sort[0] === c ? [c, -sort[1]] : [c, 1])} aria-sort={sort && sort[0] === c ? (sort[1] > 0 ? "ascending" : "descending") : "none"}>
                  {titleCase(c)}
                  {sort && sort[0] === c ? (sort[1] > 0 ? " ▲" : " ▼") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {view.map((r, i) => {
              const key = rowKey ? r[rowKey] : i + page * pageSize;
              return (
                <tr key={key} onClick={onRow ? () => onRow(r) : undefined} className={`${onRow ? "clickable" : ""} ${selected !== undefined && selected === key ? "selected" : ""}`}>
                  {cols.map((c) => (
                    <td key={c}>{render[c] ? render[c](r[c], r) : fmtCell(r[c])}</td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="pager">
          <button className="btn btn-ghost sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
            Previous
          </button>
          <span className="muted small">
            Page {page + 1} of {pages}
          </span>
          <button className="btn btn-ghost sm" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>
            Next
          </button>
        </div>
      )}
    </div>
  );
}

function fmtCell(v) {
  if (v === null || v === undefined || v === "") return <span className="muted">—</span>;
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(3);
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

// ---------------------------------------------------------------- toast
let pushToast = () => {};
export const toast = (msg, kind = "ok") => pushToast(msg, kind);
export function Toasts() {
  const [items, set] = useState([]);
  pushToast = (msg, kind) => {
    const id = Math.random();
    set((x) => [...x, { id, msg, kind }]);
    setTimeout(() => set((x) => x.filter((t) => t.id !== id)), 4500);
  };
  return (
    <div className="toasts" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast t-${t.kind}`}>
          {t.msg}
        </div>
      ))}
    </div>
  );
}

export function PageHead({ title, subtitle, children }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {children && <div className="actions">{children}</div>}
    </div>
  );
}

export const SIM_NOTE =
  "Attendance outcomes in the imported datasets are SIMULATED. Figures based on them describe the simulation, not observed student behaviour.";
