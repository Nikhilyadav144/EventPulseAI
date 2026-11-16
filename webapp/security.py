"""Security configuration and HTTP hardening for the EventPulse AI web app (Phase 2).

Everything here is configured with environment variables and is OFF-by-default safe for local
development: with no variables set the web app behaves exactly as before on 127.0.0.1 / the LAN.

    EVENTPULSE_ENV=production            turns on the production rules below and FAILS CLOSED
                                         (the server refuses to start) when a required setting
                                         is missing or unsafe.
    EVENTPULSE_PUBLIC_BASE_URL           https://<public host>   (required in production; used for
                                         QR links and HTTPS redirects)
    EVENTPULSE_ALLOWED_HOSTS             comma list of Host names accepted (production default: the
                                         host of EVENTPULSE_PUBLIC_BASE_URL)
    EVENTPULSE_TRUSTED_PROXIES           "none", "*" or comma list of proxy IPs/CIDRs whose
                                         X-Forwarded-For / X-Forwarded-Proto are believed
                                         (required in production; on Render use "*" - the service is
                                         only reachable through Render's proxy)
    EVENTPULSE_PROXY_HOPS                how many trusted proxies append to X-Forwarded-For (default 1)
    EVENTPULSE_SECURE_COOKIES            1 = Secure cookie (forced on in production; 0 is refused there)
    EVENTPULSE_ALLOWED_EMAIL_DOMAINS     domains students may register with (default ncuindia.edu)
    EVENTPULSE_HASH_CONCURRENCY          password hashes computed at the same time (default 2, ~64 MB each)
    EVENTPULSE_DB_URL                    required in production (PostgreSQL); local SQLite is refused
                                         there unless EVENTPULSE_ALLOW_SQLITE_IN_PRODUCTION=1
    EVENTPULSE_TIMEZONE                  zone for event dates and check-in (default Asia/Kolkata); in
                                         production the web process also runs in this zone
    WEB_CONCURRENCY                      if set in production it must be 1: limits, sign-in delays and
                                         the hashing cap are kept in ONE process (single Uvicorn worker)

The middleware (``install``) adds: Host validation and HTTPS enforcement (production), a
cross-site request check on every state-changing request (Origin / Sec-Fetch-Site, plus JSON-only
bodies for the sign-in endpoints), and security headers (CSP, HSTS in production, nosniff,
frame denial, referrer, permissions and cross-origin policies).
"""
from __future__ import annotations

import ipaddress
import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Mapping, Optional
from urllib.parse import urlsplit

DEFAULT_EMAIL_DOMAINS = ("ncuindia.edu",)
DEFAULT_TIMEZONE = "Asia/Kolkata"
# India has no daylight saving: a fixed +05:30 offset is exact for Asia/Kolkata, so the app works
# even on a host without the IANA time-zone database.
IST = timezone(timedelta(hours=5, minutes=30), "IST")
DEV_ORIGINS = ("http://127.0.0.1:5173", "http://localhost:5173")
_HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")
_DOMAIN_RE = re.compile(r"^(?=.{3,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
       "form-action 'self'")
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"
# The interactive API docs load Swagger UI from a CDN; they exist only in development.
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")
# Paths that must answer plain HTTP from the platform's own health checker.
HEALTH_PATHS = ("/api/health",)
# Unauthenticated JSON endpoints: their bodies must be application/json (an HTML form on another
# site cannot send that without a CORS preflight, which this server never grants).
JSON_ONLY_PREFIXES = ("/api/auth/",)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class ConfigError(RuntimeError):
    """The security configuration is unsafe for the selected environment (server refuses to start)."""


@dataclass(frozen=True)
class SecurityConfig:
    production: bool = False
    secure_cookies: bool = False
    public_base_url: Optional[str] = None
    allowed_hosts: tuple = ()
    trust_all_proxies: bool = False
    trusted_proxies: tuple = ()
    proxy_hops: int = 1
    hsts_seconds: int = 0
    allowed_email_domains: tuple = DEFAULT_EMAIL_DOMAINS
    hash_concurrency: int = 2
    docs_enabled: bool = True
    allowed_origins: tuple = field(default=DEV_ORIGINS)
    timezone_name: str = DEFAULT_TIMEZONE

    @property
    def session_cookie(self) -> str:
        # "__Host-" cookies must be Secure, Path=/ and have no Domain: browsers enforce that.
        return "__Host-ep_session" if self.secure_cookies else "ep_session"


