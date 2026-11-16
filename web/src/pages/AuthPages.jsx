import { useState } from "react";
import { api } from "../api.js";
import { Button, Field, Notice } from "../components/ui.jsx";

function AuthFrame({ title, subtitle, children, foot }) {
  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <div className="brand auth-brand">
          <img src="/logo.png" alt="" />
          <div>
            <b>EventPulse AI</b>
            <span>Predict. Plan. Participate.</span>
          </div>
        </div>
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
        {children}
        {foot && <div className="auth-foot">{foot}</div>}
      </div>
    </div>
  );
}

export function Login({ onSignedIn, notice }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api("/api/auth/login", { method: "POST", body: { email: email.trim(), password } });
      setPassword("");
      onSignedIn(r);
    } catch (x) {
      setErr(x.status === 0 ? "The EventPulse AI server is not reachable. Start it and try again." : x.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthFrame title="Sign in" subtitle="Students sign in with their college e-mail address; the organiser with the administrator account."
      foot={<>New student? <a href="#/register">Create a student account</a><br />Forgot your password? <a href="#/reset">Use a reset code from the organiser</a></>}>
      {notice && <Notice kind="note">{notice}</Notice>}
      <form className="stack" onSubmit={submit} noValidate={false}>
        <Field label="E-mail"><input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
        <Field label="Password"><input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        {err && <Notice kind="error">{err}</Notice>}
        <Button type="submit" busy={busy}>Sign in</Button>
      </form>
    </AuthFrame>
  );
}

const pwHints = (pw) => [
  [pw.length >= 10, "10+ characters"],
  [/[A-Za-z]/.test(pw) && /\d/.test(pw), "letters and numbers"],
];

export function Register() {
  const [f, setF] = useState({ full_name: "", email: "", student_id: "", password: "", confirm_password: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [done, setDone] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const submit = async (e) => {
    e.preventDefault();
    setErr(null);
    if (f.password !== f.confirm_password) return setErr("The two passwords do not match.");
    setBusy(true);
    try {
      const r = await api("/api/auth/register", { method: "POST", body: { ...f, full_name: f.full_name.trim(), email: f.email.trim(), student_id: f.student_id.trim() } });
      setDone(r);
      setF({ full_name: "", email: "", student_id: "", password: "", confirm_password: "" });
    } catch (x) {
      setErr(x.status === 0 ? "The EventPulse AI server is not reachable. Start it and try again." : x.message);
    } finally {
      setBusy(false);
    }
  };
  if (done)
    return (
      <AuthFrame title="Account created" foot={<a href="#/login">Go to sign in</a>}>
        <Notice kind="ok">{done.message}</Notice>
        <p className="muted small">Student ID {done.user.student_id} · status: waiting for the organiser's approval. QR check-in unlocks after that.</p>
      </AuthFrame>
    );
  return (
    <AuthFrame title="Create a student account" subtitle="For students taking part in events. Organiser accounts cannot be created here."
      foot={<>Already registered? <a href="#/login">Sign in</a></>}>
      <form className="stack" onSubmit={submit}>
        <Field label="Full name"><input autoComplete="name" required minLength={2} maxLength={100} value={f.full_name} onChange={set("full_name")} /></Field>
        <Field label="College e-mail" hint="Only college addresses (@ncuindia.edu) can register.">
          <input type="email" autoComplete="email" required maxLength={254} placeholder="name@ncuindia.edu" value={f.email} onChange={set("email")} />
        </Field>
        <Field label="Student ID" hint="The pseudonymous ID the organiser gave you (e.g. STU001) - not your roll number.">
          <input required minLength={2} maxLength={40} pattern="[A-Za-z0-9][A-Za-z0-9._\-]{1,39}" value={f.student_id} onChange={set("student_id")} />
        </Field>
        <Field label="Password" hint={pwHints(f.password).map(([ok, t]) => `${ok ? "✓" : "·"} ${t}`).join("   ")}>
          <input type="password" autoComplete="new-password" required minLength={10} maxLength={128} value={f.password} onChange={set("password")} />
        </Field>
        <Field label="Confirm password"><input type="password" autoComplete="new-password" required maxLength={128} value={f.confirm_password} onChange={set("confirm_password")} /></Field>
        {err && <Notice kind="error">{err}</Notice>}
        <Button type="submit" busy={busy}>Create account</Button>
        <p className="muted small">We store your name and e-mail only for sign-in. Your records appear after the organiser verifies your Student ID.</p>
      </form>
    </AuthFrame>
  );
}

export function ResetPassword() {
  const [f, setF] = useState({ email: "", code: "", password: "", confirm_password: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [done, setDone] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const submit = async (e) => {
    e.preventDefault();
    setErr(null);
    if (f.password !== f.confirm_password) return setErr("The two passwords do not match.");
    setBusy(true);
    try {
      const r = await api("/api/auth/reset-password", { method: "POST", body: { ...f, email: f.email.trim(), code: f.code.trim() } });
      setDone(r.message);
      setF({ email: "", code: "", password: "", confirm_password: "" });
    } catch (x) {
      setErr(x.status === 0 ? "The EventPulse AI server is not reachable. Start it and try again." : x.message);
    } finally {
      setBusy(false);
    }
  };
  if (done)
    return (
      <AuthFrame title="Password changed" foot={<a href="#/login">Go to sign in</a>}>
        <Notice kind="ok">{done}</Notice>
      </AuthFrame>
    );
  return (
    <AuthFrame title="Reset your password" subtitle="Ask the organiser for a one-time reset code. It works once and expires after 30 minutes."
      foot={<a href="#/login">Back to sign in</a>}>
      <form className="stack" onSubmit={submit}>
        <Field label="College e-mail"><input type="email" autoComplete="username" required maxLength={254} value={f.email} onChange={set("email")} /></Field>
        <Field label="Reset code" hint="Format XXXX-XXXX-XXXX"><input required minLength={12} maxLength={24} autoComplete="one-time-code" autoCapitalize="characters" value={f.code} onChange={set("code")} /></Field>
        <Field label="New password" hint={pwHints(f.password).map(([ok, t]) => `${ok ? "✓" : "·"} ${t}`).join("   ")}>
          <input type="password" autoComplete="new-password" required minLength={10} maxLength={128} value={f.password} onChange={set("password")} />
        </Field>
        <Field label="Confirm new password"><input type="password" autoComplete="new-password" required maxLength={128} value={f.confirm_password} onChange={set("confirm_password")} /></Field>
        {err && <Notice kind="error">{err}</Notice>}
        <Button type="submit" busy={busy}>Set new password</Button>
      </form>
    </AuthFrame>
  );
}
