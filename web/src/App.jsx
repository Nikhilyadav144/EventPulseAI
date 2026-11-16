import { useCallback, useEffect, useState } from "react";
import { api, setCsrf } from "./api.js";
import { AuthContext } from "./auth.jsx";
import { Toasts } from "./components/ui.jsx";
import Accounts from "./pages/Accounts.jsx";
import { Login, Register, ResetPassword } from "./pages/AuthPages.jsx";
import Students from "./pages/Students.jsx";
import { StudentAttendance, StudentHome, StudentPredictions, StudentRegistrations } from "./pages/StudentPages.jsx";
import Overview from "./pages/Overview.jsx";
import DatasetExplorer from "./pages/DatasetExplorer.jsx";
import Events from "./pages/Events.jsx";
import Calendar from "./pages/Calendar.jsx";
import Registrations from "./pages/Registrations.jsx";
import Prediction from "./pages/Prediction.jsx";
import Attendance from "./pages/Attendance.jsx";
import ModelLab from "./pages/ModelLab.jsx";
import Analytics from "./pages/Analytics.jsx";
import Insights from "./pages/Insights.jsx";
import Monitoring from "./pages/Monitoring.jsx";
import Reports from "./pages/Reports.jsx";
import Notifications from "./pages/Notifications.jsx";
import Settings from "./pages/Settings.jsx";

// Administrator navigation (the server enforces the same rule: every /api data route is admin-only).
export const ADMIN_NAV = [
  { section: "Workspace", items: [
    ["overview", "Overview", Overview, "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"],
    ["dataset", "Dataset Explorer", DatasetExplorer, "M4 6h16M4 12h16M4 18h16"],
    ["events", "Event Management", Events, "M5 6h14v14H5zM8 3v5M16 3v5M5 10h14"],
    ["calendar", "Event Calendar", Calendar, "M4 6h16v14H4zM8 3v5M16 3v5M8 13h.01M12 13h.01M16 13h.01M8 17h.01M12 17h.01"],
    ["registrations", "Registration Management", Registrations, "M9 11a3 3 0 100-6 3 3 0 000 6zM3 20c0-3 3-5 6-5s6 2 6 5M17 8a2.5 2.5 0 110 5M18 15c2 .5 3 2 3 5"],
    ["prediction", "Attendance Prediction", Prediction, "M12 4a8 8 0 100 16 8 8 0 000-16zM12 9a3 3 0 100 6 3 3 0 000-6z"],
    ["attendance", "Attendance Records", Attendance, "M6 4h12v16H6zM9 12l2 2 4-4"],
    ["students", "Students", Students, "M12 12a4 4 0 100-8 4 4 0 000 8zM4 21c0-4 4-6 8-6s8 2 8 6"],
  ]},
  { section: "Intelligence", items: [
    ["models", "Model Laboratory", ModelLab, "M9 3h6M10 3v6l-5 9a2 2 0 002 3h10a2 2 0 002-3l-5-9V3"],
    ["analytics", "Analytics", Analytics, "M4 20V10M10 20V4M16 20v-7M22 20H2"],
    ["insights", "Insights", Insights, "M9 18h6M10 21h4M12 3a6 6 0 00-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0012 3z"],
    ["monitoring", "Model Monitoring", Monitoring, "M3 5h18v12H3zM8 21h8M12 17v4M6 13l3-3 3 2 5-4"],
  ]},
  { section: "Manage", items: [
    ["reports", "Reports & Export", Reports, "M6 3h9l4 4v14H6zM14 3v5h5M9 13h6M9 17h6"],
    ["accounts", "Student Accounts", Accounts, "M16 11a3 3 0 100-6 3 3 0 000 6zM8 11a3 3 0 100-6 3 3 0 000 6zM2 20c0-3 3-5 6-5 1.5 0 2.8.4 3.8 1M13 20c0-3 2-5 4-5s5 2 5 5M9 7h.01"],
    ["notifications", "Notifications", Notifications, "M6 17V11a6 6 0 1112 0v6l1.5 1.5h-15zM10 21h4"],
    ["settings", "Settings", Settings, "M12 9a3 3 0 100 6 3 3 0 000-6zM4 12h2M18 12h2M12 4v2M12 18v2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4"],
  ]},
];
// Student navigation: only the student's own data (/api/student/*).
export const STUDENT_NAV = [
  { section: "My EventPulse", items: [
    ["home", "My dashboard", StudentHome, "M4 11l8-7 8 7v9H4zM10 20v-6h4v6"],
    ["my-registrations", "My registrations", StudentRegistrations, "M5 6h14v14H5zM8 3v5M16 3v5M5 10h14"],
    ["my-attendance", "My attendance", StudentAttendance, "M6 4h12v16H6zM9 12l2 2 4-4"],
    ["my-predictions", "My predictions", StudentPredictions, "M12 4a8 8 0 100 16 8 8 0 000-16zM12 9a3 3 0 100 6 3 3 0 000-6z"],
  ]},
];
const pagesOf = (nav) => Object.fromEntries(nav.flatMap((s) => s.items).map(([k, t, C]) => [k, { title: t, C }]));
const ADMIN_PAGES = pagesOf(ADMIN_NAV);
const STUDENT_PAGES = pagesOf(STUDENT_NAV);

