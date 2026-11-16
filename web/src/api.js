// Thin client for the FastAPI backend. All data comes from /api; nothing is cached in
// browser storage. Errors carry the backend's message (or say the backend is unreachable).

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

function describe(detail) {
  if (!detail) return "Request failed";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail.map((d) => { const f = (d.loc || []).filter((x) => x !== "body").join("."); const m = String(d.msg || "").replace(/^Value error, /, ""); return f ? `${f.replace(/_/g, " ")}: ${m}` : m; }).join("; ");
  return JSON.stringify(detail);
}

// Per-session CSRF token: kept in memory only (never localStorage / sessionStorage). The session
// itself lives in an HttpOnly cookie that JavaScript cannot read.
let csrfToken = null;
export const setCsrf = (t) => {
  csrfToken = t || null;
};

export async function api(path, { method = "GET", body, form, raw } = {}) {
  let res;
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  try {
    res = await fetch(path, {
      method,
      credentials: "same-origin",
      headers,
      body: form || (body !== undefined ? JSON.stringify(body) : undefined),
    });
  } catch {
    throw new ApiError("The EventPulse AI backend is not reachable. Start it and try again.", 0);
  }
  if (raw && res.ok) return res;
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (res.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new CustomEvent("ep-unauthorized"));
  if (!res.ok) throw new ApiError(describe(data && data.detail !== undefined ? data.detail : data), res.status, data);
  return data;
}

export async function download(path, { method = "GET", fallbackName = "download" } = {}) {
  const res = await api(path, { method, raw: true });
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const m = cd.match(/filename="?([^";]+)"?/);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = m ? m[1] : fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  return a.download;
}

// Query string including the leading "?" (empty string when there is nothing to send).
export const qs = (o) => {
  const s = Object.entries(o)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join("&");
  return s ? `?${s}` : "";
};
