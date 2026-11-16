import { useAuth } from "../auth.jsx";
import { Badge, Card, DataTable, ErrorState, Grid, Kpi, Loading, Notice, PageHead, SIM_NOTE, StatusBadge, num, pct, useApi } from "../components/ui.jsx";

const LINK = { verified: ["good", "Student ID verified"], pending: ["gold", "Waiting for verification"], rejected: ["critical", "Student ID not confirmed"] };
export const LinkBadge = ({ status }) => <Badge tone={LINK[status]?.[0] || "neutral"}>{LINK[status]?.[1] || status}</Badge>;

const sourceBadge = (r) => <Badge tone={r.simulated ? "gold" : "good"}>{r.simulated ? "Simulated (dataset)" : r.attended == null ? "Checked in" : "Recorded"}</Badge>;

// ---------------------------------------------------------------- shared views (also used by the admin's student detail)
export function RecordView({ record }) {
  if (!record) return null;
  return (
    <dl className="info">
      <dt>Student ID</dt><dd>{record.student_id}</dd>
      <dt>Course</dt><dd>{record.course} · {record.department}</dd>
      <dt>Year</dt><dd>{record.year}</dd>
      <dt>Experience</dt><dd>{record.experience_level}</dd>
      <dt>Area of interest</dt><dd>{record.area_of_interest}</dd>
      <dt>Events attended before joining</dt><dd>{record.previous_event_attendance}{record.previous_events_registered != null ? ` of ${record.previous_events_registered}` : ""}</dd>
    </dl>
  );
}

export function SummaryKpis({ s }) {
  if (!s) return null;
  return (
    <Grid cols={4}>
      <Kpi label="Registrations" value={num(s.registrations)} caption={`${s.active_registrations} active`} />
      <Kpi label="Upcoming events" value={num(s.upcoming_events)} tone="blue" />
      <Kpi label="Recorded attendance" value={s.recorded_outcomes ? pct(s.recorded_attendance_rate) : "—"} caption={s.recorded_outcomes ? `${s.recorded_attended} of ${s.recorded_outcomes} recorded events` : "no attendance recorded in the app yet"} tone="good" />
      <Kpi label="Dataset outcomes" value={s.simulated_outcomes ? pct(s.simulated_attendance_rate) : "—"} caption={s.simulated_outcomes ? `${s.simulated_outcomes} SIMULATED outcomes` : "none"} tone="gold" hint={SIM_NOTE} />
    </Grid>
  );
}

export const RegistrationsTable = ({ rows }) => (
  <DataTable rows={rows} rowKey="registration_id" columns={["event_id", "event_name", "event_type", "event_date", "time_slot", "venue", "event_status", "status"]}
    render={{ status: (v) => <StatusBadge value={v} />, event_status: (v) => <StatusBadge value={v} /> }} empty="No registrations" />
);

export const AttendanceTable = ({ rows }) => (
  <DataTable rows={rows} columns={["event_id", "event_name", "event_date", "outcome", "checkin_method", "checked_in_utc", "source"]}
    render={{ outcome: (v) => <StatusBadge value={v === "Attended" ? "Attended" : v === "Absent" ? "Absent" : v} />, source: (_, r) => sourceBadge(r) }} empty="No attendance recorded" />
);

export const PredictionsTable = ({ rows }) => (
  <DataTable rows={rows} rowKey="prediction_id" columns={["predicted_utc", "event_id", "event_name", "probability", "predicted_attend", "certainty", "model"]}
    render={{ probability: (v) => pct(v, 1), predicted_attend: (v) => (v ? "Likely to attend" : "At risk of missing"), certainty: (v) => <StatusBadge value={v} /> }} empty="No predictions about you yet" />
);

// ---------------------------------------------------------------- student pages
function useStudent(path) {
  const r = useApi(path);
  const gate = r.loading && !r.data ? <Loading /> : r.error ? <ErrorState error={r.error} onRetry={r.reload} /> : null;
  return { ...r, gate };
}

function LinkNotice({ data }) {
  return data && data.notice ? <Notice kind="note">{data.notice}</Notice> : null;
}

export function StudentHome() {
  const { user } = useAuth();
  const prof = useStudent("/api/student/profile");
  const sum = useStudent("/api/student/summary");
  if (prof.gate) return prof.gate;
  const s = sum.data?.summary;
  return (
    <>
      <PageHead title={`Hello, ${user.full_name.split(" ")[0]}`} subtitle="Your events, attendance and predictions - visible only to you and the organiser." />
      <LinkNotice data={prof.data} />
      {sum.gate || <SummaryKpis s={s} />}
      {s?.latest_prediction && (
        <Notice kind="info">
          Latest prediction ({s.latest_prediction.event_id || "individual"}): {pct(s.latest_prediction.probability)} chance of attending, from {s.latest_prediction.model}. Predictions are estimates from a model trained on simulated data - not a judgement of you.
        </Notice>
      )}
      <Grid cols={2}>
        <Card title="Your account">
          <dl className="info">
            <dt>Name</dt><dd>{user.full_name}</dd>
            <dt>E-mail</dt><dd>{user.email}</dd>
            <dt>Student ID</dt><dd>{user.student_id || "—"}</dd>
            <dt>Status</dt><dd><LinkBadge status={user.link_status} /></dd>
          </dl>
        </Card>
        <Card title="Your student record" subtitle={prof.data.record ? "As held by the organiser" : ""}>
          {prof.data.record ? <RecordView record={prof.data.record} /> : <p className="muted">Your record is shown here once your Student ID is verified.</p>}
        </Card>
      </Grid>
    </>
  );
}

export function StudentRegistrations() {
  const r = useStudent("/api/student/registrations");
  if (r.gate) return r.gate;
  return (
    <>
      <PageHead title="My registrations" subtitle="Events you are registered for." />
      <LinkNotice data={r.data} />
      <Card tight><RegistrationsTable rows={r.data.rows} /></Card>
    </>
  );
}

export function StudentAttendance() {
  const r = useStudent("/api/student/attendance");
  if (r.gate) return r.gate;
  const sim = r.data.rows.some((x) => x.simulated);
  return (
    <>
      <PageHead title="My attendance" subtitle="Attendance recorded by the organiser or by QR / desk check-in." />
      <LinkNotice data={r.data} />
      {sim && <Notice kind="info">Rows marked “Simulated (dataset)” come from the project dataset and are not observed attendance.</Notice>}
      <Card tight><AttendanceTable rows={r.data.rows} /></Card>
    </>
  );
}

export function StudentPredictions() {
  const r = useStudent("/api/student/predictions");
  if (r.gate) return r.gate;
  return (
    <>
      <PageHead title="My predictions" subtitle="Attendance predictions the organiser made for your registrations." />
      <LinkNotice data={r.data} />
      <Notice kind="info">These are estimates from a model trained on SIMULATED attendance data. They help organisers plan; they are not a judgement of you.</Notice>
      <Card tight><PredictionsTable rows={r.data.rows} /></Card>
    </>
  );
}
