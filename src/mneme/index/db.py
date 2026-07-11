from __future__ import annotations

import os
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any, Iterable

from mneme.normalize.operations import OperationCard
from mneme.util import json_dumps_compact, json_loads_maybe


def default_db_path() -> str:
    """Return the default SQLite index path.

    Resolution order:
    1. ``$MNEME_DB`` if set.
    2. ``$XDG_DATA_HOME/mneme/mneme.db`` if set.
    3. ``%LOCALAPPDATA%\\mneme\\mneme.db`` on Windows.
    4. ``~/.local/share/mneme/mneme.db`` on Linux/macOS.
    """

    env = os.environ.get("MNEME_DB")
    if env:
        return env

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return str(Path(xdg) / "mneme" / "mneme.db")

    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return str(Path(base) / "mneme" / "mneme.db")

    return str(Path.home() / ".local" / "share" / "mneme" / "mneme.db")


DEFAULT_DB_PATH = default_db_path()


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS specs (
  spec_id TEXT PRIMARY KEY,
  source_url TEXT,
  title TEXT,
  version TEXT,
  provider_domain TEXT,
  fetched_at TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  openapi_version TEXT,
  raw_json TEXT NOT NULL,
  discovered_via TEXT,
  status TEXT NOT NULL DEFAULT 'ok',
  error TEXT
);

