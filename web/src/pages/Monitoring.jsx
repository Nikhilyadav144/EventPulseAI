import { Columns } from "../components/charts.jsx";
import { Badge, Card, DataTable, ErrorState, Grid, Kpi, Loading, Notice, PageHead, num, pct, useApi } from "../components/ui.jsx";

export default function Monitoring() {
  const { data, error, loading, reload } = useApi("/api/monitoring");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const acc = data.accuracy || [];
  const ok = data.recommendation?.toLowerCase().startsWith("no");
  return (
    <>
      <PageHead title="Model Monitoring" subtitle="Compares incoming prediction inputs with the training data (drift), counts rejected requests, and tracks accuracy on events with recorded attendance." />
      <Grid cols={4}>
        <Kpi label="Incoming records" value={num(data.n_incoming)} caption="inputs of stored predictions + API log" tone="blue" />
        <Kpi label="Drifted features" value={data.drifted.length} caption={data.drifted.join(", ") || "none"} tone={data.drifted.length ? "critical" : "good"} />
        <Kpi label="Schema violations" value={num(data.schema_violations)} caption="rejected API requests" tone={data.schema_violations ? "gold" : "good"} />
        <Kpi label="Evaluated events" value={acc.length} caption="with recorded outcomes" />
      </Grid>
      <Card title="Recommendation">
        <Notice kind={ok ? "ok" : "note"}><b>{data.recommendation}</b></Notice>
        <ul className="tips">{data.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
      </Card>
      <Card title="Feature drift" subtitle="Numeric features: standardised mean difference (drift when |SMD| > 0.5). Categorical features: largest change in a category share (drift when > 15 points).">
        {data.report?.length ? (
          <DataTable rows={data.report} rowKey="feature" render={{ drift: (x) => (x ? <Badge tone="critical">Drift</Badge> : <Badge tone="good">OK</Badge>), score: (x) => (x == null ? "—" : x.toFixed(3)), missing_training: (x) => pct(x, 1), missing_incoming: (x) => pct(x, 1) }} />
        ) : <Notice kind="note">Not enough incoming data to measure drift yet. Drift is computed once predictions have been made on new registrations.</Notice>}
      </Card>
      <Card title="Accuracy on recorded events">
        {acc.length ? (
          <>
            <Columns categories={acc.map((a) => a.event_id)} series={[{ name: "Accuracy", values: acc.map((a) => a.accuracy) }, { name: "Balanced accuracy", values: acc.map((a) => a.balanced_accuracy), color: "#1e8fa8" }]} yMax={1} as={(x) => pct(x)} height={210} />
            <DataTable rows={acc} search={false} render={{ accuracy: (x) => pct(x, 1), balanced_accuracy: (x) => pct(x, 1) }} />
          </>
        ) : <Notice kind="note">No events with recorded attendance have been evaluated yet.</Notice>}
      </Card>
    </>
  );
}
