import { useState } from "react";
import { download } from "../api.js";
import { Button, Card, DataTable, ErrorState, Grid, Kpi, Loading, Notice, PageHead, f3, num, pct, toast, useApi } from "../components/ui.jsx";

const TABLES = [
  ["events", "Events with participation and error"], ["registrations", "All registrations"], ["attendance_recorded", "Attendance recorded in the app"],
  ["predictions", "Prediction history"], ["predicted_vs_actual", "Predicted vs actual evaluations"], ["models", "Model comparison"],
  ["mlflow_runs", "MLflow runs"], ["calendar", "Calendar"],
];

export default function Reports() {
  const { data, error, loading, reload } = useApi("/api/reports/summary");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState(null);
  const get = async (key, path, opts) => {
    setBusy(key);
    setErr(null);
    try {
      const name = await download(path, opts);
      toast(`Downloaded ${name}`);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy("");
    }
  };
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const k = data.kpis;
  return (
    <>
      <PageHead title="Reports & Export" subtitle="Generate a full report from the current database, or export any table. Files are generated on the server from stored records." />
      <ErrorState error={err} />
      <Grid cols={2}>
        <Card title="Full report" subtitle="KPIs, data sources, model comparison, events, predicted vs actual, and key findings with source labels.">
          <div className="row">
            <Button busy={busy === "pdf"} onClick={() => get("pdf", "/api/reports/pdf", { method: "POST", fallbackName: "EventPulse_report.pdf" })}>Download PDF report</Button>
            <Button kind="ghost" busy={busy === "xlsx"} onClick={() => get("xlsx", "/api/reports/xlsx", { method: "POST", fallbackName: "EventPulse_report.xlsx" })}>Download Excel workbook</Button>
          </div>
          <Grid cols={3} className="mt">
            <Kpi label="Events" value={num(k.events)} caption={`${k.completed_events} completed`} />
            <Kpi label="Registrations" value={num(k.registrations)} tone="blue" />
            <Kpi label="Active model" value={`${k.model_name} v${k.model_version}`} caption={`CV ROC-AUC ${f3(k.cv_roc_auc)}`} tone="good" />
          </Grid>
          {k.notes.map((n) => <Notice key={n} kind="note">{n}</Notice>)}
        </Card>
        <Card title="Key findings" subtitle="Included in both reports.">
          <ul className="tips">{data.findings.map((f) => <li key={f}>{f}</li>)}</ul>
          <p className="muted small">Recorded attendance rate: {k.recorded_attendance_rate != null ? pct(k.recorded_attendance_rate, 1) : "—"}{k.recorded_attendance_simulated ? " (includes SIMULATED outcomes)" : ""}</p>
        </Card>
      </Grid>
      <Card title="Table exports">
        <DataTable search={false} pageSize={20} rows={TABLES.map(([t, label]) => ({ table: t, contents: label, download: t }))} columns={["contents", "download"]}
          render={{
            download: (t) => (
              <div className="row">
                <Button kind="ghost" busy={busy === `${t}.csv`} onClick={() => get(`${t}.csv`, `/api/export/${t}?fmt=csv`)}>CSV</Button>
                <Button kind="ghost" busy={busy === `${t}.xlsx`} onClick={() => get(`${t}.xlsx`, `/api/export/${t}?fmt=xlsx`)}>Excel</Button>
              </div>
            ),
          }} />
      </Card>
    </>
  );
}
