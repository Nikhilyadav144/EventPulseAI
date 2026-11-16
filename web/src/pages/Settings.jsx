import { useState } from "react";
import { api } from "../api.js";
import { Badge, Button, Card, DataTable, ErrorState, Grid, Loading, PageHead, toast, useApi } from "../components/ui.jsx";

const kb = (b) => `${(b / 1024).toFixed(0)} KB`;

export default function Settings() {
  const { data, error, loading, reload } = useApi("/api/settings");
  const [busy, setBusy] = useState(false);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const backup = async () => {
    setBusy(true);
    try {
      const b = await api("/api/settings/backup", { method: "POST" });
      toast(`Backup created (${kb(b.size_bytes)})`);
      reload();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };
  const { app, database: db, model } = data;
  return (
    <>
      <PageHead title="Settings" subtitle="Application, database and privacy information." />
      <Grid cols={2}>
        <Card title="Application">
          <dl className="info">
            <dt>Name</dt><dd>{app.name} — {app.tagline}</dd>
            <dt>Version</dt><dd>{app.version} (web)</dd>
            <dt>Runtime</dt><dd>Python {app.python} · scikit-learn {app.sklearn}</dd>
            <dt>Active model</dt><dd>{model ? `${model.name} v${model.version} (threshold ${model.threshold})` : "none"}</dd>
          </dl>
        </Card>
        <Card title="Database" actions={<Button busy={busy} onClick={backup}>Back up now</Button>}>
          <dl className="info">
            <dt>File</dt><dd><code>{db.path}</code></dd>
            <dt>Schema version</dt><dd>{db.schema_version}</dd>
            <dt>Integrity check</dt><dd>{db.integrity === "ok" ? <Badge tone="good">ok</Badge> : <Badge tone="critical">{String(db.integrity)}</Badge>}</dd>
            <dt>Mode</dt><dd>{db.demo ? <Badge tone="gold">Demo database</Badge> : <Badge tone="good">Live database</Badge>}</dd>
          </dl>
          <p className="muted small">The web app and the desktop app share this SQLite file. A backup is also taken automatically every time the web server starts.</p>
        </Card>
      </Grid>
      <Card title="Recent backups" subtitle="Verified copies in database/backups (newest first).">
        <DataTable rows={data.backups.map((b) => ({ ...b, path: b.path.split("/").pop(), size: kb(b.size_bytes) }))} columns={["path", "label", "created_utc", "schema_version", "size"]} search={false} empty="No backups yet" />
      </Card>
      <Card title="Privacy">
        <ul className="tips">{data.privacy.map((p) => <li key={p}>{p}</li>)}</ul>
      </Card>
    </>
  );
}