def _flag(env: Mapping[str, str], name: str) -> Optional[bool]:
    v = env.get(name)
    if v is None or v.strip() == "":
        return None
    v = v.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be 1 or 0 (got {v!r}).")


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number.") from None
    if not lo <= n <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}.")
    return n


def _base_url(raw: str, production: bool) -> str:
    parts = urlsplit(raw.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password \
            or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ConfigError("EVENTPULSE_PUBLIC_BASE_URL must look like https://host (no path, query or credentials).")
    if production and parts.scheme != "https":
        raise ConfigError("EVENTPULSE_PUBLIC_BASE_URL must use https:// in production.")
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}"


def load_config(env: Optional[Mapping[str, str]] = None) -> SecurityConfig:
    env = os.environ if env is None else env
    mode = (env.get("EVENTPULSE_ENV") or "development").strip().lower()
    if mode not in ("development", "production"):
        raise ConfigError("EVENTPULSE_ENV must be 'development' or 'production'.")
    prod = mode == "production"
    problems: list[str] = []

    secure = _flag(env, "EVENTPULSE_SECURE_COOKIES")
    if prod and secure is False:
        problems.append("EVENTPULSE_SECURE_COOKIES=0 is not allowed in production (cookies must be Secure).")
    secure = True if prod else bool(secure)

    base = None
    raw_base = (env.get("EVENTPULSE_PUBLIC_BASE_URL") or "").strip()
    if raw_base:
        try:
            base = _base_url(raw_base, prod)
        except ConfigError as exc:
            problems.append(str(exc))
    elif prod:
        problems.append("EVENTPULSE_PUBLIC_BASE_URL (https://your-host) is required in production.")

    hosts = tuple(h.strip().lower() for h in (env.get("EVENTPULSE_ALLOWED_HOSTS") or "").split(",") if h.strip())
    for h in hosts:
        if not _HOST_RE.match(h):
            problems.append(f"EVENTPULSE_ALLOWED_HOSTS: {h!r} is not a host name (wildcards are not allowed).")
    if prod and not hosts and base:
        hosts = (urlsplit(base).hostname,)

    raw_proxies = (env.get("EVENTPULSE_TRUSTED_PROXIES") or "").strip()
    trust_all, nets = False, []
    if raw_proxies == "*":
        trust_all = True
    elif raw_proxies and raw_proxies.lower() != "none":
        for p in raw_proxies.split(","):
            try:
                nets.append(ipaddress.ip_network(p.strip(), strict=False))
            except ValueError:
                problems.append(f"EVENTPULSE_TRUSTED_PROXIES: {p.strip()!r} is not an IP address or CIDR range.")
    if prod and not raw_proxies:
        problems.append("EVENTPULSE_TRUSTED_PROXIES is required in production ('*' behind Render's proxy, "
                        "'none' when clients connect directly).")

    domains = tuple(d.strip().lower().lstrip("@") for d in
                    (env.get("EVENTPULSE_ALLOWED_EMAIL_DOMAINS") or ",".join(DEFAULT_EMAIL_DOMAINS)).split(",") if d.strip())
    for d in domains:
        if not _DOMAIN_RE.match(d):
            problems.append(f"EVENTPULSE_ALLOWED_EMAIL_DOMAINS: {d!r} is not a domain name.")
    if not domains:
        problems.append("EVENTPULSE_ALLOWED_EMAIL_DOMAINS must list at least one domain.")

    if prod and not (env.get("EVENTPULSE_DB_URL") or "").strip() and _flag(env, "EVENTPULSE_ALLOW_SQLITE_IN_PRODUCTION") is not True:
        problems.append("EVENTPULSE_DB_URL (PostgreSQL) is required in production; a local SQLite file on a "
                        "hosting platform's temporary disk would lose data.")

    tz_name = (env.get("EVENTPULSE_TIMEZONE") or DEFAULT_TIMEZONE).strip()
    try:
        zone_for(tz_name)
    except ConfigError as exc:
        problems.append(str(exc))
    if prod and (env.get("WEB_CONCURRENCY") or "1").strip() != "1":
        problems.append("WEB_CONCURRENCY must be 1 (or unset) in production: rate limits, sign-in delays and the "
                        "password-hashing cap live in one process, so run a single Uvicorn worker.")

    try:
        hops = _int(env, "EVENTPULSE_PROXY_HOPS", 1, 1, 5)
        conc = _int(env, "EVENTPULSE_HASH_CONCURRENCY", 2, 1, 8)
    except ConfigError as exc:
        problems.append(str(exc))
        hops, conc = 1, 2

    if problems:
        raise ConfigError("Unsafe EventPulse AI web configuration:\n  - " + "\n  - ".join(problems))
    origins = tuple(o for o in ((base,) if base else ()) + (() if prod else DEV_ORIGINS))
    return SecurityConfig(production=prod, secure_cookies=secure, public_base_url=base, allowed_hosts=hosts,
                          trust_all_proxies=trust_all, trusted_proxies=tuple(nets), proxy_hops=hops,
                          hsts_seconds=31536000 if prod else 0, allowed_email_domains=domains,
                          hash_concurrency=conc, docs_enabled=not prod, allowed_origins=origins,
                          timezone_name=tz_name)


