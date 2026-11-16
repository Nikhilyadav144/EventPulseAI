"""PostgreSQL support for ``core.db.connection.Database`` (cloud deployment).

The desktop app never imports this module: it is used only when the database is given
as a ``postgresql://`` URL (``EVENTPULSE_DB_URL``). It exposes the small part of the
sqlite3 connection API that EventPulse code uses, so repositories, services and the web
API run unchanged on either engine:

    conn.execute(sql, params) -> cursor with fetchone(), fetchall(), lastrowid, rowcount
    conn.executemany(sql, seq_of_params)
    conn.executescript(sql)              # migrations: several statements, no parameters
    conn.close()

What the adapter does (and nothing more):
* ``?`` placeholders -> ``%s`` (outside quotes and comments); a literal ``%`` -> ``%%``.
* ``BEGIN IMMEDIATE`` -> ``BEGIN``; ``PRAGMA ...`` statements are no-ops.
* ``INSERT`` without ``RETURNING`` gets ``RETURNING *`` so ``cursor.lastrowid`` works (the
  first column of every AUTOINCREMENT/IDENTITY table is its id).
* Parameters: ``bool`` -> 0/1; whole floats -> int and NaN -> NULL (as SQLite); ``date``/``datetime`` -> ISO text (dates are stored as ISO text, as in SQLite);
  numpy scalars -> Python numbers.
* Rows behave like ``sqlite3.Row``: ``row[0]``, ``row["name"]``, ``row.keys()``, ``dict(row)``.
* Errors are raised as ``core.db.errors.PgError`` (SQLSTATE + constraint) and translated by
  ``core.db.errors.translate`` into the same exceptions as on SQLite.

Connection settings (``prepare_url``): every non-local host gets ``sslmode=require`` (weaker
modes are refused), plus ``connect_timeout`` and ``application_name``. After connecting,
``check_schema`` refuses the ``public`` schema on a non-local host and enforces
``EVENTPULSE_DB_SCHEMA`` when set (private-schema deployment, e.g. Supabase).

Driver: psycopg 3 (``pip install "psycopg[binary]"``) in production. Tests may install a
different "raw driver" with ``set_driver_factory`` (see tests/pg_libpq.py).
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any, Callable, Iterable, Optional, Sequence
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from core.db.errors import PgError

URL_PREFIXES = ("postgresql://", "postgres://")


def is_postgres_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(URL_PREFIXES)


def redact(url: str) -> str:
    """URL safe for logs and screens: no user name or password."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or "localhost"
        netloc = host + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    except ValueError:
        return "postgresql://(unparseable)"


# --------------------------------------------------------------------------- connection settings
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
STRONG_SSLMODES = {"require", "verify-ca", "verify-full"}
DEFAULT_APP_NAME = "eventpulse"
DEFAULT_CONNECT_TIMEOUT = 10                                  # seconds


def _hosts(url: str) -> list[str]:
    """Host names the URL connects to (netloc host and/or ``?host=``; may be a comma list)."""
    parts = urlsplit(url)
    hosts = [h for h in (parts.hostname or "").split(",") if h]
    for q in parse_qs(parts.query).get("host", []):
        hosts += [h for h in q.split(",") if h]
    return hosts


def is_local_url(url: str) -> bool:
    """True when every host is a Unix socket directory or localhost / 127.0.0.1 / ::1.

    Decided by the host NAMES only (``hostaddr`` is ignored on purpose): a named host is
    treated as remote, so it always gets an encrypted connection."""
    hosts = _hosts(url)
    return not hosts or all(h.startswith("/") or h.lower() in LOCAL_HOSTS for h in hosts)


def prepare_url(url: str, env: Optional[dict] = None) -> str:
    """The URL actually used to connect: missing settings are added as URL parameters.

    * ``sslmode=require`` for any non-local host; a weaker ``sslmode`` there is refused.
    * ``connect_timeout`` (``EVENTPULSE_DB_CONNECT_TIMEOUT``, default 10 s, 1-120).
    * ``application_name`` (``EVENTPULSE_DB_APP_NAME``, default ``eventpulse``).
    Values already in the URL are kept (except an insecure sslmode for a remote host).
    Local URLs (tests, the desktop never uses PostgreSQL) get only the timeout and name.
    """
    env = os.environ if env is None else env
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    add: dict[str, str] = {}
    if not is_local_url(url):
        mode = (query.get("sslmode") or [""])[-1].strip().lower()
        if not mode:
            add["sslmode"] = "require"
        elif mode not in STRONG_SSLMODES:
            raise PgError("EP001", f"Refusing sslmode={mode} for the non-local database host "
                                   f"{parts.hostname}: use require, verify-ca or verify-full.")
    if "connect_timeout" not in query:
        try:
            t = int(str(env.get("EVENTPULSE_DB_CONNECT_TIMEOUT") or DEFAULT_CONNECT_TIMEOUT))
        except ValueError:
            t = DEFAULT_CONNECT_TIMEOUT
        add["connect_timeout"] = str(min(max(t, 1), 120))
    if "application_name" not in query:
        name = (env.get("EVENTPULSE_DB_APP_NAME") or DEFAULT_APP_NAME).strip()[:63]
        add["application_name"] = re.sub(r"[^A-Za-z0-9_.-]", "-", name) or DEFAULT_APP_NAME
    if not add:
        return url
    extra = urlencode(add)
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       f"{parts.query}&{extra}" if parts.query else extra, parts.fragment))


