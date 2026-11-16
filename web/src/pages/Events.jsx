import { useState } from "react";
import { api, download } from "../api.js";
import { go } from "../nav.js";
import { DAYS, EVENT_STATUS, EVENT_TYPES, SLOTS, dayOf } from "../lib/options.js";
import { Button, Card, DataTable, Empty, ErrorState, Field, Grid, Kpi, Loading, Notice, PageHead, Select, StatusBadge, SIM_NOTE, num, pct, srcLabel, toast, useApi } from "../components/ui.jsx";

const BLANK = { event_day: "", name: "", event_type: "Technical", event_date: "", start_time: "", time_slot: "Morning", duration_hours: 2, venue: "", capacity: "", popularity: "", registration_deadline: "" };

function EventForm({ initial, onSaved, onCancel }) {
  const editing = !!initial?.event_id;
  const [f, setF] = useState(() => ({ ...BLANK, ...Object.fromEntries(Object.entries(initial || {}).map(([k, v]) => [k, v ?? ""])) }));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e?.target ? e.target.value : e });
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const body = {
      name: f.name || null, event_type: f.event_type, event_date: f.event_date || null, start_time: f.start_time || null,
      time_slot: f.time_slot, duration_hours: Number(f.duration_hours), venue: f.venue || null,
      capacity: f.capacity === "" ? null : Number(f.capacity), popularity: f.popularity === "" ? null : Number(f.popularity),
      registration_deadline: f.registration_deadline || null,
    };
    if (!editing) body.event_day = f.event_date ? dayOf(f.event_date) : f.event_day || null;
    try {
      const ev = editing ? await api(`/api/events/${initial.event_id}`, { method: "PATCH", body }) : await api("/api/events", { method: "POST", body });
      toast(editing ? `Saved ${ev.event_id}` : `Created ${ev.event_id}`);
      onSaved(ev);
    } catch (x) {
      setErr(x);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title={editing ? `Edit ${initial.event_id}` : "New event"} subtitle="Stored in the database with source 'Entered in app'.">
      <form className="form" onSubmit={submit}>
        <Field label="Name" wide><input value={f.name} onChange={set("name")} maxLength={120} placeholder="e.g. Hackathon kickoff" /></Field>
        <Field label="Type"><Select value={f.event_type} onChange={set("event_type")} options={EVENT_TYPES} /></Field>
        <Field label="Date" hint={f.event_date ? dayOf(f.event_date) : "optional"}><input type="date" value={f.event_date} onChange={set("event_date")} /></Field>
        {!editing && !f.event_date && <Field label="Planned day" hint="required when there is no date"><Select value={f.event_day} onChange={set("event_day")} options={DAYS} placeholder="Choose…" required /></Field>}
        <Field label="Start time"><input type="time" value={f.start_time} onChange={set("start_time")} /></Field>
        <Field label="Time slot"><Select value={f.time_slot} onChange={set("time_slot")} options={SLOTS} /></Field>
        <Field label="Duration (hours)"><input type="number" min="0.5" max="12" step="0.5" required value={f.duration_hours} onChange={set("duration_hours")} /></Field>
        <Field label="Venue"><input value={f.venue} onChange={set("venue")} maxLength={120} /></Field>
        <Field label="Capacity"><input type="number" min="1" value={f.capacity} onChange={set("capacity")} /></Field>
        <Field label="Popularity (0–100)" hint="model input; leave blank if unknown"><input type="number" min="0" max="100" value={f.popularity} onChange={set("popularity")} /></Field>
        <Field label="Registration deadline"><input type="date" value={f.registration_deadline} onChange={set("registration_deadline")} /></Field>
        <div className="row wide">
          <Button type="submit" busy={busy}>{editing ? "Save changes" : "Create event"}</Button>
          <Button kind="ghost" type="button" onClick={onCancel}>Cancel</Button>
        </div>
      </form>
      <ErrorState error={err} />
    </Card>
  );
}