# ============================================================================ time zone
def zone_for(name: str):
    """tzinfo for an IANA zone name. Asia/Kolkata falls back to a fixed +05:30 offset (exact: no DST)."""
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    except ImportError:                              # pragma: no cover - Python < 3.9
        pass
    if name == DEFAULT_TIMEZONE:
        return IST
    raise ConfigError(f"EVENTPULSE_TIMEZONE: unknown time zone {name!r}.")


def local_now(cfg: SecurityConfig, now: Optional[datetime] = None) -> datetime:
    """Current time in the configured zone, independent of the server's own zone (Render runs in UTC)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("local_now needs an aware datetime")
    return now.astimezone(zone_for(cfg.timezone_name))


def local_today(cfg: SecurityConfig, now: Optional[datetime] = None) -> date:
    """Calendar date in the configured zone: used for 'check-in is open on the event day only'."""
    return local_now(cfg, now).date()


def apply_process_timezone(cfg: SecurityConfig) -> str:
    """Production only: make the web process's local time the configured zone, so every remaining
    ``date.today()`` (e.g. 'upcoming' lists) agrees with check-in. Never called by the desktop app."""
    name = cfg.timezone_name
    tz = name if os.path.exists(os.path.join("/usr/share/zoneinfo", name)) else \
        ("IST-5:30" if name == DEFAULT_TIMEZONE else name)        # POSIX form needs no tz database
    os.environ["TZ"] = tz
    if hasattr(time, "tzset"):
        time.tzset()
    return tz


# ============================================================================ log redaction
# Secrets that could otherwise reach logs: the QR token (?t=) in access-log paths, reset codes,
# CSRF/session tokens, passwords, and credentials inside database URLs.
_REDACTIONS = (
    (re.compile(r"([?&](?:t|token|code|csrf_token|password|session)=)[^&\s\"']+", re.I), r"\1[redacted]"),
    (re.compile(r"(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@", re.I), r"\1[redacted]@"),
    (re.compile(r"(\bpassword\s*[=:]\s*)[^\s,;&\"']+", re.I), r"\1[redacted]"),
    (re.compile(r"((?:__Host-)?ep_session=)[^;\s]+", re.I), r"\1[redacted]"),
)


def redact(text: str) -> str:
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    """Removes secrets from log records (message and arguments) before any handler writes them."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
        return True


_FILTER = RedactingFilter()
LOGGER_NAMES = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "eventpulse", "fastapi")


def install_log_redaction() -> None:
    """Attach the redacting filter to the app's and Uvicorn's loggers AND their handlers (a logger's own
    filter does not see records propagated from child loggers). Safe to call more than once."""
    for name in LOGGER_NAMES:
        lg = logging.getLogger(name)
        if _FILTER not in lg.filters:
            lg.addFilter(_FILTER)
        for h in lg.handlers:
            if _FILTER not in h.filters:
                h.addFilter(_FILTER)


# ============================================================================ proxy-aware request facts
def _peer(request) -> str:
    return request.client.host if request.client else ""


def _ip(value: str) -> Optional[str]:
    try:
        return str(ipaddress.ip_address(value.strip().strip("[]")))
    except ValueError:
        return None


def peer_is_trusted(request, cfg: SecurityConfig) -> bool:
    if cfg.trust_all_proxies:
        return True
    ip = _ip(_peer(request))
    if ip is None or not cfg.trusted_proxies:
        return False
    addr = ipaddress.ip_address(ip)
    return any(addr in n for n in cfg.trusted_proxies)


def client_ip(request, cfg: SecurityConfig) -> str:
    """The real client address. X-Forwarded-For is used ONLY when the direct peer is a configured
    trusted proxy, and then only the entry that proxy itself appended (counting ``proxy_hops`` from
    the right), so a client cannot choose its own address by sending the header."""
    peer = _peer(request) or "unknown"
    if not peer_is_trusted(request, cfg):
        return peer
    parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if len(parts) < cfg.proxy_hops:
        return peer
    return _ip(parts[-cfg.proxy_hops]) or peer


