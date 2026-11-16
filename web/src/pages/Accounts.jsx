import { useState } from "react";
import { api } from "../api.js";
import { go } from "../nav.js";
import { Badge, Button, Card, DataTable, ErrorState, Grid, Kpi, Loading, Notice, PageHead, Tabs, toast, useApi } from "../components/ui.jsx";
import { LinkBadge } from "./StudentPages.jsx";

const CONFIRM = {
  reject: "Reject this Student ID claim? The ID is released so its real owner can register, and this account sees no records.",
  deactivate: "Deactivate this account? It is signed out everywhere and cannot sign in until reactivated.",
  reset: "Issue a one-time password-reset code for this student? Give it to them in person (it works once, for 30 minutes). Any earlier code stops working.",
};

export default function Accounts() {
  const { data, error, loading, reload } = useApi("/api/admin/accounts");
  const log = useApi("/api/admin/security-log");
  const [tab, setTab] = useState("Waiting for verification");
  const [busy, setBusy] = useState("");
  const [issued, setIssued] = useState(null);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const act = async (u, action) => {
    if (CONFIRM[action] && !window.confirm(CONFIRM[action])) return;
    setBusy(`${u.user_id}:${action}`);
    try {
      await api(`/api/admin/accounts/${u.user_id}`, { method: "POST", body: { action } });
      toast(`${u.email}: ${action} done`);
      reload();
      log.reload();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy("");
    }
  };
  const issueReset = async (u) => {
    if (!window.confirm(CONFIRM.reset)) return;
    setBusy(`${u.user_id}:reset`);
    try {
      setIssued(await api(`/api/admin/accounts/${u.user_id}/reset-code`, { method: "POST" }));
      reload();
      log.reload();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy("");
    }
  };
  const pending = data.filter((u) => u.link_status === "pending");
  const rows = tab === "Waiting for verification" ? pending : data;
  return (
    <>
      <PageHead title="Student accounts" subtitle="Self-registered student accounts. A student sees their records only after you verify the Student ID they claimed - nothing is linked automatically." />
      <Grid cols={4}>
        <Kpi label="Student accounts" value={data.length} />
        <Kpi label="Waiting for verification" value={pending.length} tone="gold" />
        <Kpi label="Verified" value={data.filter((u) => u.link_status === "verified").length} tone="good" />
        <Kpi label="Deactivated" value={data.filter((u) => !u.is_active).length} tone="critical" />
      </Grid>
      <Notice kind="note">Before verifying, confirm with the student (in person or through an official channel) that the Student ID really is theirs. Matching names is not enough.</Notice>
      {issued && (
        <Notice kind="ok">
          Reset code for {issued.email}: <b style={{ fontFamily: "monospace", fontSize: "1.15em", letterSpacing: "0.05em" }}>{issued.code}</b> (valid until {issued.expires_utc} UTC).{" "}
          {issued.message} <button className="link" onClick={() => setIssued(null)}>Hide</button>
        </Notice>
      )}
      <Card title="Accounts" actions={<Tabs tabs={["Waiting for verification", "All accounts"]} value={tab} onChange={setTab} />}>
        <DataTable rows={rows} rowKey="user_id" empty={tab === "All accounts" ? "No student accounts yet" : "No accounts are waiting"}
          columns={["full_name", "email", "student_id", "record_exists", "link_status", "actions", "is_active", "last_login_utc"]}
          render={{
            student_id: (v) => (v ? <button className="link" onClick={() => go("students", v)}>{v}</button> : <span className="muted">released</span>),
            record_exists: (v) => (v ? "Yes" : <Badge tone="gold">No record</Badge>),
            link_status: (v) => <LinkBadge status={v} />,
            is_active: (v) => (v ? "Active" : <Badge tone="critical">Deactivated</Badge>),
            actions: (_, u) => (
              <div className="row nowrap">
                {u.link_status !== "verified" && u.student_id && <Button kind="primary" busy={busy === `${u.user_id}:verify`} disabled={!u.record_exists} title={u.record_exists ? "" : "No student record with this ID"} onClick={() => act(u, "verify")}>Verify</Button>}
                {u.link_status === "verified" && <Button kind="ghost" busy={busy === `${u.user_id}:unverify`} onClick={() => act(u, "unverify")}>Unverify</Button>}
                {u.link_status !== "rejected" && <Button kind="ghost" busy={busy === `${u.user_id}:reject`} onClick={() => act(u, "reject")}>Reject ID</Button>}
                {u.is_active ? <Button kind="ghost" busy={busy === `${u.user_id}:reset`} title={u.reset_code_expires_utc ? `A code is pending until ${u.reset_code_expires_utc}` : ""} onClick={() => issueReset(u)}>Reset code</Button> : null}
                {u.is_active ? <Button kind="danger" busy={busy === `${u.user_id}:deactivate`} onClick={() => act(u, "deactivate")}>Deactivate</Button>
                  : <Button kind="ghost" busy={busy === `${u.user_id}:activate`} onClick={() => act(u, "activate")}>Activate</Button>}
              </div>
            ),
          }} />
      </Card>
      <Card title="Security log" subtitle="Sign-ins, failed attempts, registrations and account changes (latest 200). No passwords or tokens are logged.">
        <DataTable rows={log.data || []} pageSize={10} empty="No security events yet" />
      </Card>
    </>
  );
}
