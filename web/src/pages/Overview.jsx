import { Card, DataTable, ErrorState, Grid, Kpi, Loading, Notice, PageHead, StatusBadge, f3, num, pct, useApi } from "../components/ui.jsx";
import { HBar, SplitBar } from "../components/charts.jsx";

export default function Overview() {
  const { data, error, loading, reload } = useApi("/api/overview");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const k = data.kpis, s = data.sources;
  const part = (data.participation || []).filter((r) => r.registrations > 0);
  return (
    <>
      <PageHead title="Overview" subtitle="A live summary of students, events, the active model and stored predictions." />
      {k.notes.map((n) => (
        <Notice key={n} kind={n.includes("SIMULATED") ? "info" : "note"}>{n}</Notice>
      ))}
      <Grid cols={6}>
        <Kpi label="Students" value={num(k.students)} caption={`${num(k.registrations)} registrations`} />
        <Kpi label="Events" value={num(k.events)} caption={`${k.completed_events} completed · ${k.upcoming_events} scheduled`} tone="blue" />
        <Kpi label="Predicted attendees" value={k.predicted_attendees ?? "—"} caption={k.expected_attendance != null ? `expected ${k.expected_attendance} (sum of probabilities)` : "no predictions yet"} tone="good" />
        <Kpi label="Predicted no-shows" value={k.predicted_non_attendees ?? "—"} caption={k.predicted_registrations ? `of ${k.predicted_registrations} predicted` : "no predictions yet"} tone="gold" />
        <Kpi label="CV ROC-AUC" value={f3(k.cv_roc_auc)} caption={k.cv_roc_auc_std != null ? `± ${k.cv_roc_auc_std.toFixed(3)} · holdout ${f3(k.roc_auc)}` : ""} hint="Cross-validated ROC-AUC is the primary estimate; the holdout is a single split shown for reference." />
        <Kpi label="Holdout accuracy" value={pct(k.accuracy, 1)} caption={k.baseline_accuracy != null ? `baseline ${pct(k.baseline_accuracy, 1)} (always 'attend')` : ""} tone={k.accuracy > k.baseline_accuracy ? "good" : "gold"} />
      </Grid>
      <Grid cols={2}>
        <Card title="Attendance outcomes" subtitle={data.distribution.simulated ? "Stored outcomes — SIMULATED in the imported dataset." : "Outcomes recorded after real events."}>
          <SplitBar parts={[{ label: "Attended", value: data.distribution.attended }, { label: "Did not attend", value: data.distribution.did_not_attend }]} />
        </Card>
        <Card title="Data sources" subtitle="Every record keeps its origin; nothing is mixed silently.">
          <dl className="info">
            <dt>Original dataset</dt>
            <dd>{s.students?.ORIGINAL_HYBRID ?? 0} students · {s.events?.ORIGINAL_HYBRID ?? 0} events · {s.registrations?.ORIGINAL_HYBRID ?? 0} registrations</dd>
            <dt>Entered in app</dt>
            <dd>{(s.events?.MANUAL ?? 0) + (s.events?.UPLOAD ?? 0)} events · {(s.registrations?.MANUAL ?? 0) + (s.registrations?.UPLOAD ?? 0)} registrations</dd>
            <dt>Simulated outcomes</dt>
            <dd>{s.simulated_outcomes} (from the dataset, not observed)</dd>
            <dt>Recorded attendance</dt>
            <dd>{s.observed_outcomes} outcomes entered after real events</dd>
            <dt>Upcoming events</dt>
            <dd>{s.future_events}</dd>
          </dl>
        </Card>
      </Grid>
      <Grid cols={2}>
        <Card title="Registrations by department" subtitle="Stored registrations, all sources.">
          <HBar data={part} label="Department" value="registrations" />
        </Card>
        <Card title="Recent activity" subtitle="Predictions, evaluations and changes stored in the database.">
          <DataTable rows={data.activity} search={false} pageSize={8} empty="No activity yet" />
        </Card>
      </Grid>
      <Card title="Recent predictions">
        <DataTable rows={data.recent_predictions} search={false} pageSize={8} empty="No predictions stored yet"
          render={{ outcome: (v) => <StatusBadge value={v} /> }} />
      </Card>
    </>
  );
}
