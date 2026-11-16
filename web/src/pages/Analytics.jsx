import { useState } from "react";
import { download } from "../api.js";
import { Columns, HBar, SplitBar } from "../components/charts.jsx";
import { Button, Card, DataTable, ErrorState, Grid, Kpi, Loading, Notice, PageHead, Select, SIM_NOTE, StatusBadge, num, pct, srcLabel, useApi } from "../components/ui.jsx";

const DIMS = ["Department", "Course", "Year", "Event_Type", "Event_Day", "Event_Time", "Weather_Forecast", "Experience_Level", "Area_Of_Interest", "Role", "Distance_Category"];

export default function Analytics() {
  const [dim, setDim] = useState("Department");
  const [source, setSource] = useState("");
  const { data, error, loading, reload } = useApi(`/api/analytics?dimension=${dim}${source ? `&source=${source}` : ""}`);
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <Loading />;
  const d = data.distribution, e = data.errors, er = data.errors_recorded;
  const part = data.participation.filter((r) => r.registrations > 0);
  const pva = data.predicted_vs_actual;
  const recordedEvents = data.events.filter((x) => x.recorded_outcomes > 0 && !x.target_is_simulated);
  return (
    <>
      <PageHead title="Analytics & Insights" subtitle="Computed from the records in the database. Simulated dataset outcomes and attendance recorded in the app are always labelled separately.">
        <Select value={dim} onChange={setDim} options={DIMS.map((x) => ({ value: x, label: x.replace(/_/g, " ") }))} aria-label="Dimension" />
        <Select value={source} onChange={setSource} placeholder="All sources" options={[{ value: "ORIGINAL_HYBRID", label: "Original dataset" }, { value: "MANUAL", label: "Entered in app" }]} aria-label="Source" />
        <Button kind="ghost" onClick={() => download("/api/export/events?fmt=xlsx")}>Export events</Button>
      </PageHead>
      {loading && <Loading label="Updating…" />}
      {d.simulated && <Notice kind="info">{SIM_NOTE}</Notice>}
      <Grid cols={4}>
        <Kpi label="Outcomes stored" value={num(d.recorded)} caption={d.simulated ? "includes SIMULATED outcomes" : "recorded in the app"} />
        <Kpi label="Attendance rate" value={pct(d.rate, 1)} caption={`${num(d.attended)} attended · ${num(d.did_not_attend)} did not`} tone="good" />
        <Kpi label="Events evaluated (recorded)" value={num(er?.events || 0)} caption="predicted vs actual, real outcomes only" tone="blue" />
        <Kpi label="Mean absolute error" value={er?.events ? num(er.mean_absolute_error, 2) : "—"} caption={er?.events ? `attendees per event · ${num(er.mean_percentage_error, 1)}% mean error` : "no recorded events evaluated yet"} tone="gold" />
      </Grid>
      <Grid cols={2}>
        <Card title={`Attendance rate by ${dim.replace(/_/g, " ").toLowerCase()}`} subtitle="Share of stored outcomes marked 'attended'.">
          {part.length ? <HBar data={part} label={dim} value="attendance_rate" as={(x) => pct(x, 1)} max={1} /> : <Notice kind="note">No outcomes for this selection.</Notice>}
        </Card>
        <Card title={`Registrations by ${dim.replace(/_/g, " ").toLowerCase()}`}>
          {part.length ? <HBar data={part} label={dim} value="registrations" color="#1e8fa8" /> : <Notice kind="note">No registrations for this selection.</Notice>}
        </Card>
      </Grid>
      <Grid cols={2}>
        <Card title="Stored prediction probabilities" subtitle={`${num(data.probability.n)} stored predictions · mean ${pct(data.probability.mean, 1)}`}>
          {data.probability.n ? (
            <Columns categories={data.probability.counts.map((_, i) => `${Math.round(data.probability.edges[i] * 100)}–${Math.round(data.probability.edges[i + 1] * 100)}%`)} series={[{ name: "Predictions", values: data.probability.counts }]} height={210} />
          ) : <Notice kind="note">No predictions stored yet. Run one on the Attendance Prediction page.</Notice>}
        </Card>
        <Card title="Outcome split" subtitle={d.simulated ? "SIMULATED + recorded outcomes" : "Recorded outcomes"}>
          <SplitBar parts={[{ label: "Attended", value: d.attended }, { label: "Did not attend", value: d.did_not_attend }]} />
          {e?.events > 0 && e.includes_simulated && <Notice kind="note">Including simulated events, {e.events} evaluations have a mean absolute error of {num(e.mean_absolute_error, 2)} attendees.</Notice>}
        </Card>
      </Grid>
      <Card title="Predicted vs actual attendance" subtitle="Each row compares the model's expected attendance (sum of probabilities) with the recorded count for an event.">
        {pva.length ? (
          <>
            <Columns categories={pva.slice(-12).map((r) => r.event_id)} series={[{ name: "Expected", values: pva.slice(-12).map((r) => r.expected_attendance) }, { name: "Actual", values: pva.slice(-12).map((r) => r.actual_attendees), color: "#c47f14" }]} height={220} />
            <DataTable rows={pva} columns={["event_id", "event_type", "event_date", "model", "method", "registrations_evaluated", "expected_attendance", "actual_attendees", "error", "percentage_error", "accuracy", "target_is_simulated"]}
              render={{ target_is_simulated: (x) => <StatusBadge value={x ? "simulated" : "recorded"} />, accuracy: (x) => pct(x, 1) }} />
          </>
        ) : <Notice kind="note">No event has both predictions and recorded attendance yet. Predict an event, hold it, then record attendance to see this comparison.</Notice>}
      </Card>
      <Card title="Event trends" subtitle={`${recordedEvents.length} event(s) with attendance recorded in the app.`}>
        <DataTable rows={data.events} rowKey="event_id" columns={["event_id", "name", "event_type", "event_date", "status", "data_source", "active_registrations", "recorded_attended", "attendance_rate", "expected_attendance_active_model", "error", "target_is_simulated"]}
          render={{ status: (x) => <StatusBadge value={x} />, data_source: srcLabel, attendance_rate: (x) => pct(x, 1), target_is_simulated: (x) => (x == null ? "" : <StatusBadge value={x ? "simulated" : "recorded"} />) }} />
      </Card>
    </>
  );
}
