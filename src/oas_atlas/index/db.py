from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

from oas_atlas.normalize.operations import OperationCard
from oas_atlas.util import json_dumps_compact, json_loads_maybe


def default_db_path() -> str:
    """Return the default SQLite index path.

    Resolution order:
    1. ``$OAS_ATLAS_DB`` if set.
    2. ``$XDG_DATA_HOME/oas-atlas/oas_atlas.db`` if set.
    3. ``%LOCALAPPDATA%\\oas-atlas\\oas_atlas.db`` on Windows.
    4. ``~/.local/share/oas-atlas/oas_atlas.db`` on Linux/macOS.
    """

    env = os.environ.get("OAS_ATLAS_DB")
    if env:
        return env

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return str(Path(xdg) / "oas-atlas" / "oas_atlas.db")

    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return str(Path(base) / "oas-atlas" / "oas_atlas.db")

    return str(Path.home() / ".local" / "share" / "oas-atlas" / "oas_atlas.db")


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
"""


class AtlasDB:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        if self.path.parent and str(self.path.parent) != ".":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
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
        with self.conn:
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
        with self.conn:
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

    def stats(self) -> dict[str, Any]:
        specs = self.conn.execute("SELECT COUNT(*) FROM specs WHERE status = 'ok'").fetchone()[0]
        errors = self.conn.execute("SELECT COUNT(*) FROM specs WHERE status = 'error'").fetchone()[
            0
        ]
        operations = self.conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
        providers = self.conn.execute(
            "SELECT COUNT(DISTINCT provider_domain) FROM operations WHERE provider_domain IS NOT NULL"
        ).fetchone()[0]
        return {
            "db_path": str(self.path),
            "specs": specs,
            "failed_specs": errors,
            "operations": operations,
            "providers": providers,
        }


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
