import { useEffect, useRef, useState } from "react";
import { api, qs } from "../api.js";
import { go } from "../nav.js";
import { COURSES, DAYS, EVENT_TYPES, EXPERIENCE, INTERESTS, ROLES, SLOTS, WEATHER } from "../lib/options.js";
import { Columns } from "../components/charts.jsx";
import { Button, Card, DataTable, ErrorState, Field, Grid, Kpi, Notice, PageHead, Select, StatusBadge, Tabs, num, pct, toast, useApi } from "../components/ui.jsx";

const TABS = ["Individual", "Whole event", "Bulk file", "What-if planner"];
const TAB_KEYS = { individual: "Individual", event: "Whole event", bulk: "Bulk file", whatif: "What-if planner" };

const DEFAULT = {
  Course: "B.Tech CSE", Year: 2, Currently_UBA_Volunteer: 0, Participated_UBA_Event_Before: 0, Experience_Level: "Some experience",
  Area_Of_Interest: "Problem Solving & Decision Making", Role: "Did not apply", Event_Type: "Technical", Event_Day: "Wednesday",
  Event_Time: "Afternoon", Event_Duration_Hours: 2, Event_Popularity: "", Weather_Forecast: "Unavailable", Registration_Days_Before: 7,
  Previous_Event_Attendance: 1, Previous_Events_Registered: "", Previous_Attendance_Rate: "", Academic_Engagement_Score: "",
  Distance_From_Venue_KM: "", Has_Exam_Near_Event: 0,
};
const INT = ["Year", "Currently_UBA_Volunteer", "Participated_UBA_Event_Before", "Registration_Days_Before", "Previous_Event_Attendance", "Previous_Events_Registered", "Has_Exam_Near_Event"];
const FLOAT = ["Event_Duration_Hours", "Event_Popularity", "Previous_Attendance_Rate", "Academic_Engagement_Score", "Distance_From_Venue_KM"];

function ModelSelect({ value, onChange }) {
  const m = useApi("/api/models");
  const opts = (m.data?.versions || []).filter((v) => v.supported).map((v) => ({ value: String(v.model_version_id), label: `${v.model} v${v.version}${v.active ? " (active)" : ""}` }));
  return <Select value={value} onChange={onChange} options={opts} placeholder="Active model" aria-label="Model" />;
}

function Probability({ p, threshold }) {
  return (
    <div className="probbar" role="img" aria-label={`Probability ${pct(p, 1)}, threshold ${pct(threshold)}`}>
      <span style={{ width: `${p * 100}%` }} />
      <i style={{ left: `${threshold * 100}%` }} title={`threshold ${threshold}`} />
    </div>
  );
}

