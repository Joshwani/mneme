from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


def default_notes_db_path() -> str:
    """Return the default SQLite notes database path.

    Resolution order:
    1. ``$MNEME_NOTES_DB`` if set.
    2. ``$XDG_DATA_HOME/mneme/notes.db`` if set.
    3. ``%LOCALAPPDATA%\\mneme\\notes.db`` on Windows.
    4. ``~/.local/share/mneme/notes.db`` on Linux/macOS.
    """

    env = os.environ.get("MNEME_NOTES_DB")
    if env:
        return env

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return str(Path(xdg) / "mneme" / "notes.db")

    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return str(Path(base) / "mneme" / "notes.db")

    return str(Path.home() / ".local" / "share" / "mneme" / "notes.db")


DEFAULT_NOTES_DB_PATH = default_notes_db_path()


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS notes (
  note_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '[]',
  scope TEXT,
  source TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
  note_id UNINDEXED,
  title,
  body,
  tags,
  scope
);

CREATE INDEX IF NOT EXISTS idx_notes_scope ON notes(scope);
CREATE INDEX IF NOT EXISTS idx_notes_updated_at ON notes(updated_at DESC);

CREATE TABLE IF NOT EXISTS workspace_scopes (
  scope TEXT PRIMARY KEY,
  base_path TEXT NOT NULL,
  max_bytes INTEGER NOT NULL DEFAULT 10485760,
  enabled_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_files (
  scope TEXT NOT NULL REFERENCES workspace_scopes(scope) ON DELETE CASCADE,
  rel_path TEXT NOT NULL,
  size INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (scope, rel_path)
);
"""


class NotesDB:
    """SQLite database for notes and workspace metadata.

    Kept deliberately separate from the operations/library index so users can
    back up, sync, or wipe their agent memory without touching the API catalog.
    """

    def __init__(self, path: str | Path = DEFAULT_NOTES_DB_PATH) -> None:
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
