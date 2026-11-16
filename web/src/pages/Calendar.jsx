import { useMemo, useState } from "react";
import { download } from "../api.js";
import { go } from "../nav.js";
import { Button, Card, DataTable, ErrorState, Loading, Notice, PageHead, StatusBadge, useApi } from "../components/ui.jsx";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export default function Calendar() {
  const { data, error, loading, reload } = useApi("/api/calendar");
  const today = iso(new Date());
  const [cursor, setCursor] = useState(() => {
    const d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), 1);
  });
  const [sel, setSel] = useState(today);
  const byDay = useMemo(() => {
    const m = {};
    (data?.events || []).forEach((e) => (m[e.event_date] = m[e.event_date] || []).push(e));
    return m;
  }, [data]);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const first = new Date(cursor);
  const start = new Date(first);
  start.setDate(1 - ((first.getDay() + 6) % 7));
  const cells = Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    return d;
  });
  const shift = (n) => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + n, 1));
  const dayEvents = byDay[sel] || [];
  const dated = Object.keys(byDay).sort();
  const jump = (dir) => {
    const ref = iso(new Date(cursor.getFullYear(), cursor.getMonth() + (dir > 0 ? 1 : 0), 1));
    const target = dir > 0 ? dated.find((d) => d >= ref) : [...dated].reverse().find((d) => d < ref);
    if (target) {
      const t = new Date(target + "T12:00:00");
      setCursor(new Date(t.getFullYear(), t.getMonth(), 1));
      setSel(target);
    }
  };
  return (
    <>
      <PageHead title="Event Calendar" subtitle="Dated events by month. Click a day to see its events; click an event to open it.">
        <Button kind="ghost" onClick={() => download("/api/export/calendar?fmt=csv")}>Export CSV</Button>
      </PageHead>
      {data.undated > 0 && <Notice kind="note">{data.undated} event(s) have no date and are not shown on the calendar.</Notice>}
      <div className="split cal-split">
        <Card title={`${MONTHS[cursor.getMonth()]} ${cursor.getFullYear()}`}
          actions={<>
            <Button kind="plain" onClick={() => jump(-1)} title="Previous month with events">« events</Button>
            <Button kind="ghost" onClick={() => shift(-1)} aria-label="Previous month">‹</Button>
            <Button kind="ghost" onClick={() => { const d = new Date(); setCursor(new Date(d.getFullYear(), d.getMonth(), 1)); setSel(today); }}>Today</Button>
            <Button kind="ghost" onClick={() => shift(1)} aria-label="Next month">›</Button>
            <Button kind="plain" onClick={() => jump(1)} title="Next month with events">events »</Button>
          </>}>
          <div className="cal">
            {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d} className="dow">{d}</div>)}
            {cells.map((d) => {
              const k = iso(d), evs = byDay[k] || [];
              return (
                <button key={k} className={`day ${d.getMonth() !== cursor.getMonth() ? "out" : ""} ${k === today ? "today" : ""} ${k === sel ? "sel" : ""} ${evs.length ? "has" : ""}`} onClick={() => setSel(k)}>
                  <span className="n">{d.getDate()}</span>
                  {evs.slice(0, 2).map((e) => <span key={e.event_id} className={`ev ${e.status}`}>{e.name || e.event_id}</span>)}
                  {evs.length > 2 && <span className="more">+{evs.length - 2} more</span>}
                </button>
              );
            })}
          </div>
        </Card>
        <Card title={new Date(sel + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" })} subtitle={`${dayEvents.length} event(s)`}>
          <DataTable rows={dayEvents} search={false} rowKey="event_id" onRow={(r) => go("events", r.event_id)}
            columns={["event_id", "name", "time_slot", "venue", "status", "phase", "registrations", "capacity"]}
            render={{ status: (v) => <StatusBadge value={v} /> }} empty="Nothing scheduled on this day" />
        </Card>
      </div>
    </>
  );
}