function EventDetail({ id, onChanged }) {
  const { data, error, loading, reload } = useApi(`/api/events/${id}`);
  const [editing, setEditing] = useState(false);
  const [student, setStudent] = useState("");
  const [days, setDays] = useState("");
  const [exam, setExam] = useState(0);
  const [head, setHead] = useState("");
  const [walk, setWalk] = useState(0);
  const [pred, setPred] = useState(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState(null);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const ev = data.event, h = data.history;
  const act = async (name, fn) => {
    setBusy(name);
    setErr(null);
    try {
      await fn();
      reload();
      onChanged();
    } catch (x) {
      setErr(x);
    } finally {
      setBusy("");
    }
  };
  if (editing) return <EventForm initial={ev} onSaved={() => { setEditing(false); reload(); onChanged(); }} onCancel={() => setEditing(false)} />;
  const active = data.registrations.filter((r) => r.status === "Registered");
  return (
    <div className="stack">
      <Card eyebrow={`${ev.event_id} · ${srcLabel(ev.data_source)}`} title={ev.name || ev.event_id}
        actions={<><StatusBadge value={ev.status} /><Button kind="ghost" onClick={() => setEditing(true)}>Edit</Button></>}>
        <dl className="info">
          <dt>Type</dt><dd>{ev.event_type}</dd>
          <dt>When</dt><dd>{ev.event_date ? `${ev.event_date} (${ev.event_day})` : "No date"} · {ev.time_slot}{ev.start_time ? ` · ${ev.start_time}` : ""} · {ev.duration_hours} h</dd>
          <dt>Venue / capacity</dt><dd>{ev.venue || "—"} · {ev.capacity ?? "no limit"}</dd>
          <dt>Phase</dt><dd>{ev.phase}</dd>
          <dt>History</dt><dd>{h.registrations} registrations · {h.predictions} predictions · {h.attendance_records} attendance records · {h.evaluations} evaluations</dd>
        </dl>
        {data.simulated_outcomes && <Notice kind="info">{SIM_NOTE}</Notice>}
        <div className="row" style={{ marginTop: 12 }}>
          <span className="small muted">Set status:</span>
          {EVENT_STATUS.filter((s) => s !== ev.status).map((s) => (
            <Button key={s} kind={s === "Cancelled" ? "danger" : "ghost"} busy={busy === s} onClick={() => act(s, () => api(`/api/events/${id}`, { method: "PATCH", body: { status: s } }))}>{s}</Button>
          ))}
          <span className="spacer" />
          <Button kind="plain" onClick={() => go("attendance", id)}>Attendance & QR</Button>
          {!Object.values(h).some(Boolean) && (
            <Button kind="danger" busy={busy === "del"} onClick={() => window.confirm(`Delete ${id}? It has no history.`) && act("del", async () => { await api(`/api/events/${id}`, { method: "DELETE" }); toast(`Deleted ${id}`); go("events"); })}>Delete</Button>
          )}
        </div>
        <ErrorState error={err} />
      </Card>

      <div className="pair">
        <Card title="Register a student" subtitle="Students are pseudonymous IDs from the database.">
          <form className="form" onSubmit={(e) => { e.preventDefault(); act("reg", async () => { await api(`/api/events/${id}/registrations`, { method: "POST", body: { student_id: student.trim(), registration_days_before: days === "" ? null : Number(days), has_exam_near_event: Number(exam) } }); toast(`Registered ${student}`); setStudent(""); }); }}>
            <Field label="Student ID"><input required value={student} onChange={(e) => setStudent(e.target.value)} placeholder="STU001" /></Field>
            <Field label="Days before event"><input type="number" min="0" max="365" value={days} onChange={(e) => setDays(e.target.value)} /></Field>
            <Field label="Exam near event"><Select value={String(exam)} onChange={setExam} options={[{ value: "0", label: "No" }, { value: "1", label: "Yes" }]} /></Field>
            <div className="row wide"><Button type="submit" busy={busy === "reg"}>Register</Button></div>
          </form>
        </Card>
        <Card title="Predict attendance" subtitle={`Scores the ${active.length} active registration(s) with the active model and stores the run.`}>
          <Button disabled={!active.length} busy={busy === "pred"} onClick={() => act("pred", async () => setPred(await api(`/api/events/${id}/predict`, { method: "POST", body: {} })))}>Run prediction</Button>
          {pred && (
            <Grid cols={3} className="mt">
              <Kpi label="Expected attendees" value={num(pred.summary.expected_attendance, 1)} caption="sum of probabilities" />
              <Kpi label="Predicted to attend" value={pred.summary.predicted_attendees ?? "—"} tone="good" caption={`threshold ${pred.summary.threshold}`} />
              <Kpi label="Registrations scored" value={pred.summary.registrations ?? pred.rows.length} tone="blue" />
            </Grid>
          )}
          {pred && <DataTable rows={pred.rows} pageSize={8} render={{ probability: (v) => pct(v, 1), predicted_attend: (v) => (v ? "Attend" : "No-show") }} />}
        </Card>
      </div>

      <Card title="Registrations" actions={<Button kind="ghost" onClick={() => download(`/api/export/registrations?fmt=csv&event_id=${id}`)}>Export CSV</Button>}>
        <DataTable rows={data.registrations} columns={["registration_id", "student_id", "department", "course", "year", "status", "registration_days_before", "has_exam_near_event", "attendance", "data_source"]}
          render={{ status: (v) => <StatusBadge value={v} />, data_source: srcLabel, attendance: (v) => <StatusBadge value={v} /> }} empty="No registrations yet" />
      </Card>

      <Card title="Record actual attendance" subtitle="Use a headcount when per-student check-in isn't available. Per-student attendance is recorded on the Attendance page.">
        {data.simulated_outcomes ? (
          <Notice kind="note">This event comes from the imported dataset and already carries SIMULATED outcomes, so real attendance is not recorded for it. Create a new event to record real attendance.</Notice>
        ) : data.outcome ? (
          <Notice kind="ok">Recorded: {data.outcome.actual_attendees} attendees ({data.outcome.method}{data.outcome.walk_ins ? `, ${data.outcome.walk_ins} walk-ins` : ""}) by {data.outcome.recorded_by} at {data.outcome.recorded_utc}.</Notice>
        ) : (
          <form className="form" onSubmit={(e) => { e.preventDefault(); act("head", async () => { await api(`/api/events/${id}/attendance`, { method: "POST", body: { headcount: Number(head), walk_ins: Number(walk) } }); toast("Headcount recorded"); }); }}>
            <Field label="Headcount"><input type="number" min="0" required value={head} onChange={(e) => setHead(e.target.value)} /></Field>
            <Field label="Walk-ins (unregistered)"><input type="number" min="0" value={walk} onChange={(e) => setWalk(e.target.value)} /></Field>
            <div className="row wide"><Button type="submit" busy={busy === "head"}>Record headcount</Button></div>
          </form>
        )}
        {data.evaluations?.length > 0 && <DataTable rows={data.evaluations} columns={["model_version_id", "method", "expected_attendance", "actual_attendees", "error", "accuracy", "roc_auc", "target_is_simulated"]} search={false} />}
      </Card>
    </div>
  );
}

export default function Events({ arg }) {
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useApi(`/api/events${status ? `?status=${status}` : ""}`);
  const [creating, setCreating] = useState(false);
  return (
    <>
      <PageHead title="Event Management" subtitle="Create, edit, schedule and close events; register students; run predictions; record outcomes.">
        <Select value={status} onChange={setStatus} options={EVENT_STATUS} placeholder="All statuses" aria-label="Status filter" />
        <Button kind="ghost" onClick={() => download("/api/export/events?fmt=xlsx")}>Export Excel</Button>
        <Button onClick={() => { setCreating(true); go("events"); }}>New event</Button>
      </PageHead>
      <ErrorState error={error} onRetry={reload} />
      <div className="split split-list">
        <Card title="Events" tight>
          {loading && !data ? <Loading /> : (
            <DataTable rows={data || []} rowKey="event_id" selected={arg} onRow={(r) => { setCreating(false); go("events", r.event_id); }}
              columns={["event_id", "name", "event_type", "event_date", "status", "active_registrations"]}
              render={{ status: (v) => <StatusBadge value={v} />, name: (v, r) => v || <span className="muted">{r.event_type}</span> }} empty="No events" />
          )}
        </Card>
        <div>
          {creating ? <EventForm onSaved={(ev) => { setCreating(false); reload(); go("events", ev.event_id); }} onCancel={() => setCreating(false)} />
            : arg ? <EventDetail key={arg} id={arg} onChanged={reload} />
              : <Card><Empty title="Select an event">Pick an event on the left, or create a new one.</Empty></Card>}
        </div>
      </div>
    </>
  );
}
