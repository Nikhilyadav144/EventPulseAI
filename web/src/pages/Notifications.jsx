import { useState } from "react";
import { api } from "../api.js";
import { go } from "../nav.js";
import { Badge, Button, Card, Empty, ErrorState, Loading, PageHead, Tabs, toast, useApi } from "../components/ui.jsx";

const TONE = { critical: "critical", warning: "gold", info: "blue" };

export default function Notifications() {
  const { data, error, loading, reload } = useApi("/api/notifications");
  const [tab, setTab] = useState("Active");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const dismiss = async (key) => {
    try {
      await api("/api/notifications/dismiss", { method: "POST", body: { key } });
      toast(key ? "Dismissed" : "All notifications restored");
      reload();
    } catch (e) {
      toast(e.message, "error");
    }
  };
  const items = data.filter((n) => (tab === "Active" ? !n.dismissed : n.dismissed));
  return (
    <>
      <PageHead title="Notifications" subtitle="Generated from the current data: upcoming events, deadlines, capacity, missing attendance, model and data issues.">
        <Tabs tabs={["Active", "Dismissed"]} value={tab} onChange={setTab} />
        <Button kind="ghost" onClick={() => dismiss(null)} disabled={!data.some((n) => n.dismissed)}>Restore all</Button>
      </PageHead>
      <Card tight>
        {items.length ? (
          <ul className="notes">
            {items.map((n) => (
              <li key={n.key} className={`note-item lvl-${n.level}`}>
                <div>
                  <Badge tone={TONE[n.level] || "neutral"}>{n.level}</Badge> <span className="muted small">{n.category}</span>
                  <h3>{n.title}</h3>
                  <p className="muted">{n.detail}</p>
                </div>
                <div className="row">
                  {n.event_id && <Button kind="plain" onClick={() => go("events", n.event_id)}>Open {n.event_id}</Button>}
                  {!n.dismissed && <Button kind="ghost" onClick={() => dismiss(n.key)}>Dismiss</Button>}
                </div>
              </li>
            ))}
          </ul>
        ) : <Empty title={tab === "Active" ? "You're all caught up" : "Nothing dismissed"}>{tab === "Active" ? "No notifications need attention right now." : ""}</Empty>}
      </Card>
    </>
  );
}