function Individual({ onDone }) {
  const [f, setF] = useState(DEFAULT);
  const [ids, setIds] = useState({ student: "", event: "" });
  const [model, setModel] = useState("");
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState("");
  const out = useRef(null);
  useEffect(() => { if (res && window.innerWidth < 1180) out.current?.scrollIntoView({ behavior: "smooth" }); }, [res]);
  const set = (k) => (e) => setF({ ...f, [k]: e?.target ? e.target.value : e });
  const prefill = async () => {
    setBusy("fill");
    setErr(null);
    try {
      const r = await api(`/api/registration-features${qs({ student_id: ids.student.trim(), event_id: ids.event.trim() })}`);
      setF(Object.fromEntries(Object.keys(DEFAULT).map((k) => [k, r[k] ?? ""])));
      toast("Filled from the stored registration");
    } catch (e) {
      setErr(e);
    } finally {
      setBusy("");
    }
  };
  const submit = async (e) => {
    e.preventDefault();
    const body = {};
    for (const [k, v] of Object.entries(f)) {
      if (v === "" || v === null) continue;
      body[k] = INT.includes(k) ? parseInt(v, 10) : FLOAT.includes(k) ? parseFloat(v) : v;
    }
    if (ids.student.trim()) body.Student_ID = ids.student.trim();
    if (ids.event.trim()) body.Event_ID = ids.event.trim();
    if (model) body.model_version_id = Number(model);
    setBusy("pred");
    setErr(null);
    try {
      setRes(await api("/api/predict", { method: "POST", body }));
      onDone?.();
    } catch (x) {
      setErr(x);
      setRes(null);
    } finally {
      setBusy("");
    }
  };
  const sel = (k, opts) => <Field label={k.replace(/_/g, " ")}><Select value={f[k]} onChange={set(k)} options={opts} /></Field>;
  const numF = (k, p = {}) => <Field label={k.replace(/_/g, " ")} hint={p.hint}><input type="number" value={f[k]} onChange={set(k)} required={p.required} min={p.min} max={p.max} step={p.step || "any"} /></Field>;
  const yn = (k) => <Field label={k.replace(/_/g, " ")}><Select value={String(f[k])} onChange={set(k)} options={[{ value: "0", label: "No" }, { value: "1", label: "Yes" }]} /></Field>;
  return (
    <div className="split">
      <Card title="Registration details" subtitle="Fill in manually, or load the inputs of a stored registration.">
        <div className="row" style={{ marginBottom: 12 }}>
          <input placeholder="Student ID (optional)" value={ids.student} onChange={(e) => setIds({ ...ids, student: e.target.value })} aria-label="Student ID" />
          <input placeholder="Event ID (optional)" value={ids.event} onChange={(e) => setIds({ ...ids, event: e.target.value })} aria-label="Event ID" />
          <Button kind="ghost" disabled={!ids.student || !ids.event} busy={busy === "fill"} onClick={prefill}>Load registration</Button>
        </div>
        <form className="form" onSubmit={submit}>
          {sel("Course", COURSES)}
          {numF("Year", { required: true, min: 1, max: 5, step: 1 })}
          {sel("Experience_Level", EXPERIENCE)}
          {sel("Area_Of_Interest", INTERESTS)}
          {sel("Role", ROLES)}
          {yn("Currently_UBA_Volunteer")}
          {yn("Participated_UBA_Event_Before")}
          {sel("Event_Type", EVENT_TYPES)}
          {sel("Event_Day", DAYS)}
          {sel("Event_Time", SLOTS)}
          {numF("Event_Duration_Hours", { required: true, min: 0.5, max: 12 })}
          {numF("Event_Popularity", { min: 0, max: 100, hint: "optional" })}
          {sel("Weather_Forecast", WEATHER)}
          {numF("Registration_Days_Before", { required: true, min: 0, max: 365, step: 1 })}
          {numF("Previous_Event_Attendance", { required: true, min: 0, max: 500, step: 1 })}
          {numF("Previous_Events_Registered", { min: 0, max: 500, step: 1, hint: "optional" })}
          {numF("Previous_Attendance_Rate", { min: 0, max: 100, hint: "optional, %" })}
          {numF("Academic_Engagement_Score", { min: 0, max: 100, hint: "optional" })}
          {numF("Distance_From_Venue_KM", { min: 0, max: 200, hint: "optional" })}
          {yn("Has_Exam_Near_Event")}
          <div className="row wide">
            <ModelSelect value={model} onChange={setModel} />
            <Button type="submit" busy={busy === "pred"}>Predict</Button>
            <Button kind="plain" type="button" onClick={() => { setF(DEFAULT); setRes(null); }}>Reset</Button>
          </div>
        </form>
        <ErrorState error={err} />
      </Card>
      <div className="stack" ref={out}>
        {!res ? (
          <Card title="Result"><p className="muted">Submit the form to get a prediction from the trained model. Each prediction is stored in the history with the model version and inputs.</p></Card>
        ) : (
          <>
            <Card eyebrow={`${res.model} · prediction #${res.prediction_id}`} title={res.predicted_attend ? "Likely to attend" : "At risk of not attending"}>
              <p className="big">{pct(res.probability, 1)}</p>
              <Probability p={res.probability} threshold={res.threshold} />
              <p className="muted small">Decision threshold {res.threshold} · certainty <StatusBadge value={res.certainty} /> · forecast used: {res.forecast_used}</p>
              <Notice kind="info">{res.notice}</Notice>
            </Card>
            <Card title="What moved this prediction" subtitle="Change in probability when each input is set to its typical training value (local sensitivity, not causation).">
              {res.factors.map((x) => (
                <div key={x.feature} className={`factor ${x.effect >= 0 ? "up" : "down"}`}>
                  <span>{x.label}: <b>{String(x.value)}</b> <span className="muted small">(typical {String(x.typical)})</span></span>
                  <span>{x.effect >= 0 ? "+" : ""}{(x.effect * 100).toFixed(1)} pts</span>
                </div>
              ))}
            </Card>
            <Card title="Organiser tips">
              <ul className="tips">{res.tips.map((t) => <li key={t}>{t}</li>)}</ul>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}

function Summary({ s }) {
  return (
    <>
      <Grid cols={4}>
        <Kpi label="Registrations scored" value={num(s.registrations)} tone="blue" />
        <Kpi label="Expected attendance" value={num(s.expected_attendance, 1)} caption={s.expected_attendance_range_95 ? `95% range ${s.expected_attendance_range_95[0]}–${s.expected_attendance_range_95[1]}` : ""} />
        <Kpi label="Predicted to attend" value={num(s.predicted_attendees)} caption={`threshold ${s.threshold}`} tone="good" />
        <Kpi label="Predicted no-shows" value={num(s.predicted_non_attendees)} caption={`mean probability ${pct(s.mean_probability, 1)}`} tone="gold" />
      </Grid>
      {s.histogram && (
        <Card title="Probability distribution" subtitle="Count of registrations per probability band.">
          <Columns categories={s.histogram.map((_, i) => `${i * 10}–${i * 10 + 10}%`)} series={[{ name: "Registrations", values: s.histogram }]} height={200} />
        </Card>
      )}
      {s.forecast_unavailable_rows > 0 && <Notice kind="note">{s.forecast_unavailable_rows} row(s) had no weather forecast; the model used the 'Unavailable' category.</Notice>}
    </>
  );
}

function WholeEvent({ onDone }) {
  const ev = useApi("/api/events");
  const [id, setId] = useState("");
  const [model, setModel] = useState("");
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      setRes(await api(`/api/events/${id}/predict`, { method: "POST", body: { model_version_id: model ? Number(model) : null } }));
      onDone?.();
    } catch (e) {
      setErr(e);
      setRes(null);
    } finally {
      setBusy(false);
    }
  };
  const withRegs = (ev.data || []).filter((e) => e.active_registrations > 0);
  return (
    <div className="stack">
      <Card title="Predict every active registration of an event" subtitle="The run and each prediction are stored with the model version.">
        <div className="row">
          <Select value={id} onChange={setId} placeholder="Choose an event…" aria-label="Event" options={withRegs.map((e) => ({ value: e.event_id, label: `${e.event_id} · ${e.name || e.event_type} · ${e.active_registrations} registrations` }))} />
          <ModelSelect value={model} onChange={setModel} />
          <Button disabled={!id} busy={busy} onClick={run}>Predict event</Button>
        </div>
        <ErrorState error={err || ev.error} />
      </Card>
      {res && (
        <>
          <Summary s={res.summary} />
          <Card title="Per-student probabilities" actions={<Button kind="plain" onClick={() => go("events", id)}>Open event</Button>}>
            <DataTable rows={res.rows} render={{ probability: (v) => pct(v, 1), predicted_attend: (v) => (v ? "Attend" : "No-show"), certainty: (v) => <StatusBadge value={v} /> }} />
          </Card>
        </>
      )}
    </div>
  );
}

function Bulk({ onDone }) {
  const [file, setFile] = useState(null);
  const [model, setModel] = useState("");
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    const form = new FormData();
    form.append("file", file);
    setBusy(true);
    setErr(null);
    try {
      setRes(await api(`/api/predict/file${qs({ model_version_id: model })}`, { method: "POST", form }));
      onDone?.();
    } catch (e) {
      setErr(e);
      setRes(null);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="stack">
      <Card title="Bulk prediction from a file" subtitle="CSV or Excel with the model input columns (see API docs). Personal-data columns are refused. Every row is stored in the prediction history.">
        <div className="row">
          <input type="file" accept=".csv,.xlsx" onChange={(e) => setFile(e.target.files[0] || null)} aria-label="Prediction file" />
          <ModelSelect value={model} onChange={setModel} />
          <Button disabled={!file} busy={busy} onClick={run}>Predict file</Button>
        </div>
        <ErrorState error={err} />
      </Card>
      {res && (
        <>
          <Summary s={res.summary} />
          <Card title={`Predictions (${num(res.total)} rows${res.total > 1000 ? ", first 1,000 shown" : ""})`}>
            <DataTable rows={res.rows} maxCols={12} />
          </Card>
        </>
      )}
    </div>
  );
}

function WhatIf() {
  const ev = useApi("/api/events");
  const [id, setId] = useState("");
  const [ch, setCh] = useState({});
  const [regs, setRegs] = useState("");
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (v) => setCh((c) => { const n = { ...c }; if (v === "" || v === undefined) delete n[k]; else n[k] = v; return n; });
  const run = async () => {
    const changes = Object.fromEntries(Object.entries(ch).map(([k, v]) => [k, ["Event_Duration_Hours", "Event_Popularity"].includes(k) ? parseFloat(v) : k === "Has_Exam_Near_Event" ? parseInt(v, 10) : v]));
    setBusy(true);
    setErr(null);
    try {
      setRes(await api("/api/whatif", { method: "POST", body: { event_id: id, changes, registrations: regs ? Number(regs) : null } }));
    } catch (e) {
      setErr(e);
      setRes(null);
    } finally {
      setBusy(false);
    }
  };
  const withRegs = (ev.data || []).filter((e) => e.active_registrations > 0);
  const row = (label, k) => ({ label, plan: res.original[k], scenario: res.modified[k], change: res.original[k] == null || res.modified[k] == null ? null : res.modified[k] - res.original[k] });
  return (
    <div className="stack">
      <Card title="What-if planner" subtitle="Change event-level inputs and compare the model's expectation with the current plan. This is a model scenario — nothing is stored and it is not a forecast of real behaviour.">
        <div className="form">
          <Field label="Event" wide><Select value={id} onChange={(v) => { setId(v); setRes(null); }} placeholder="Choose an event with registrations…" options={withRegs.map((e) => ({ value: e.event_id, label: `${e.event_id} · ${e.name || e.event_type} (${e.event_type}, ${e.event_day || "no day"}, ${e.time_slot}) · ${e.active_registrations} registrations` }))} /></Field>
          <Field label="Event type"><Select value={ch.Event_Type || ""} onChange={set("Event_Type")} placeholder="unchanged" options={EVENT_TYPES} /></Field>
          <Field label="Day"><Select value={ch.Event_Day || ""} onChange={set("Event_Day")} placeholder="unchanged" options={DAYS} /></Field>
          <Field label="Time slot"><Select value={ch.Event_Time || ""} onChange={set("Event_Time")} placeholder="unchanged" options={SLOTS} /></Field>
          <Field label="Duration (h)"><input type="number" min="0.5" max="12" step="0.5" placeholder="unchanged" value={ch.Event_Duration_Hours || ""} onChange={(e) => set("Event_Duration_Hours")(e.target.value)} /></Field>
          <Field label="Popularity"><input type="number" min="0" max="100" placeholder="unchanged" value={ch.Event_Popularity || ""} onChange={(e) => set("Event_Popularity")(e.target.value)} /></Field>
          <Field label="Weather"><Select value={ch.Weather_Forecast || ""} onChange={set("Weather_Forecast")} placeholder="unchanged" options={WEATHER} /></Field>
          <Field label="Exam near event"><Select value={ch.Has_Exam_Near_Event ?? ""} onChange={set("Has_Exam_Near_Event")} placeholder="unchanged" options={[{ value: "0", label: "No" }, { value: "1", label: "Yes" }]} /></Field>
          <Field label="Registrations" hint="scale to this many (optional)"><input type="number" min="1" max="5000" value={regs} onChange={(e) => setRegs(e.target.value)} /></Field>
          <div className="row wide"><Button disabled={!id} busy={busy} onClick={run}>Compare scenario</Button></div>
        </div>
        <ErrorState error={err || ev.error} />
      </Card>
      {res && (
        <Card title={`Scenario for ${res.event_id}`} subtitle={res.model}>
          <Grid cols={3}>
            <Kpi label="Expected attendees — plan" value={num(res.original.expected_attendees, 1)} caption={`${res.original.registrations} registrations`} tone="blue" />
            <Kpi label="Expected attendees — scenario" value={num(res.modified.expected_attendees, 1)} caption={`range ${num(res.modified.low, 1)}–${num(res.modified.high, 1)}`} />
            <Kpi label="Difference" value={`${res.difference >= 0 ? "+" : ""}${num(res.difference, 1)}`} tone={res.difference >= 0 ? "good" : "critical"} caption={`mean probability ${pct(res.original.mean_probability, 1)} → ${pct(res.modified.mean_probability, 1)}`} />
          </Grid>
          <DataTable search={false} rows={[row("Registrations", "registrations"), row("Expected attendees", "expected_attendees"), row("Expected no-shows", "expected_no_shows"), row("Predicted to attend", "predicted_to_attend"), row("Mean probability", "mean_probability")]} />
          {res.notes.map((n) => <Notice key={n} kind="note">{n}</Notice>)}
        </Card>
      )}
    </div>
  );
}

export default function Prediction({ arg }) {
  const tab = TAB_KEYS[arg] || "Individual";
  const setTab = (t) => go("prediction", Object.keys(TAB_KEYS).find((k) => TAB_KEYS[k] === t));
  const hist = useApi("/api/predictions?limit=50");
  return (
    <>
      <PageHead title="Attendance Prediction" subtitle="Real predictions from the trained scikit-learn pipeline. Results are stored in the prediction history.">
        <Tabs tabs={TABS} value={tab} onChange={setTab} />
      </PageHead>
      {tab === "Individual" && <Individual onDone={hist.reload} />}
      {tab === "Whole event" && <WholeEvent onDone={hist.reload} />}
      {tab === "Bulk file" && <Bulk onDone={hist.reload} />}
      {tab === "What-if planner" && <WhatIf />}
      <Card title="Recent prediction history" actions={<Button kind="plain" onClick={hist.reload}>Refresh</Button>}>
        <ErrorState error={hist.error} />
        <DataTable rows={hist.data || []} pageSize={10} render={{ probability: (v) => pct(v, 1), outcome: (v) => <StatusBadge value={v} />, certainty: (v) => <StatusBadge value={v} /> }} empty="No predictions stored yet" />
      </Card>
    </>
  );
}
