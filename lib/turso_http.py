"""HTTP-only Turso client with a sqlite3-compatible interface.

`libsql-experimental` (the native libSQL Python client) requires Rust +
cmake to build from source and doesn't have prebuilt wheels for Python
3.14 as of mid-2026. Streamlit Cloud runs Python 3.14, so we can't use
it there. This module provides a pure-Python replacement that talks to
Turso's HTTP API (`/v2/pipeline`) using only `requests`.

Design: expose enough of the sqlite3.Connection / Cursor / Row surface
that the rest of the codebase (which was written against sqlite3) works
unchanged. Every `execute` / `executemany` / `executescript` call
becomes one HTTP round trip, so this is slower than an embedded DB —
but for the ~1 query per user action volume of the Streamlit UI it's
fine.

Value type mapping matches Turso's Hrana protocol:
    Python None     -> {"type": "null"}
    Python bool     -> {"type": "integer", "value": "0" or "1"}
    Python int      -> {"type": "integer", "value": str(v)}
    Python float    -> {"type": "float", "value": float(v)}
    Python bytes    -> {"type": "blob", "base64": ...}
    Python str      -> {"type": "text", "value": v}

Turso responses use the same typed shape; we decode them back into
Python natives before handing rows to callers.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Iterable, Sequence

import requests

LOG = logging.getLogger(__name__)


class TursoError(Exception):
    """Raised for Turso HTTP-side failures (network, HTTP status, SQL error)."""


class TursoRow:
    """A row that supports both integer indexing and string (column) access.

    Mimics sqlite3.Row enough that existing code doing `row["col"]` and
    `row[0]` keeps working.
    """

    __slots__ = ("_values", "_cols")

    def __init__(self, values: list, cols: list[str]) -> None:
        self._values = values
        self._cols = cols

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        # str
        try:
            i = self._cols.index(key)
        except ValueError:
            raise KeyError(key)
        return self._values[i]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        pairs = ", ".join(f"{k}={v!r}" for k, v in zip(self._cols, self._values))
        return f"TursoRow({pairs})"

    def keys(self):
        return list(self._cols)


class TursoCursor:
    """sqlite3.Cursor-shaped wrapper for a single execute result."""

    def __init__(
        self, conn: "TursoConnection",
        cols: list[str] | None = None,
        rows: list[list] | None = None,
        rowcount: int = 0,
        lastrowid: int | None = None,
    ):
        self._conn = conn
        self._cols = cols or []
        self._rows: list[list] = rows or []
        self._iter_pos = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        # sqlite3.Cursor.description is a list of 7-tuples per column.
        self.description = (
            [(c, None, None, None, None, None, None) for c in self._cols]
            if self._cols else None
        )

    # -- sqlite3.Cursor API --

    def execute(self, sql: str, params: Sequence | None = None):
        cur = self._conn.execute(sql, params)
        # Replace state with the new cursor's.
        self._cols = cur._cols
        self._rows = cur._rows
        self._iter_pos = 0
        self.rowcount = cur.rowcount
        self.lastrowid = cur.lastrowid
        self.description = cur.description
        return self

    def executemany(self, sql: str, param_seq: Iterable[Sequence]):
        return self._conn.executemany(sql, param_seq)

    def fetchone(self):
        if self._iter_pos >= len(self._rows):
            return None
        row = self._rows[self._iter_pos]
        self._iter_pos += 1
        return TursoRow(row, self._cols) if self._conn.row_factory else row

    def fetchall(self):
        remaining = self._rows[self._iter_pos:]
        self._iter_pos = len(self._rows)
        if self._conn.row_factory:
            return [TursoRow(r, self._cols) for r in remaining]
        return [tuple(r) for r in remaining]

    def fetchmany(self, size: int = 1):
        chunk = self._rows[self._iter_pos:self._iter_pos + size]
        self._iter_pos += len(chunk)
        if self._conn.row_factory:
            return [TursoRow(r, self._cols) for r in chunk]
        return [tuple(r) for r in chunk]

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row

    def close(self):
        # No per-cursor state on Turso side; nothing to do.
        pass


class TursoConnection:
    """sqlite3.Connection-shaped wrapper over Turso's HTTP API."""

    # sqlite3-compatible attribute. Setting this to sqlite3.Row (or any
    # truthy value) makes cursor rows come back as TursoRow instead of
    # plain tuples. Existing code sets `conn.row_factory = sqlite3.Row`.
    row_factory: Any = None

    def __init__(self, http_url: str, auth_token: str, timeout_s: int = 60):
        self._http_url = http_url.rstrip("/")
        self._pipeline_url = f"{self._http_url}/v2/pipeline"
        self._auth = f"Bearer {auth_token}"
        self._timeout_s = timeout_s
        self._session = requests.Session()

    # -- sqlite3.Connection API --

    def execute(self, sql: str, params: Sequence | None = None) -> TursoCursor:
        stmts = self._split_multi(sql)
        cursors: list[TursoCursor] = []
        # Group multi-statement single-execute together in one pipeline
        # so we save HTTP round trips.
        payload = []
        for i, stmt_sql in enumerate(stmts):
            # Only the last statement (or a single-statement input) gets
            # the params; sqlite3.Connection.execute() only supports one
            # statement anyway. If someone passed a multi-statement SQL,
            # they meant it as executescript() semantics.
            args = params if (i == len(stmts) - 1 and params is not None) else None
            payload.append(_stmt(stmt_sql, args))
        results = self._pipeline(payload)
        for r in results:
            cursors.append(self._result_to_cursor(r))
        # Return the last cursor (that's what sqlite3 does for a single-
        # execute call; for scripts, callers don't consume the return).
        return cursors[-1] if cursors else TursoCursor(self)

    def executemany(self, sql: str, param_seq: Iterable[Sequence]) -> TursoCursor:
        payload = [_stmt(sql, params) for params in param_seq]
        if not payload:
            return TursoCursor(self)
        results = self._pipeline(payload)
        # For executemany we sum affected rows and take the last lastrowid.
        total = 0
        last_rowid = None
        for r in results:
            total += r.get("result", {}).get("affected_row_count") or 0
            lri = r.get("result", {}).get("last_insert_rowid")
            if lri:
                try:
                    last_rowid = int(lri)
                except (TypeError, ValueError):
                    pass
        return TursoCursor(self, cols=[], rows=[], rowcount=total, lastrowid=last_rowid)

    def executescript(self, sql: str) -> TursoCursor:
        stmts = self._split_multi(sql)
        payload = [_stmt(s) for s in stmts]
        if not payload:
            return TursoCursor(self)
        self._pipeline(payload)
        return TursoCursor(self)

    def commit(self):
        # Turso auto-commits per request. No-op.
        pass

    def rollback(self):
        # Auto-commit semantics — nothing local to roll back.
        pass

    def close(self):
        try:
            self._session.close()
        except Exception:  # noqa: BLE001
            pass

    # Context-manager support: `with connect(...) as conn:` semantics.
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # -- internals --

    def _pipeline(self, requests_payload: list[dict]) -> list[dict]:
        body = {"requests": requests_payload + [{"type": "close"}]}
        try:
            r = self._session.post(
                self._pipeline_url,
                headers={"Authorization": self._auth, "Content-Type": "application/json"},
                data=json.dumps(body),
                timeout=self._timeout_s,
            )
        except requests.RequestException as exc:
            raise TursoError(f"network error: {exc}") from exc
        if not r.ok:
            raise TursoError(f"HTTP {r.status_code}: {r.text[:500]}")
        try:
            data = r.json()
        except ValueError as exc:
            raise TursoError(f"non-JSON response: {r.text[:200]}") from exc
        out = []
        for i, res in enumerate(data.get("results") or []):
            if res.get("type") == "error":
                err = res.get("error") or {}
                raise TursoError(f"step {i}: {err.get('message') or err}")
            resp = res.get("response") or {}
            # Only 'execute' responses have a `result`; 'close' has none.
            if resp.get("type") == "execute":
                out.append(resp)
        return out

    def _result_to_cursor(self, resp: dict) -> TursoCursor:
        result = resp.get("result") or {}
        col_defs = result.get("cols") or []
        cols = [c.get("name") for c in col_defs if isinstance(c, dict)]
        raw_rows = result.get("rows") or []
        rows: list[list] = []
        for raw in raw_rows:
            if not isinstance(raw, list):
                continue
            rows.append([_decode_value(cell) for cell in raw])
        rowcount = result.get("affected_row_count") or (len(rows) if rows else 0)
        lri = result.get("last_insert_rowid")
        try:
            lastrowid = int(lri) if lri else None
        except (TypeError, ValueError):
            lastrowid = None
        return TursoCursor(self, cols=cols, rows=rows, rowcount=rowcount, lastrowid=lastrowid)

    def _split_multi(self, sql: str) -> list[str]:
        """Split a script string into individual statements.

        SQLite's semicolon is the statement separator. This isn't a
        full SQL parser — it's naive to semicolons inside string
        literals — but our code doesn't have any, so this is fine.

        Chunks containing only comments/whitespace are dropped: a
        semicolon at the end of a `--` comment line splits the script
        there, and Turso rejects a comment-only "statement" with
        'SQL string does not contain any statement' (hit 2026-07-07
        by the quota-ledger schema banner).
        """
        parts = [s.strip() for s in re.split(r";\s*(?:\n|$)", sql)]
        out = []
        for p in parts:
            if not p:
                continue
            has_sql = any(
                line.strip() and not line.strip().startswith("--")
                for line in p.splitlines()
            )
            if has_sql:
                out.append(p)
        return out


def _stmt(sql: str, args: Sequence | None = None) -> dict:
    """Wrap a SQL string + args into a Turso pipeline 'execute' request."""
    stmt: dict[str, Any] = {"sql": sql}
    if args is not None:
        stmt["args"] = [_encode_value(a) for a in args]
    return {"type": "execute", "stmt": stmt}


def _encode_value(v) -> dict:
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(v).decode("ascii")}
    return {"type": "text", "value": str(v)}


def _decode_value(cell: dict):
    if not isinstance(cell, dict):
        return cell
    t = cell.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(cell.get("value") or 0)
    if t == "float":
        v = cell.get("value")
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    if t == "blob":
        b = cell.get("base64")
        try:
            return base64.b64decode(b) if b else b""
        except Exception:  # noqa: BLE001
            return b""
    # text (default)
    return cell.get("value")


def connect(url: str, token: str, timeout_s: int = 60) -> TursoConnection:
    """Open a Turso HTTP connection.

    `url` may be either `libsql://...` (as Turso reports) or `https://...`.
    We normalize to https here since we're hitting the HTTP API.
    """
    http_url = url.replace("libsql://", "https://", 1)
    return TursoConnection(http_url, token, timeout_s=timeout_s)
