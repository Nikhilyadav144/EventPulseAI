import { useEffect, useState } from "react";
import { api, download, qs } from "../api.js";
import { go } from "../nav.js";
import { Button, Card, DataTable, ErrorState, Field, Grid, Kpi, Loading, Notice, PageHead, Select, SIM_NOTE, StatusBadge, Tabs, num, srcLabel, toast, useApi } from "../components/ui.jsx";

function QrPanel({ id, hasToken, onChange }) {
  const [qr, setQr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [stamp, setStamp] = useState(Date.now());
  const [imgErr, setImgErr] = useState(false);
  useEffect(() => setQr(null), [id]);
  const create = async () => {
    setBusy(true);
    setErr(null);
    try {
      setQr(await api(`/api/attendance/${id}/qr`, { method: "POST" }));
      setStamp(Date.now());
      onChange();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const revoke = async () => {
    if (!window.confirm("Revoke the check-in code? Phones with the old link will be refused.")) return;
    await api(`/api/attendance/${id}/qr`, { method: "DELETE" });
    setQr(null);
    onChange();
    toast("Check-in code revoked");
  };
  return (
    <Card title="QR self check-in" subtitle="Students scan the code on a phone connected to the same Wi-Fi as this computer and enter their Student ID.">
      {qr || hasToken ? (
        <div className="qr-wrap">
          {imgErr ? <Notice kind="error">The QR image could not be generated on the server (is the segno package installed?).</Notice>
            : <img className="qr" src={`/api/attendance/${id}/qr.png?ts=${stamp}`} alt={`Check-in QR code for ${id}`} width="220" height="220" onError={() => setImgErr(true)} onLoad={() => setImgErr(false)} />}
          <div className="stack">
            {qr ? <code className="small">{qr.url}</code> : <Button kind="ghost" onClick={create}>Show link</Button>}
            <Notice kind="note">Works only on the local network while the server is running. Each student can check in once; only registered students are accepted.</Notice>
            <div className="row"><Button kind="danger" onClick={revoke}>Revoke code</Button></div>
          </div>
        </div>
      ) : (
        <Button busy={busy} onClick={create}>Create check-in code</Button>
      )}
      <ErrorState error={err} />
    </Card>
  );
}

function EventAttendance({ id, ev, onChanged }) {
  const { data, error, loading, reload } = useApi(`/api/attendance${qs({ event_id: id })}`);
  const [student, setStudent] = useState("");
  const [walk, setWalk] = useState(0);
  const [corr, setCorr] = useState({ registration_id: "", attended: "1", corrected_by: "", reason: "" });
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState(null);
  const act = async (name, fn, okMsg) => {
    setBusy(name);
    setErr(null);
    try {
      const r = await fn();
      if (okMsg) toast(typeof okMsg === "function" ? okMsg(r) : okMsg);
      reload();
      onChanged();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy("");
    }
  };
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const finalised = data.rows.some((r) => r.attended !== null && r.attended !== undefined);
  const present = data.rows.filter((r) => r.attended === 1).length;
  return (
    <div className="stack">
      <Grid cols={4}>
        <Kpi label="Active registrations" value={num(ev?.active_registrations)} tone="blue" />
        <Kpi label="Checked in" value={num(data.checkins.length)} caption="QR + desk, not yet final" />
        <Kpi label="Recorded present" value={finalised ? num(present) : "—"} caption={finalised ? `of ${data.rows.length} registrations` : "not finalised"} tone="good" />
        <Kpi label="Model expected" value={ev?.expected_attendance_active_model != null ? num(ev.expected_attendance_active_model, 1) : "—"} caption="active model, sum of probabilities" tone="gold" />
      </Grid>
      <ErrorState error={err} />
      <Grid cols={2}>
        <QrPanel id={id} hasToken={data.has_token} onChange={reload} />
        <Card title="Desk check-in & finalise" subtitle="Check students in at the door, then finalise: everyone checked in is recorded present, every other active registration absent. Check-in is open on the event day only.">
          <form className="row" onSubmit={(e) => { e.preventDefault(); act("desk", () => api(`/api/attendance/${id}/checkin`, { method: "POST", body: { student_id: student.trim() } }), (r) => r.message || `Checked in ${student}`).then(() => setStudent("")); }}>
            <input required placeholder="Student ID" value={student} onChange={(e) => setStudent(e.target.value)} aria-label="Student ID to check in" />
            <Button type="submit" busy={busy === "desk"}>Check in</Button>
          </form>
          <form className="row" style={{ marginTop: 14 }} onSubmit={(e) => { e.preventDefault(); window.confirm(`Finalise attendance for ${id}? This records present/absent for every active registration.`) && act("fin", () => api(`/api/attendance/${id}/finalise`, { method: "POST", body: { walk_ins: Number(walk) } }), (r) => `Finalised: ${r.present} present, ${r.marked_absent} absent`); }}>
            <Field label="Walk-ins"><input type="number" min="0" value={walk} onChange={(e) => setWalk(e.target.value)} /></Field>
            <Button type="submit" kind="ghost" busy={busy === "fin"}>Finalise attendance</Button>
          </form>
          {data.checkins.length > 0 && <DataTable rows={data.checkins} search={false} pageSize={6} />}
        </Card>
      </Grid>
      <Card title="Attendance records" actions={<Button kind="ghost" onClick={() => download(`/api/export/attendance_recorded${qs({ fmt: "csv", event_id: id })}`)}>Export CSV</Button>}>
        <DataTable rows={data.rows} rowKey="registration_id" columns={["registration_id", "student_id", "registration_status", "checkin", "checked_in_utc", "outcome", "source", "recorded_by", "recorded_utc"]}
          render={{ outcome: (v) => <StatusBadge value={v} />, source: srcLabel, registration_status: (v) => <StatusBadge value={v} /> }}
          onRow={(r) => setCorr({ ...corr, registration_id: String(r.registration_id), attended: r.attended === 1 ? "0" : "1" })} empty="No registrations" />
      </Card>
      <Grid cols={2}>
        <Card title="Correct a record" subtitle="Corrections keep the old value in an audit log. Click a row above to pick it.">
          <form className="form" onSubmit={(e) => { e.preventDefault(); act("corr", () => api("/api/attendance/correct", { method: "POST", body: { registration_id: Number(corr.registration_id), attended: Number(corr.attended), corrected_by: corr.corrected_by, reason: corr.reason } }), "Correction saved"); }}>
            <Field label="Registration ID"><input required type="number" value={corr.registration_id} onChange={(e) => setCorr({ ...corr, registration_id: e.target.value })} /></Field>
            <Field label="Attended"><Select value={corr.attended} onChange={(v) => setCorr({ ...corr, attended: v })} options={[{ value: "1", label: "Present" }, { value: "0", label: "Absent" }]} /></Field>
            <Field label="Corrected by"><input required maxLength={60} value={corr.corrected_by} onChange={(e) => setCorr({ ...corr, corrected_by: e.target.value })} /></Field>
            <Field label="Reason"><input required maxLength={300} value={corr.reason} onChange={(e) => setCorr({ ...corr, reason: e.target.value })} /></Field>
            <div className="row wide"><Button type="submit" busy={busy === "corr"}>Save correction</Button></div>
          </form>
        </Card>
        <Card title="Correction log">
          <DataTable rows={data.corrections} search={false} pageSize={6} empty="No corrections" />
        </Card>
      </Grid>
    </div>
  );
}

export default function Attendance({ arg }) {
  const [tab, setTab] = useState("Recorded attendance");
  const evs = useApi("/api/attendance/events");
  const sim = useApi(tab === "Simulated dataset outcomes" ? "/api/attendance?simulated=true" : null);
  const ev = (evs.data || []).find((e) => e.event_id === arg);
  return (
    <>
      <PageHead title="Attendance Records" subtitle="Record real attendance with QR or desk check-in, finalise it, and correct mistakes with an audit trail.">
        <Tabs tabs={["Recorded attendance", "Simulated dataset outcomes"]} value={tab} onChange={setTab} />
      </PageHead>
      {tab === "Recorded attendance" ? (
        <>
          <Card tight>
            <div className="row">
              <Select value={arg || ""} onChange={(v) => go("attendance", v)} placeholder="Choose an event…" aria-label="Event"
                options={(evs.data || []).map((e) => ({ value: e.event_id, label: `${e.event_id} · ${e.name || e.event_type} · ${e.event_date || "no date"} · ${e.status}` }))} />
              {arg && <Button kind="plain" onClick={() => go("events", arg)}>Open event</Button>}
            </div>
            <ErrorState error={evs.error} onRetry={evs.reload} />
          </Card>
          {arg ? <EventAttendance key={arg} id={arg} ev={ev} onChanged={evs.reload} /> : <Notice kind="note">Only events created or managed in the app appear here; dataset events carry SIMULATED outcomes and are shown on the other tab.</Notice>}
        </>
      ) : (
        <Card title="Simulated outcomes from the imported dataset" actions={<Button kind="ghost" onClick={() => download("/api/export/attendance_recorded?fmt=csv")}>Export recorded CSV</Button>}>
          <Notice kind="info">{SIM_NOTE}</Notice>
          {sim.loading && !sim.data ? <Loading /> : <DataTable rows={sim.data?.rows || []} columns={["event_id", "event_name", "event_date", "student_id", "registration_status", "outcome", "source"]} render={{ outcome: (v) => <StatusBadge value={v} />, source: srcLabel }} />}
          <ErrorState error={sim.error} />
        </Card>
      )}
    </>
  );
}
