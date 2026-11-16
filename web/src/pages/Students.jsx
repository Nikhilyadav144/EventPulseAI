import { useState } from "react";
import { go } from "../nav.js";
import { Card, DataTable, Empty, ErrorState, Loading, Notice, PageHead, SIM_NOTE, Tabs, num, srcLabel, useApi } from "../components/ui.jsx";
import { AttendanceTable, LinkBadge, PredictionsTable, RecordView, RegistrationsTable, SummaryKpis } from "./StudentPages.jsx";

function StudentDetail({ id }) {
  const { data, error, loading, reload } = useApi(`/api/admin/students/${encodeURIComponent(id)}`);
  const [tab, setTab] = useState("Registrations");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const a = data.account;
  return (
    <div className="stack">
      <Card eyebrow={`${srcLabel(data.profile.data_source)}`} title={data.profile.student_id}>
        <RecordView record={data.profile} />
        <p className="small muted" style={{ marginTop: 10 }}>
          Login account: {a ? <>{a.full_name} · {a.email} · <LinkBadge status={a.link_status} />{!a.is_active && " · deactivated"} · <button className="link" onClick={() => go("accounts")}>manage</button></> : "none linked"}
        </p>
      </Card>
      <SummaryKpis s={data.summary} />
      {data.summary.simulated_outcomes > 0 && <Notice kind="info">{SIM_NOTE}</Notice>}
      <Card actions={<Tabs tabs={["Registrations", "Attendance", "Predictions"]} value={tab} onChange={setTab} />} title="Records">
        {tab === "Registrations" && <RegistrationsTable rows={data.registrations} />}
        {tab === "Attendance" && <AttendanceTable rows={data.attendance} />}
        {tab === "Predictions" && <PredictionsTable rows={data.predictions} />}
      </Card>
    </div>
  );
}

export default function Students({ arg }) {
  const { data, error, loading, reload } = useApi("/api/students?limit=2000");
  return (
    <>
      <PageHead title="Students" subtitle="Every student record with their registrations, attendance and predictions. Overall performance is on the Analytics page." />
      <ErrorState error={error} onRetry={reload} />
      <div className="split split-list">
        <Card title="Student records" tight>
          {loading && !data ? <Loading /> : (
            <DataTable rows={data || []} rowKey="student_id" selected={arg} onRow={(r) => go("students", r.student_id)}
              columns={["student_id", "course", "department", "year", "previous_event_attendance"]} empty="No students" />
          )}
          {data && <p className="muted small">{num(data.length)} students</p>}
        </Card>
        <div>{arg ? <StudentDetail key={arg} id={arg} /> : <Card><Empty title="Select a student">Pick a student to see their records.</Empty></Card>}</div>
      </div>
    </>
  );
}