CREATE TABLE IF NOT EXISTS operations (
  operation_id TEXT PRIMARY KEY,
  spec_id TEXT NOT NULL REFERENCES specs(spec_id) ON DELETE CASCADE,
  api_title TEXT,
  api_version TEXT,
  provider_domain TEXT,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  operation_id_native TEXT,
  summary TEXT,
  description TEXT,
  tags TEXT NOT NULL,
  servers TEXT NOT NULL,
  auth TEXT NOT NULL,
  parameters TEXT NOT NULL,
  request_body TEXT,
  responses TEXT NOT NULL,
  agent_text TEXT NOT NULL,
  spec_slice TEXT NOT NULL,
  source_url TEXT,
  quality_score REAL NOT NULL,
  fetched_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS operations_fts USING fts5(
  operation_id UNINDEXED,
  spec_id UNINDEXED,
  api_title,
  provider_domain,
  method,
  path,
  summary,
  description,
  tags,
  agent_text
);

CREATE INDEX IF NOT EXISTS idx_operations_spec_id ON operations(spec_id);
CREATE INDEX IF NOT EXISTS idx_operations_provider_domain ON operations(provider_domain);
CREATE INDEX IF NOT EXISTS idx_operations_method ON operations(method);
CREATE INDEX IF NOT EXISTS idx_operations_path ON operations(path);

CREATE TABLE IF NOT EXISTS library_packages (
  package_id TEXT PRIMARY KEY,
  language TEXT NOT NULL,
  name TEXT NOT NULL,
  version TEXT,
  source TEXT NOT NULL,
  source_url TEXT,
  homepage TEXT,
  summary TEXT,
  fetched_at TEXT NOT NULL,
  content_hash TEXT,
  status TEXT NOT NULL DEFAULT 'ok',
  error TEXT
);

CREATE TABLE IF NOT EXISTS library_symbols (
  symbol_id TEXT PRIMARY KEY,
  package_id TEXT NOT NULL REFERENCES library_packages(package_id) ON DELETE CASCADE,
  language TEXT NOT NULL,
  kind TEXT NOT NULL,
  package_name TEXT NOT NULL,
  module_path TEXT NOT NULL,
  qualified_name TEXT NOT NULL,
  symbol_name TEXT NOT NULL,
  signature TEXT,
  summary TEXT,
  description TEXT,
  parameters TEXT NOT NULL DEFAULT '[]',
  returns TEXT,
  tags TEXT NOT NULL DEFAULT '[]',
  agent_text TEXT NOT NULL,
  source_url TEXT,
  quality_score REAL NOT NULL DEFAULT 0.0,
  fetched_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS library_symbols_fts USING fts5(
  symbol_id UNINDEXED,
  package_id UNINDEXED,
  language UNINDEXED,
  package_name,
  module_path,
  qualified_name,
  symbol_name,
  signature,
  summary,
  description,
  tags,
  agent_text
);

CREATE INDEX IF NOT EXISTS idx_library_symbols_package_id ON library_symbols(package_id);
CREATE INDEX IF NOT EXISTS idx_library_symbols_language ON library_symbols(language);
CREATE INDEX IF NOT EXISTS idx_library_symbols_package_name ON library_symbols(package_name);
CREATE INDEX IF NOT EXISTS idx_library_symbols_kind ON library_symbols(kind);
"""


class MnemeDB:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        if self.path.parent and str(self.path.parent) != ".":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # FastAPI executes synchronous handlers in worker threads. SQLite is compiled
        # in serialized mode on supported Python builds, so permit that shared app
        # connection to be used by those workers.
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._write_lock = threading.RLock()
        self.init()

    def close(self) -> None:
        self.conn.close()

    def init(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def upsert_spec(
        self,
        *,
        spec_meta: dict[str, Any],
        raw_json: dict[str, Any],
        operations: Iterable[OperationCard],
        discovered_via: str | None = None,
    ) -> int:
        operations_list = list(operations)
        spec_id = spec_meta["spec_id"]
        with self._write_lock, self.conn:
            self.conn.execute("DELETE FROM operations_fts WHERE spec_id = ?", (spec_id,))
            self.conn.execute("DELETE FROM operations WHERE spec_id = ?", (spec_id,))
            self.conn.execute(
                """
                INSERT INTO specs (
                  spec_id, source_url, title, version, provider_domain, fetched_at,
                  content_hash, openapi_version, raw_json, discovered_via, status, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', NULL)
                ON CONFLICT(spec_id) DO UPDATE SET
                  source_url=excluded.source_url,
                  title=excluded.title,
                  version=excluded.version,
                  provider_domain=excluded.provider_domain,
                  fetched_at=excluded.fetched_at,
                  content_hash=excluded.content_hash,
                  openapi_version=excluded.openapi_version,
                  raw_json=excluded.raw_json,
                  discovered_via=excluded.discovered_via,
                  status='ok',
                  error=NULL
                """,
                (
                    spec_id,
                    spec_meta.get("source_url"),
                    spec_meta.get("title"),
                    spec_meta.get("version"),
                    spec_meta.get("provider_domain"),
                    spec_meta.get("fetched_at"),
                    spec_meta.get("content_hash"),
                    spec_meta.get("openapi_version"),
                    json_dumps_compact(raw_json),
                    discovered_via,
                ),
            )
            for op in operations_list:
                self._insert_operation(op)
        return len(operations_list)

    def record_failed_spec(
        self,
        *,
        spec_id: str,
        source_url: str,
        fetched_at: str,
        content_hash: str,
        error: str,
        discovered_via: str | None = None,
    ) -> None:
        with self._write_lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO specs (
                  spec_id, source_url, title, version, provider_domain, fetched_at,
                  content_hash, openapi_version, raw_json, discovered_via, status, error
                ) VALUES (?, ?, NULL, NULL, NULL, ?, ?, NULL, '{}', ?, 'error', ?)
                ON CONFLICT(spec_id) DO UPDATE SET
                  fetched_at=excluded.fetched_at,
                  content_hash=excluded.content_hash,
                  discovered_via=excluded.discovered_via,
                  status='error',
                  error=excluded.error
                """,
                (spec_id, source_url, fetched_at, content_hash, discovered_via, error),
            )

    def _insert_operation(self, op: OperationCard) -> None:
        self.conn.execute(
            """
            INSERT INTO operations (
              operation_id, spec_id, api_title, api_version, provider_domain,
              method, path, operation_id_native, summary, description, tags,
              servers, auth, parameters, request_body, responses, agent_text,
              spec_slice, source_url, quality_score, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                op.operation_id,
                op.spec_id,
                op.api_title,
                op.api_version,
                op.provider_domain,
                op.method,
                op.path,
                op.operation_id_native,
                op.summary,
                op.description,
                json_dumps_compact(op.tags),
                json_dumps_compact(op.servers),
                json_dumps_compact(op.auth),
                json_dumps_compact(op.parameters),
                json_dumps_compact(op.request_body) if op.request_body is not None else None,
                json_dumps_compact(op.responses),
                op.agent_text,
                json_dumps_compact(op.spec_slice),
                op.source_url,
                op.quality_score,
                op.fetched_at,
            ),
        )
        self.conn.execute(
            """
            INSERT INTO operations_fts (
              operation_id, spec_id, api_title, provider_domain, method, path,
              summary, description, tags, agent_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                op.operation_id,
                op.spec_id,
                op.api_title or "",
                op.provider_domain or "",
                op.method,
                op.path,
                op.summary or "",
                op.description or "",
                " ".join(op.tags),
                op.agent_text,
            ),
        )

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if row is None:
            return None
        return row_to_operation_dict(row)

    def get_spec(self, spec_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM specs WHERE spec_id = ?", (spec_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["raw_json"] = json_loads_maybe(data.get("raw_json"), {})
        return data

    def list_specs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        provider_domain: str | None = None,
        query: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List spec metadata and operation counts without loading raw documents."""

        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("s.status = ?")
            params.append(status)
        if provider_domain:
            where.append("s.provider_domain = ?")
            params.append(provider_domain)
        if query:
            where.append("(s.title LIKE ? OR s.provider_domain LIKE ? OR s.source_url LIKE ?)")
            match = f"%{query}%"
            params.extend((match, match, match))
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        total = int(
            self.conn.execute(
                f"SELECT COUNT(*) FROM specs s {where_sql}",  # noqa: S608
                params,
            ).fetchone()[0]
        )
        rows = self.conn.execute(
            f"""
            SELECT s.spec_id, s.source_url, s.title, s.version, s.provider_domain,
                   s.fetched_at, s.content_hash, s.openapi_version, s.discovered_via,
                   s.status, s.error, COUNT(o.operation_id) AS operation_count
            FROM specs s
            LEFT JOIN operations o ON o.spec_id = s.spec_id
            {where_sql}
            GROUP BY s.spec_id
            ORDER BY s.fetched_at DESC, s.title COLLATE NOCASE, s.spec_id
            LIMIT ? OFFSET ?
            """,  # noqa: S608
            (*params, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows], total

    def get_spec_metadata(self, spec_id: str) -> dict[str, Any] | None:
        """Get spec metadata and count while deliberately excluding raw_json."""

        row = self.conn.execute(
            """
            SELECT s.spec_id, s.source_url, s.title, s.version, s.provider_domain,
                   s.fetched_at, s.content_hash, s.openapi_version, s.discovered_via,
                   s.status, s.error, COUNT(o.operation_id) AS operation_count
            FROM specs s
            LEFT JOIN operations o ON o.spec_id = s.spec_id
            WHERE s.spec_id = ?
            GROUP BY s.spec_id
            """,
            (spec_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def delete_spec(self, spec_id: str) -> dict[str, int] | None:
        """Delete a spec and its operation/FTS rows atomically."""

        exists = self.conn.execute("SELECT 1 FROM specs WHERE spec_id = ?", (spec_id,)).fetchone()
        if exists is None:
            return None
        operation_count = int(
            self.conn.execute(
                "SELECT COUNT(*) FROM operations WHERE spec_id = ?", (spec_id,)
            ).fetchone()[0]
        )
        fts_count = int(
            self.conn.execute(
                "SELECT COUNT(*) FROM operations_fts WHERE spec_id = ?", (spec_id,)
            ).fetchone()[0]
        )
        with self._write_lock, self.conn:
            self.conn.execute("DELETE FROM operations_fts WHERE spec_id = ?", (spec_id,))
            self.conn.execute("DELETE FROM operations WHERE spec_id = ?", (spec_id,))
            self.conn.execute("DELETE FROM specs WHERE spec_id = ?", (spec_id,))
        return {"operations": operation_count, "fts_rows": fts_count}

    def list_operations(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        spec_id: str | None = None,
        provider_domain: str | None = None,
        method: str | None = None,
        query: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List compact operation cards with optional catalog filters."""

        where: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("spec_id", spec_id),
            ("provider_domain", provider_domain),
            ("method", method.upper() if method else None),
        ):
            if value:
                where.append(f"{column} = ?")
                params.append(value)
        if query:
            where.append(
                "(summary LIKE ? OR description LIKE ? OR path LIKE ? "
                "OR operation_id_native LIKE ? OR api_title LIKE ?)"
            )
            match = f"%{query}%"
            params.extend((match, match, match, match, match))
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        total = int(
            self.conn.execute(
                f"SELECT COUNT(*) FROM operations {where_sql}",  # noqa: S608
                params,
            ).fetchone()[0]
        )
        rows = self.conn.execute(
            f"""
            SELECT operation_id, spec_id, api_title, api_version, provider_domain,
                   method, path, operation_id_native, summary, description, tags,
                   source_url, quality_score, fetched_at
            FROM operations
            {where_sql}
            ORDER BY api_title COLLATE NOCASE, path, method, operation_id
            LIMIT ? OFFSET ?
            """,  # noqa: S608
            (*params, limit, offset),
        ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["tags"] = json_loads_maybe(item.get("tags"), [])
        return items, total

    def stats(self) -> dict[str, Any]:
        specs = self.conn.execute("SELECT COUNT(*) FROM specs WHERE status = 'ok'").fetchone()[0]
        errors = self.conn.execute("SELECT COUNT(*) FROM specs WHERE status = 'error'").fetchone()[
            0
        ]
        operations = self.conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
        providers = self.conn.execute(
            "SELECT COUNT(DISTINCT provider_domain) FROM operations WHERE provider_domain IS NOT NULL"
        ).fetchone()[0]
        libraries = self.conn.execute(
            "SELECT COUNT(*) FROM library_packages WHERE status = 'ok'"
        ).fetchone()[0]
        library_symbols = self.conn.execute("SELECT COUNT(*) FROM library_symbols").fetchone()[0]
        by_language = {}
        for row in self.conn.execute(
            "SELECT language, COUNT(*) AS n FROM library_symbols GROUP BY language"
        ):
            by_language[row["language"]] = int(row["n"])
        return {
            "db_path": str(self.path),
            "specs": specs,
            "failed_specs": errors,
            "operations": operations,
            "providers": providers,
            "libraries": libraries,
            "library_symbols": library_symbols,
            "library_symbols_by_language": by_language,
        }

    def catalog_summary(self) -> dict[str, Any]:
        """Return a compact inventory of the capabilities in this index."""

        provider_rows = self.conn.execute(
            """
            SELECT provider_domain, api_title, COUNT(*) AS operation_count
            FROM operations
            WHERE provider_domain IS NOT NULL
            GROUP BY provider_domain, api_title
            ORDER BY provider_domain, api_title
            """
        )
        providers_by_domain: dict[str, dict[str, Any]] = {}
        for row in provider_rows:
            domain = str(row["provider_domain"])
            provider = providers_by_domain.setdefault(
                domain,
                {
                    "provider_domain": domain,
                    "operation_count": 0,
                    "api_titles": [],
                },
            )
            provider["operation_count"] += int(row["operation_count"])
            if row["api_title"]:
                provider["api_titles"].append(str(row["api_title"]))

        libraries = [
            {
                "language": package["language"],
                "name": package["name"],
                "version": package["version"],
            }
            for package in self.list_library_packages()
        ]
        return {
            **self.stats(),
            "indexed_providers": list(providers_by_domain.values()),
            "indexed_libraries": libraries,
        }

    def upsert_library_package(self, *, package: dict[str, Any]) -> None:
        """Insert or replace a library package row."""

        with self._write_lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO library_packages (
                  package_id, language, name, version, source, source_url, homepage,
                  summary, fetched_at, content_hash, status, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', NULL)
                ON CONFLICT(package_id) DO UPDATE SET
                  language=excluded.language,
                  name=excluded.name,
                  version=excluded.version,
                  source=excluded.source,
                  source_url=excluded.source_url,
                  homepage=excluded.homepage,
                  summary=excluded.summary,
                  fetched_at=excluded.fetched_at,
                  content_hash=excluded.content_hash,
                  status='ok',
                  error=NULL
                """,
                (
                    package["package_id"],
                    package["language"],
                    package["name"],
                    package.get("version"),
                    package["source"],
                    package.get("source_url"),
                    package.get("homepage"),
                    package.get("summary"),
                    package["fetched_at"],
                    package.get("content_hash"),
                ),
            )

    def replace_library_symbols(self, *, package_id: str, symbols: Iterable[dict[str, Any]]) -> int:
        """Replace all indexed symbols for a package atomically. Returns the new count."""

        symbols_list = list(symbols)
        with self._write_lock, self.conn:
            self.conn.execute("DELETE FROM library_symbols_fts WHERE package_id = ?", (package_id,))
            self.conn.execute("DELETE FROM library_symbols WHERE package_id = ?", (package_id,))
            for sym in symbols_list:
                self._insert_library_symbol(sym)
        return len(symbols_list)

    def _insert_library_symbol(self, sym: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO library_symbols (
              symbol_id, package_id, language, kind, package_name, module_path,
              qualified_name, symbol_name, signature, summary, description,
              parameters, returns, tags, agent_text, source_url, quality_score, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sym["symbol_id"],
                sym["package_id"],
                sym["language"],
                sym["kind"],
                sym["package_name"],
                sym["module_path"],
                sym["qualified_name"],
                sym["symbol_name"],
                sym.get("signature"),
                sym.get("summary"),
                sym.get("description"),
                json_dumps_compact(sym.get("parameters") or []),
                json_dumps_compact(sym.get("returns")) if sym.get("returns") is not None else None,
                json_dumps_compact(sym.get("tags") or []),
                sym["agent_text"],
                sym.get("source_url"),
                float(sym.get("quality_score", 0.0)),
                sym["fetched_at"],
            ),
        )
        self.conn.execute(
            """
            INSERT INTO library_symbols_fts (
              symbol_id, package_id, language, package_name, module_path,
              qualified_name, symbol_name, signature, summary, description, tags, agent_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sym["symbol_id"],
                sym["package_id"],
                sym["language"],
                sym["package_name"],
                sym["module_path"],
                sym["qualified_name"],
                sym["symbol_name"],
                sym.get("signature") or "",
                sym.get("summary") or "",
                sym.get("description") or "",
                " ".join(sym.get("tags") or []),
                sym["agent_text"],
            ),
        )

    def get_library_symbol(self, symbol_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM library_symbols WHERE symbol_id = ?", (symbol_id,)
        ).fetchone()
        if row is None:
            return None
        return row_to_library_symbol_dict(row)

    def get_library_package(self, package_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM library_packages WHERE package_id = ?", (package_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_library_packages(self, *, language: str | None = None) -> list[dict[str, Any]]:
        if language:
            cur = self.conn.execute(
                "SELECT * FROM library_packages WHERE language = ? AND status = 'ok' ORDER BY name",
                (language,),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM library_packages WHERE status = 'ok' ORDER BY language, name"
            )
        return [dict(r) for r in cur.fetchall()]


def row_to_operation_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key, default in [
        ("tags", []),
        ("servers", []),
        ("auth", {}),
        ("parameters", []),
        ("request_body", None),
        ("responses", {}),
        ("spec_slice", {}),
    ]:
        data[key] = json_loads_maybe(data.get(key), default)
    return data


def row_to_library_symbol_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key, default in [
        ("parameters", []),
        ("returns", None),
        ("tags", []),
    ]:
        data[key] = json_loads_maybe(data.get(key), default)
    return data
