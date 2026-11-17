"""TEST-ONLY PostgreSQL driver over the system libpq library (ctypes, no package install).

Used only when a PostgreSQL server binary and ``libpq`` are present but no Python driver
(psycopg) is installed - e.g. the isolated test environment. Production uses psycopg
(``core/db/postgres.py``). This shim implements the same small "raw driver" interface:

    raw.query(sql, params)   # sql in psycopg's %s style when params is not None
    raw.script(sql)          # several statements, no parameters
    raw.close()

and mirrors psycopg's semantics that matter here: ``%s`` placeholders with ``%%`` for a
literal percent when parameters are passed; results converted by column type OID
(int2/4/8 -> int, float4/8 and numeric -> float, bool -> bool, everything else -> str);
SQL errors raised as ``core.db.errors.PgError`` with the SQLSTATE and constraint name.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import re
from datetime import date, datetime

from core.db.errors import PgError

_PATHS = ["libpq.so.5", ctypes.util.find_library("pq") or "", "/usr/lib/x86_64-linux-gnu/libpq.so.5",
          "/opt/homebrew/opt/libpq/lib/libpq.5.dylib", "/usr/local/opt/libpq/lib/libpq.5.dylib"]


def _load():
    for p in _PATHS:
        if not p:
            continue
        try:
            return ctypes.CDLL(p)
        except OSError:
            continue
    return None


_lib = _load()
AVAILABLE = _lib is not None

if AVAILABLE:
    vp, cp, ci = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int
    for name, res, args in [
        ("PQconnectdb", vp, [cp]), ("PQstatus", ci, [vp]), ("PQerrorMessage", cp, [vp]), ("PQfinish", None, [vp]),
        ("PQexec", vp, [vp, cp]),
        ("PQexecParams", vp, [vp, cp, ci, vp, ctypes.POINTER(cp), vp, vp, ci]),
        ("PQresultStatus", ci, [vp]), ("PQresultErrorMessage", cp, [vp]), ("PQresultErrorField", cp, [vp, ci]),
        ("PQntuples", ci, [vp]), ("PQnfields", ci, [vp]), ("PQfname", cp, [vp, ci]),
        ("PQftype", ctypes.c_uint, [vp, ci]), ("PQgetvalue", cp, [vp, ci, ci]), ("PQgetisnull", ci, [vp, ci, ci]),
        ("PQcmdTuples", cp, [vp]), ("PQclear", None, [vp]),
    ]:
        f = getattr(_lib, name)
        f.restype, f.argtypes = res, args

_COMMAND_OK, _TUPLES_OK, _EMPTY = 1, 2, 0
_INT = {20, 21, 23, 26}
_FLOAT = {700, 701, 1700}
_BOOL = {16}
_PCT = re.compile(r"%%|%s")


def _param(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return b"t" if v else b"f"
    if isinstance(v, (datetime, date)):
        return v.isoformat().encode()
    if isinstance(v, float):
        return repr(v).encode()
    return str(v).encode()


def _convert(raw: bytes, oid: int):
    s = raw.decode()
    if oid in _INT:
        return int(s)
    if oid in _FLOAT:
        return float(s)
    if oid in _BOOL:
        return s == "t"
    return s


class LibpqRaw:
    def __init__(self, conninfo: str):
        if not AVAILABLE:
            raise RuntimeError("libpq is not available")
        self._c = _lib.PQconnectdb(conninfo.encode())
        if _lib.PQstatus(self._c) != 0:
            msg = (_lib.PQerrorMessage(self._c) or b"").decode().strip()
            _lib.PQfinish(self._c)
            raise PgError("08001", f"could not connect: {msg}")

    def _check(self, res):
        if not res:
            raise PgError("08006", (_lib.PQerrorMessage(self._c) or b"").decode().strip())
        st = _lib.PQresultStatus(res)
        if st not in (_COMMAND_OK, _TUPLES_OK, _EMPTY):
            field = lambda code: (_lib.PQresultErrorField(res, code) or b"").decode() or None
            err = PgError(field(ord("C")) or "XX000", field(ord("M")) or "error",
                          constraint=field(ord("n")), table=field(ord("t")), detail=field(ord("D")))
            _lib.PQclear(res)
            raise err
        return st

    def query(self, sql: str, params):
        if params is None:
            res = _lib.PQexec(self._c, sql.encode())
        else:
            n = [0]

            def repl(m):
                if m.group(0) == "%%":
                    return "%"
                n[0] += 1
                return f"${n[0]}"
            native = _PCT.sub(repl, sql)
            if n[0] != len(params):
                raise PgError("08P01", f"{len(params)} parameters for {n[0]} placeholders")
            vals = (ctypes.c_char_p * max(1, len(params)))(*[_param(p) for p in params])
            res = _lib.PQexecParams(self._c, native.encode(), len(params), None, vals, None, None, 0)
        st = self._check(res)
        cols, rows = [], []
        if st == _TUPLES_OK:
            nf, nt = _lib.PQnfields(res), _lib.PQntuples(res)
            cols = [_lib.PQfname(res, j).decode() for j in range(nf)]
            oids = [_lib.PQftype(res, j) for j in range(nf)]
            for i in range(nt):
                rows.append(tuple(None if _lib.PQgetisnull(res, i, j) else _convert(_lib.PQgetvalue(res, i, j), oids[j])
                                  for j in range(nf)))
        count = (_lib.PQcmdTuples(res) or b"").decode()
        _lib.PQclear(res)
        return cols, rows, int(count) if count.isdigit() else (len(rows) if st == _TUPLES_OK else -1)

    def script(self, sql: str) -> None:
        res = _lib.PQexec(self._c, sql.encode())     # runs every statement; stops at the first error
        self._check(res)
        _lib.PQclear(res)

    def close(self) -> None:
        if self._c:
            _lib.PQfinish(self._c)
            self._c = None
