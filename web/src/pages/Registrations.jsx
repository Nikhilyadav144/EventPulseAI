import { useState } from "react";
import { api, download, qs } from "../api.js";
import { go } from "../nav.js";
import { REG_STATUS } from "../lib/options.js";
import { HBar } from "../components/charts.jsx";
import { Button, Card, DataTable, ErrorState, Grid, Kpi, Loading, Notice, PageHead, Select, StatusBadge, num, srcLabel, toast, useApi } from "../components/ui.jsx";

export default function Registrations() {
  const [flt, setFlt] = useState({ search: "", event_id: "", department: "", status: "", data_source: "" });
  const [q, setQ] = useState("");
  const path = `/api/registrations${qs({ ...flt, search: q })}`;
  const { data, error, loading, reload } = useApi(path);
  const events = useApi("/api/events");
  const [file, setFile] = useState(null);
  const [dry, setDry] = useState(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState(null);
  const set = (k) => (v) => setFlt({ ...flt, [k]: v });

  const status = async (r, s) => {
    try {
      await api(`/api/registrations/${r.registration_id}`, { method: "PATCH", body: { status: s } });
      toast(`${r.student_id} → ${s}`);
      reload();
    } catch (e) {
      toast(e.message, "error");
    }
  };
  const runImport = async (commit) => {
    const form = new FormData();
    form.append("file", file);
    setBusy(commit ? "commit" : "dry");
    setErr(null);
    try {
      const r = await api(`/api/registrations/import?commit=${commit}`, { method: "POST", form });
      setDry(r);
      if (commit && r.committed) {
        toast(`Imported ${r.added} registration(s)`);
        setFile(null);
        reload();
      }
    } catch (e) {
      setErr(e);
    } finally {
      setBusy("");
    }
  };
  const rows = data?.rows || [];
  const counts = REG_STATUS.map((s) => rows.filter((r) => r.status === s).length);
  return (
    <>
      <PageHead title="Registration Management" subtitle="Every student–event registration with its status, origin and recorded attendance.">
        <Button kind="ghost" onClick={() => download(`/api/export/registrations${qs({ fmt: "xlsx", event_id: flt.event_id })}`)}>Export Excel</Button>
      </PageHead>
      <Card tight>
        <div className="row filters">
          <input type="search" placeholder="Search student, event…" value={flt.search} onChange={(e) => set("search")(e.target.value)} onKeyDown={(e) => e.key === "Enter" && setQ(flt.search)} aria-label="Search" />
          <Select value={flt.event_id} onChange={set("event_id")} placeholder="All events" options={(events.data || []).map((e) => ({ value: e.event_id, label: `${e.event_id} · ${e.name || e.event_type}` }))} aria-label="Event" />
          <Select value={flt.department} onChange={set("department")} placeholder="All departments" options={data?.all_departments || []} aria-label="Department" />
          <Select value={flt.status} onChange={set("status")} placeholder="All statuses" options={REG_STATUS} aria-label="Status" />
          <Select value={flt.data_source} onChange={set("data_source")} placeholder="All sources" options={[{ value: "ORIGINAL_HYBRID", label: "Original dataset" }, { value: "MANUAL", label: "Entered in app" }, { value: "UPLOAD", label: "Uploaded file" }]} aria-label="Source" />
          <Button kind="ghost" onClick={() => setQ(flt.search)}>Apply</Button>
        </div>
      </Card>
      <ErrorState error={error} onRetry={reload} />
      {loading && !data ? <Loading /> : data && (
        <>
          <Grid cols={4}>
            <Kpi label="Registrations" value={num(rows.length)} caption="matching the filters" />
            <Kpi label="Registered" value={num(counts[0])} tone="good" />
            <Kpi label="Waitlisted" value={num(counts[1])} tone="gold" />
            <Kpi label="Cancelled" value={num(counts[2])} tone="critical" />
          </Grid>
          <Grid cols={2}>
            <Card title="Registrations by department" subtitle="Counts of stored records (no outcomes involved).">
              <HBar data={data.departments} label="department" value="registrations" />
            </Card>
            <Card title="Import registrations" subtitle="CSV/Excel with Student_ID and Event_ID (+ optional Registration_Days_Before, Has_Exam_Near_Event). A dry run checks the file first; committing is all-or-nothing and skips duplicates.">
              <div className="stack">
                <input type="file" accept=".csv,.xlsx" onChange={(e) => { setFile(e.target.files[0] || null); setDry(null); }} aria-label="Registrations file" />
                <div className="row">
                  <Button kind="ghost" disabled={!file} busy={busy === "dry"} onClick={() => runImport(false)}>Dry run</Button>
                  <Button disabled={!file || !dry || dry.errors.length > 0 || dry.committed} busy={busy === "commit"} onClick={() => runImport(true)}>Commit import</Button>
                </div>
                {dry && <Notice kind={dry.errors.length ? "error" : dry.committed ? "ok" : "note"}>{dry.committed ? "Committed. " : "Dry run — nothing saved. "}{dry.summary}</Notice>}
                {dry?.errors.slice(0, 8).map((e) => <Notice key={e} kind="error">{e}</Notice>)}
                <ErrorState error={err} />
              </div>
            </Card>
          </Grid>
          <Card title="Registrations" tight>
            <DataTable rows={rows} rowKey="registration_id" pageSize={20}
              columns={["registration_id", "student_id", "event_id", "event_name", "event_date", "department", "course", "year", "status", "attendance", "data_source", "change"]}
              render={{
                status: (v) => <StatusBadge value={v} />, attendance: (v) => <StatusBadge value={v} />, data_source: srcLabel,
                event_id: (v) => <button className="link" onClick={() => go("events", v)}>{v}</button>,
                change: (_, r) => <Select value={r.status} onChange={(s) => s !== r.status && status(r, s)} options={REG_STATUS} aria-label={`Status of ${r.registration_id}`} />,
              }} />
          </Card>
        </>
      )}
    </>
  );
}