def check_schema(schema: Optional[str], url: str, env: Optional[dict] = None) -> None:
    """Refuse an unsafe schema after connecting.

    * ``EVENTPULSE_DB_SCHEMA`` set: the connection's current schema must be exactly that.
    * Non-local host: the current schema must not be ``public`` (Supabase exposes ``public``
      through its Data API) unless ``EVENTPULSE_DB_ALLOW_PUBLIC_SCHEMA=1``.
    """
    env = os.environ if env is None else env
    expected = (env.get("EVENTPULSE_DB_SCHEMA") or "").strip()
    if schema is None:
        raise PgError("EP002", "The database role's search_path names no existing schema. Create the "
                               "private schema first (see scripts/postgres/setup_private_schema.sql).")
    if expected and schema != expected:
        raise PgError("EP002", f"Connected to schema '{schema}' but EVENTPULSE_DB_SCHEMA is '{expected}'.")
    if (not is_local_url(url) and schema == "public"
            and env.get("EVENTPULSE_DB_ALLOW_PUBLIC_SCHEMA") != "1"):
        raise PgError("EP002", "Refusing to use the 'public' schema on a non-local database (it can be "
                               "exposed by the provider's Data API). Use a private schema such as "
                               "'eventpulse' via the role's search_path.")


# --------------------------------------------------------------------------- SQL translation
def to_pyformat(sql: str) -> str:
    """``?`` -> ``%s`` outside string literals, quoted identifiers and comments; ``%`` -> ``%%``."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c in ("'", '"'):                                  # literal or quoted identifier
            j = i + 1
            while j < n:
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:        # doubled quote = escaped quote
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i:j + 1].replace("%", "%%"))
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(sql[i:j].replace("%", "%%"))
            i = j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(sql[i:j].replace("%", "%%"))
            i = j
        elif c == "?":
            out.append("%s")
            i += 1
        elif c == "%":
            out.append("%%")
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


_BEGIN = re.compile(r"^\s*BEGIN\s+(IMMEDIATE|DEFERRED|EXCLUSIVE)\b", re.I)
_PRAGMA = re.compile(r"^\s*PRAGMA\b", re.I)
_INSERT = re.compile(r"^\s*INSERT\b", re.I)
_RETURNING = re.compile(r"\bRETURNING\b", re.I)


def rewrite(sql: str) -> tuple[Optional[str], bool]:
    """Return (statement for PostgreSQL or None for a no-op, wants_lastrowid)."""
    if _PRAGMA.match(sql):
        return None, False
    sql = _BEGIN.sub("BEGIN", sql)
    if _INSERT.match(sql) and not _RETURNING.search(sql):
        return sql.rstrip().rstrip(";") + " RETURNING *", True
    return sql, False


def _param(v: Any) -> Any:
    if isinstance(v, bool):                                   # booleans are INTEGER 0/1 columns, as in SQLite
        return int(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if type(v).__module__ == "numpy" and hasattr(v, "item"):  # numpy scalar -> Python number
        v = v.item()
        if isinstance(v, bool):
            return int(v)
    if isinstance(v, float):
        if v != v:                                            # NaN: SQLite stores NULL
            return None
        if v.is_integer() and abs(v) < 2 ** 53:               # 13.0 -> 13 (SQLite INTEGER affinity does this;
            return int(v)                                     #  a DOUBLE PRECISION column accepts 13 unchanged)
    return v


# --------------------------------------------------------------------------- rows and cursors
class Row(tuple):
    """Tuple with column-name access, like ``sqlite3.Row``."""

    def __new__(cls, values: Sequence[Any], index: dict[str, int]):
        r = super().__new__(cls, values)
        r._index = index
        return r

    def __getitem__(self, key):
        if isinstance(key, str):
            return tuple.__getitem__(self, self._index[key])
        return tuple.__getitem__(self, key)

    def keys(self) -> list[str]:
        return list(self._index)


class Cursor:
    def __init__(self, columns: list[str], rows: list[tuple], rowcount: int, lastrowid: Any = None):
        index = {c: i for i, c in enumerate(columns)}
        self._rows = [Row(r, index) for r in rows]
        self._pos = 0
        self.rowcount, self.lastrowid = rowcount, lastrowid
        self.description = [(c, None, None, None, None, None, None) for c in columns] if columns else None

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        self._pos += 1
        return self._rows[self._pos - 1]

    def fetchall(self):
        rest, self._pos = self._rows[self._pos:], len(self._rows)
        return rest

    def __iter__(self):
        return iter(self.fetchall())


# --------------------------------------------------------------------------- drivers
class _PsycopgRaw:
    """Production driver (psycopg 3). Autocommit: transactions are explicit BEGIN/COMMIT,
    exactly like the SQLite connection (isolation_level=None)."""

    def __init__(self, url: str):
        import psycopg                                        # imported only when PostgreSQL is used
        from psycopg.types.numeric import FloatLoader
        self._psycopg = psycopg
        # prepare_threshold=None: no server-side prepared statements (safe with transaction poolers)
        try:
            self._conn = psycopg.connect(url, autocommit=True, prepare_threshold=None)
        except psycopg.Error as e:                            # timeout, refused, SSL/certificate failure
            err = self._err(e)
            if err.sqlstate == "XX000":                       # connection errors carry no SQLSTATE
                err.sqlstate = "08001"
            raise err from e
        self._conn.adapters.register_loader("numeric", FloatLoader)   # AVG/ROUND -> float, as in SQLite

    def _err(self, e) -> PgError:
        d = getattr(e, "diag", None)
        return PgError(getattr(e, "sqlstate", None) or "XX000",
                       (d.message_primary if d and d.message_primary else str(e)).strip(),
                       constraint=getattr(d, "constraint_name", None), table=getattr(d, "table_name", None),
                       detail=getattr(d, "message_detail", None))

    def query(self, sql: str, params):
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [c.name for c in cur.description] if cur.description else []
                rows = [tuple(r) for r in cur.fetchall()] if cur.description else []
                return cols, rows, cur.rowcount
        except self._psycopg.Error as e:
            raise self._err(e) from e

    def script(self, sql: str) -> None:
        try:
            self._conn.execute(sql)                           # no parameters: several statements allowed
        except self._psycopg.Error as e:
            raise self._err(e) from e

    def close(self) -> None:
        self._conn.close()


_driver_factory: Optional[Callable[[str], Any]] = None


def set_driver_factory(factory: Optional[Callable[[str], Any]]) -> None:
    """Use another raw driver (tests). ``None`` restores psycopg."""
    global _driver_factory
    _driver_factory = factory


def _raw_connect(url: str):
    if _driver_factory is not None:
        return _driver_factory(url)
    try:
        return _PsycopgRaw(url)
    except ImportError as exc:
        raise PgError("08001", "PostgreSQL support needs the psycopg package "
                               "(pip install \"psycopg[binary]\").") from exc


class PgConnection:
    """sqlite3-like connection on top of a raw PostgreSQL driver."""

    dialect = "postgresql"

    def __init__(self, url: str):
        self.url_redacted = redact(url)
        self._raw = _raw_connect(prepare_url(url))           # SSL / timeout / application name
        try:
            self.schema = self._raw.query("SELECT current_schema()", None)[1][0][0]
            check_schema(self.schema, url)
        except Exception:
            self._raw.close()
            raise

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Cursor:
        stmt, wants_id = rewrite(sql)
        if stmt is None:
            return Cursor([], [], -1)
        params = tuple(_param(p) for p in params) if params else ()
        if params:
            cols, rows, count = self._raw.query(to_pyformat(stmt), params)
        else:
            cols, rows, count = self._raw.query(stmt, None)
        last = rows[0][0] if wants_id and rows and isinstance(rows[0][0], int) else None
        return Cursor([] if wants_id else cols, [] if wants_id else rows, count, last)

    def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> Cursor:
        total = 0
        for p in seq:
            total += max(self.execute(sql, p).rowcount, 0)
        return Cursor([], [], total)

    def executescript(self, sql: str) -> None:
        self._raw.script(sql)

    def close(self) -> None:
        self._raw.close()


def connect(url: str) -> PgConnection:
    return PgConnection(url)