// After signing in from a QR check-in page the user returns to it. Only same-site /checkin/ paths are
// accepted (never an absolute URL), so the parameter cannot be used as an open redirect.
const NEXT_RE = /^\/checkin\/[A-Za-z0-9._~%-]+\?t=[A-Za-z0-9._~%-]+$/;
export function checkinNext() {
  const m = window.location.hash.match(/[?&]next=([^&]+)/);
  if (!m) return null;
  let v;
  try {
    v = decodeURIComponent(m[1]);
  } catch {
    return null;
  }
  return NEXT_RE.test(v) ? v : null;
}

function parseHash(pages, fallback) {
  const h = window.location.hash.replace(/^#\/?/, "");
  const [page, ...rest] = h.split("/");
  return { page: pages[page] ? page : fallback, arg: rest.join("/") ? decodeURIComponent(rest.join("/")) : null, raw: page };
}

function Icon({ d }) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

function useHashRoute(pages, fallback) {
  const [route, setRoute] = useState(() => parseHash(pages, fallback));
  useEffect(() => {
    const on = () => {
      setRoute(parseHash(pages, fallback));
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, [pages, fallback]);
  return route;
}

function Shell({ user, nav, pages, fallback, signOut }) {
  const route = useHashRoute(pages, fallback);
  const [health, setHealth] = useState(null);
  const [offline, setOffline] = useState(false);
  const [menu, setMenu] = useState(false);
  const admin = user.role === "admin";
  useEffect(() => setMenu(false), [route]);

  useEffect(() => {
    let alive = true;
    const ping = async () => {
      try {
        const h = await api(admin ? "/api/health/details" : "/api/health");
        if (alive) {
          setHealth(h);
          setOffline(false);
        }
      } catch {
        if (alive) setOffline(true);
      }
    };
    ping();
    const t = setInterval(ping, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const P = pages[route.page];
  useEffect(() => {
    document.title = `${P.title} · EventPulse AI`;
  }, [P]);

  return (
    <div className={`shell ${menu ? "menu-open" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <img src="/logo.png" alt="" />
          <div>
            <b>EventPulse AI</b>
            <span>{admin ? "Organiser workspace" : "Student portal"}</span>
          </div>
        </div>
        <nav aria-label="Main">
          {nav.map((s) => (
            <div key={s.section}>
              <p className="nav-section">{s.section}</p>
              {s.items.map(([k, t, , d]) => (
                <a key={k} href={`#/${k}`} className={route.page === k ? "on" : ""} aria-current={route.page === k ? "page" : undefined}>
                  <Icon d={d} />
                  <span>{t}</span>
                </a>
              ))}
            </div>
          ))}
        </nav>
        <div className="side-foot">
          {admin && (
            <div className="side-model">
              <span className="eyebrow">Active model</span>
              <b>{health ? health.active_model || "None" : "…"}</b>
              {health && <span className={`badge ${health.demo ? "b-gold" : "b-purple"}`}>{health.demo ? "Demo data" : "Live data"}</span>}
            </div>
          )}
          <div className="side-user">
            <span className="eyebrow">{admin ? "Administrator" : "Student"}</span>
            <b title={user.email}>{user.full_name}</b>
            <span className="muted small ellipsis">{user.email}</span>
            <button className="btn btn-ghost sm" onClick={signOut}>Sign out</button>
          </div>
          {admin && <span className="muted small">Version {health ? health.version : "…"} · web</span>}
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <button className="hamburger" aria-label="Open navigation" onClick={() => setMenu(!menu)}>
            <Icon d="M4 7h16M4 12h16M4 17h16" />
          </button>
          <span className="top-title">{P.title}</span>
          <span className={`conn ${offline ? "off" : "on"}`}>{offline ? "Backend offline" : health ? "Connected" : "Connecting…"}</span>
        </header>
        {offline && (
          <div className="offline">
            The EventPulse AI backend is not responding. Start it with <code>EventPulse_Web.command</code>; this page reconnects automatically.
          </div>
        )}
        <main className="content" key={route.page}>
          <P.C arg={route.arg} />
        </main>
      </div>
      <div className="scrim" onClick={() => setMenu(false)} />
    </div>
  );
}

export default function App() {
  const [auth, setAuth] = useState({ status: "loading", user: null, notice: null });
  const [hash, setHash] = useState(window.location.hash);

  const load = useCallback(async () => {
    try {
      const r = await api("/api/auth/me");
      const next = checkinNext();
      if (next) return window.location.assign(next);        // already signed in: back to the QR check-in
      setCsrf(r.csrf_token);
      setAuth({ status: "in", user: r.user, notice: null });
    } catch (e) {
      setCsrf(null);
      setAuth({ status: e.status === 0 ? "offline" : "out", user: null, notice: null });
    }
  }, []);

  useEffect(() => {
    load();
    const expired = () => {
      setCsrf(null);
      setAuth({ status: "out", user: null, notice: "Your session has ended. Please sign in again." });
    };
    const onHash = () => setHash(window.location.hash);
    window.addEventListener("ep-unauthorized", expired);
    window.addEventListener("hashchange", onHash);
    return () => {
      window.removeEventListener("ep-unauthorized", expired);
      window.removeEventListener("hashchange", onHash);
    };
  }, [load]);

  const signedIn = (r) => {
    const next = checkinNext();
    if (next) return window.location.assign(next);
    setCsrf(r.csrf_token);
    setAuth({ status: "in", user: r.user, notice: null });
    window.location.hash = r.user.role === "admin" ? "#/overview" : "#/home";
  };

  const signOut = async () => {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch {
      /* the session is cleared locally either way */
    }
    setCsrf(null);
    setAuth({ status: "out", user: null, notice: "You have been signed out." });
    window.location.hash = "#/login";
  };

  let body;
  if (auth.status === "loading") body = <div className="auth-wrap"><div className="loading"><span className="spinner" /> Loading…</div></div>;
  else if (auth.status === "offline")
    body = (
      <div className="auth-wrap">
        <div className="auth-card">
          <h1>Server not reachable</h1>
          <p className="muted">The EventPulse AI backend is not responding. Start it with <code>EventPulse_Web.command</code>.</p>
          <button className="btn btn-primary" onClick={load}>Try again</button>
        </div>
      </div>
    );
  else if (auth.status === "out") {
    document.title = "Sign in · EventPulse AI";
    body = hash.startsWith("#/register") ? <Register /> : hash.startsWith("#/reset") ? <ResetPassword />
      : <Login onSignedIn={signedIn} notice={checkinNext() ? "Sign in to finish your QR check-in." : auth.notice} />;
  } else if (auth.user.role === "admin")
    body = <Shell user={auth.user} nav={ADMIN_NAV} pages={ADMIN_PAGES} fallback="overview" signOut={signOut} />;
  else body = <Shell user={auth.user} nav={STUDENT_NAV} pages={STUDENT_PAGES} fallback="home" signOut={signOut} />;

  return (
    <AuthContext.Provider value={{ user: auth.user, signOut }}>
      {body}
      <Toasts />
    </AuthContext.Provider>
  );
}
