import { useEffect, useState } from "react";
import { api } from "../api.js";
import { HBar } from "../components/charts.jsx";
import { Button, Card, DataTable, ErrorState, Grid, Kpi, Loading, Notice, PageHead, Select, Tabs, num, pct, useApi } from "../components/ui.jsx";

const TABS = ["Preview", "Columns & types", "Missing values", "Statistics", "Distributions", "Validation"];

export default function DatasetExplorer() {
  const list = useApi("/api/datasets");
  const [key, setKey] = useState("processed");
  const [upload, setUpload] = useState(null);
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("Preview");
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    if (upload) return;
    let live = true;
    setBusy(true);
    setErr(null);
    api(`/api/datasets/${key}?page=${page}&size=50${q ? `&search=${encodeURIComponent(q)}` : ""}`)
      .then((d) => live && setData(d))
      .catch((e) => live && setErr(e))
      .finally(() => live && setBusy(false));
    return () => {
      live = false;
    };
  }, [key, page, q, upload]);

  const inspect = async (file) => {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    setBusy(true);
    setErr(null);
    try {
      setData(await api("/api/datasets/inspect", { method: "POST", form }));
      setUpload(file.name);
      setTab("Validation");
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  const cb = data?.class_balance;
  const total = cb ? Object.values(cb).reduce((a, b) => a + b, 0) : 0;
  return (
    <>
      <PageHead title="Dataset Explorer" subtitle="Inspect the project datasets or a CSV/Excel file: types, missing values, validation against the data contract. Uploaded files are inspected only — nothing is stored. Personal-data columns and the Raw_Merged_Data sheet are refused.">
        <Select value={upload ? "" : key} onChange={(v) => { setUpload(null); setKey(v); setPage(0); }} placeholder={upload ? `Uploaded: ${upload}` : undefined}
          options={(list.data || []).filter((d) => d.available).map((d) => ({ value: d.key, label: d.label }))} aria-label="Dataset" />
        <label className="btn btn-ghost">
          Inspect a file…
          <input type="file" accept=".csv,.xlsx" hidden onChange={(e) => inspect(e.target.files[0])} />
        </label>
      </PageHead>
      <ErrorState error={err} />
      {busy && !data && <Loading />}
      {data && (
        <>
          <Grid cols={4}>
            <Kpi label="Rows" value={num(data.rows)} caption={data.source} />
            <Kpi label="Columns" value={data.columns} caption={`${data.dtypes.filter((d) => d.dtype !== "object" && d.dtype !== "str").length} numeric`} tone="blue" />
            <Kpi label="Missing cells" value={num(data.missing.reduce((a, m) => a + m.missing, 0))} caption={`${data.missing.length} column(s) affected`} tone={data.missing.length ? "gold" : "good"} />
            <Kpi label="Class balance" value={cb ? pct((cb["1"] || 0) / total) : "—"} caption={cb ? `attend · ${cb["1"] || 0} : ${cb["0"] || 0}` : "no target column"} tone="good" />
          </Grid>
          {data.source !== "UPLOAD (not stored)" && <Notice kind="info">Will_Attend in this dataset is SIMULATED from documented rules — not observed attendance.</Notice>}
          <Card title={data.label} actions={<Tabs tabs={TABS} value={tab} onChange={setTab} />}>
            {busy && <Loading label="Updating…" />}
            {tab === "Preview" && (
              <>
                {!upload && (
                  <div className="row" style={{ marginBottom: 10 }}>
                    <input type="search" placeholder="Search all rows (server-side)…" value={search} onChange={(e) => setSearch(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (setPage(0), setQ(search))} />
                    <Button kind="ghost" onClick={() => { setPage(0); setQ(search); }}>Search</Button>
                    <span className="spacer" />
                    <span className="muted small">{num(data.preview.total)} matching rows</span>
                    <Button kind="plain" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
                    <span className="muted small">page {page + 1} / {Math.max(1, Math.ceil(data.preview.total / 50))}</span>
                    <Button kind="plain" disabled={(page + 1) * 50 >= data.preview.total} onClick={() => setPage(page + 1)}>Next</Button>
                  </div>
                )}
                <DataTable rows={data.preview.rows} search={false} pageSize={50} />
              </>
            )}
            {tab === "Columns & types" && <DataTable rows={data.dtypes} pageSize={40} />}
            {tab === "Missing values" && (data.missing.length ? <Grid cols={2}><HBar data={data.missing} label="column" value="missing" color="#c47f14" /><DataTable rows={data.missing} search={false} /></Grid> : <Notice kind="ok">No missing values.</Notice>)}
            {tab === "Statistics" && <DataTable rows={data.describe} pageSize={30} />}
            {tab === "Distributions" && (
              <Grid cols={2}>
                {Object.entries(data.categorical).map(([col, vals]) => (
                  <div key={col}>
                    <h3 className="small" style={{ margin: "4px 0 6px" }}>{col}</h3>
                    <HBar data={vals} label="value" value="count" />
                  </div>
                ))}
              </Grid>
            )}
            {tab === "Validation" && (
              <div className="stack">
                <Notice kind={data.validation.ok ? "ok" : "error"}>{data.validation.ok ? "Validation passed against the data contract." : `Validation failed: ${data.validation.errors.length} error(s).`}</Notice>
                {data.validation.errors.map((e) => <Notice key={e} kind="error">{e}</Notice>)}
                {data.validation.warnings.map((e) => <Notice key={e} kind="info">{e}</Notice>)}
                {data.validation.info.map((e) => <Notice key={e} kind="note">{e}</Notice>)}
                <p className="muted small">Duplicate rows: {data.duplicate_rows} · duplicate student–event registrations: {data.duplicate_registrations ?? "—"}</p>
              </div>
            )}
          </Card>
        </>
      )}
    </>
  );
}