def request_scheme(request, cfg: SecurityConfig) -> str:
    if peer_is_trusted(request, cfg):
        proto = request.headers.get("x-forwarded-proto", "").split(",")[-1].strip().lower()
        if proto in ("http", "https"):
            return proto
    return request.url.scheme


def request_host(request) -> str:
    host = (request.headers.get("host") or "").strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def same_origin(request, cfg: SecurityConfig) -> str:
    host = request.headers.get("host", "")
    return f"{request_scheme(request, cfg)}://{host}".lower()


def origin_allowed(request, cfg: SecurityConfig) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return True                              # non-browser client; browsers always send Origin on POST
    origin = origin.strip().lower()
    return origin == same_origin(request, cfg) or origin in cfg.allowed_origins


# ============================================================================ progressive throttle
class ProgressiveThrottle:
    """Failure counter with exponentially growing waits instead of a permanent lockout.

    The first ``free`` failures cost nothing; after that each further attempt must wait
    ``base * 2**(failures - free)`` seconds (capped at ``cap``) after the previous failure. The record
    is forgotten after ``forget_after`` seconds without failures, and reset on success. In memory, one
    process (the free hosting tier runs a single instance)."""

    def __init__(self, free: int, base: float, cap: float, forget_after: float, max_keys: int = 100_000):
        self.free, self.base, self.cap, self.forget_after, self.max_keys = free, base, cap, forget_after, max_keys
        self._state: "OrderedDict[str, list]" = OrderedDict()
        self._lock = threading.Lock()

    def _get(self, key: str, now: float):
        st = self._state.get(key)
        if st and now - st[1] > self.forget_after:
            del self._state[key]
            return None
        return st

    def delay_for(self, failures: int) -> float:
        return 0.0 if failures < self.free else float(min(self.base * 2 ** (failures - self.free), self.cap))

    def retry_after(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            st = self._get(key, now)
            if not st:
                return 0
            remaining = st[1] + self.delay_for(st[0]) - now
        return max(0, math.ceil(remaining))

    def failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            st = self._get(key, now)
            if st:
                st[0] += 1
                st[1] = now
                self._state.move_to_end(key)
            else:
                self._state[key] = [1, now]
                while len(self._state) > self.max_keys:
                    self._state.popitem(last=False)

    def failures(self, key: str) -> int:
        with self._lock:
            st = self._get(key, time.monotonic())
            return st[0] if st else 0

    def reset(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._state.clear()
            else:
                self._state.pop(key, None)


# ============================================================================ middleware
def _security_headers(response, path: str, cfg: SecurityConfig) -> None:
    h = response.headers
    if not (cfg.docs_enabled and path.startswith(DOCS_PATHS)):
        h.setdefault("Content-Security-Policy", CSP)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if cfg.hsts_seconds:
        h.setdefault("Strict-Transport-Security", f"max-age={cfg.hsts_seconds}")
    if path.startswith(("/api/", "/checkin/")):
        h.setdefault("Cache-Control", "no-store")


def install(app, cfg: SecurityConfig) -> None:
    """Register the security middleware on a FastAPI/Starlette app."""
    from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

    @app.middleware("http")
    async def security_middleware(request, call_next):
        path = request.url.path
        method = request.method.upper()
        if cfg.production and path not in HEALTH_PATHS:
            if cfg.allowed_hosts and request_host(request) not in cfg.allowed_hosts:
                return PlainTextResponse("Invalid host.", status_code=400)
            if request_scheme(request, cfg) != "https":
                if method in ("GET", "HEAD"):
                    target = cfg.public_base_url + path + (f"?{request.url.query}" if request.url.query else "")
                    resp = RedirectResponse(target, status_code=308)
                else:
                    resp = JSONResponse({"detail": "HTTPS is required."}, status_code=403)
                _security_headers(resp, path, cfg)
                return resp
        if method in UNSAFE_METHODS:
            refused = None
            if request.headers.get("sec-fetch-site", "").lower() == "cross-site" or not origin_allowed(request, cfg):
                refused = "Cross-site request refused."
            elif path.startswith(JSON_ONLY_PREFIXES) and path != "/api/auth/logout" and \
                    not request.headers.get("content-type", "").lower().startswith("application/json"):
                refused = "This endpoint accepts JSON requests only."
            if refused:
                resp = JSONResponse({"detail": refused}, status_code=403)
                _security_headers(resp, path, cfg)
                return resp
        response = await call_next(request)
        _security_headers(response, path, cfg)
        return response
