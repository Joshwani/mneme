from __future__ import annotations

import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from mneme.util import sha256_hex, utc_now_iso_us

from .db import NotesDB


DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB per scope
DEFAULT_MAX_FILE_BYTES = 1 * 1024 * 1024  # 1 MiB per file
_SCOPE_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_.\-]{0,63}$")


class WorkspaceError(RuntimeError):
    """Raised when a workspace operation violates a safety invariant."""


@dataclass
class WorkspaceScope:
    scope: str
    base_path: str
    max_bytes: int
    enabled_at: str
    used_bytes: int = 0
    file_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkspaceFile:
    scope: str
    rel_path: str
    size: int
    content_hash: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_workspace_root() -> str:
    """Return the default workspace root directory.

    Resolution order mirrors the notes DB:
    ``$MNEME_WORKSPACE_ROOT`` -> ``$XDG_DATA_HOME/mneme/workspace`` ->
    ``%LOCALAPPDATA%\\mneme\\workspace`` -> ``~/.local/share/mneme/workspace``.
    """

    env = os.environ.get("MNEME_WORKSPACE_ROOT")
    if env:
        return env

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return str(Path(xdg) / "mneme" / "workspace")

    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return str(Path(base) / "mneme" / "workspace")

    return str(Path.home() / ".local" / "share" / "mneme" / "workspace")


def _validate_scope(scope: str) -> str:
    scope = (scope or "").strip()
    if not scope:
        raise WorkspaceError("scope is required")
    if not _SCOPE_RE.match(scope):
        raise WorkspaceError(
            "scope must match [a-zA-Z0-9_][a-zA-Z0-9_.\\-]{0,63} "
            "(letters, digits, underscore/dot/hyphen)"
        )
    return scope


def _safe_resolve(base_path: Path, rel_path: str) -> Path:
    """Resolve ``rel_path`` under ``base_path`` rejecting any escape attempts.

    Rules:
      - rel_path must not be empty.
      - rel_path must not be absolute.
      - rel_path must not contain ``..`` segments.
      - resolved path must remain under base_path (no symlink escapes).
      - no segment may resolve through a symlink.
    """

    if rel_path is None or rel_path == "":
        raise WorkspaceError("path is required")
    rel_path = rel_path.lstrip("/").lstrip("\\")
    if not rel_path:
        raise WorkspaceError("path resolved to empty after stripping leading slashes")
    if any(part in {"..", ""} for part in rel_path.replace("\\", "/").split("/")):
        raise WorkspaceError(f"path may not contain '..' segments: {rel_path!r}")

    candidate = (base_path / rel_path).resolve()
    base_resolved = base_path.resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise WorkspaceError(f"path escapes workspace scope: {rel_path!r}") from exc
    if candidate.is_symlink():
        raise WorkspaceError(f"refusing to follow symlink: {rel_path!r}")
    for parent in candidate.parents:
        if parent == base_resolved:
            break
        if parent.is_symlink():
            raise WorkspaceError(f"refusing to traverse symlinked dir: {parent}")
    return candidate


def enable_scope(
    db: NotesDB,
    scope: str,
    *,
    base_root: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> WorkspaceScope:
    scope = _validate_scope(scope)
    if max_bytes <= 0:
        raise WorkspaceError("max_bytes must be positive")
    root = Path(base_root or default_workspace_root()).expanduser()
    base_path = (root / scope).resolve()
    base_path.mkdir(parents=True, exist_ok=True)
    now = utc_now_iso_us()
    with db.conn:
        db.conn.execute(
            """
            INSERT INTO workspace_scopes (scope, base_path, max_bytes, enabled_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
              base_path=excluded.base_path,
              max_bytes=excluded.max_bytes
            """,
            (scope, str(base_path), int(max_bytes), now),
        )
    return _scope_with_usage(db, scope)


def disable_scope(db: NotesDB, scope: str, *, remove_files: bool = False) -> bool:
    scope = _validate_scope(scope)
    cur = db.conn.execute("SELECT base_path FROM workspace_scopes WHERE scope = ?", (scope,))
    row = cur.fetchone()
    if row is None:
        return False
    if remove_files:
        base_path = Path(row["base_path"])
        if base_path.exists():
            import shutil

            shutil.rmtree(base_path, ignore_errors=True)
    with db.conn:
        db.conn.execute("DELETE FROM workspace_scopes WHERE scope = ?", (scope,))
    return True


def list_scopes(db: NotesDB) -> list[WorkspaceScope]:
    cur = db.conn.execute("SELECT scope FROM workspace_scopes ORDER BY scope")
    return [_scope_with_usage(db, r["scope"]) for r in cur.fetchall()]


def workspace_status(db: NotesDB) -> dict[str, Any]:
    scopes = [s.to_dict() for s in list_scopes(db)]
    return {
        "enabled": bool(scopes),
        "default_root": default_workspace_root(),
        "scopes": scopes,
    }


def _scope_with_usage(db: NotesDB, scope: str) -> WorkspaceScope:
    cur = db.conn.execute(
        "SELECT scope, base_path, max_bytes, enabled_at FROM workspace_scopes WHERE scope = ?",
        (scope,),
    )
    row = cur.fetchone()
    if row is None:
        raise WorkspaceError(f"scope not enabled: {scope}")
    usage_row = db.conn.execute(
        "SELECT COALESCE(SUM(size), 0) AS used, COUNT(*) AS count FROM workspace_files WHERE scope = ?",
        (scope,),
    ).fetchone()
    return WorkspaceScope(
        scope=row["scope"],
        base_path=row["base_path"],
        max_bytes=int(row["max_bytes"]),
        enabled_at=row["enabled_at"],
        used_bytes=int(usage_row["used"] or 0),
        file_count=int(usage_row["count"] or 0),
    )


def _require_scope(db: NotesDB, scope: str) -> WorkspaceScope:
    return _scope_with_usage(db, _validate_scope(scope))


def list_workspace(db: NotesDB, scope: str, *, prefix: str = "") -> list[WorkspaceFile]:
    info = _require_scope(db, scope)
    sql = (
        "SELECT scope, rel_path, size, content_hash, updated_at "
        "FROM workspace_files WHERE scope = ?"
    )
    params: list[Any] = [info.scope]
    if prefix:
        sql += " AND rel_path LIKE ?"
        params.append(f"{prefix.lstrip('/')}%")
    sql += " ORDER BY rel_path"
    cur = db.conn.execute(sql, params)
    return [
        WorkspaceFile(
            scope=r["scope"],
            rel_path=r["rel_path"],
            size=int(r["size"]),
            content_hash=r["content_hash"],
            updated_at=r["updated_at"],
        )
        for r in cur.fetchall()
    ]


def read_workspace(db: NotesDB, scope: str, rel_path: str) -> dict[str, Any]:
    info = _require_scope(db, scope)
    full = _safe_resolve(Path(info.base_path), rel_path)
    if not full.exists() or not full.is_file():
        raise WorkspaceError(f"file not found: {rel_path}")
    data = full.read_bytes()
    try:
        text = data.decode("utf-8")
        binary = False
    except UnicodeDecodeError:
        import base64

        text = base64.b64encode(data).decode("ascii")
        binary = True
    return {
        "scope": info.scope,
        "rel_path": rel_path.lstrip("/"),
        "size": len(data),
        "content_hash": sha256_hex(data),
        "binary": binary,
        "content": text,
    }


def write_workspace(
    db: NotesDB,
    scope: str,
    rel_path: str,
    content: str | bytes,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> WorkspaceFile:
    info = _require_scope(db, scope)
    if isinstance(content, str):
        data = content.encode("utf-8")
    else:
        data = bytes(content)
    if len(data) > max_file_bytes:
        raise WorkspaceError(f"file too large: {len(data)} bytes (max {max_file_bytes})")

    rel = rel_path.lstrip("/").lstrip("\\")
    full = _safe_resolve(Path(info.base_path), rel)

    existing_size = 0
    if full.exists() and full.is_file():
        existing_size = full.stat().st_size
    projected = info.used_bytes - existing_size + len(data)
    if projected > info.max_bytes:
        raise WorkspaceError(f"scope quota exceeded: {projected} bytes > {info.max_bytes} bytes")

    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(data)
    now = utc_now_iso_us()
    digest = sha256_hex(data)
    with db.conn:
        db.conn.execute(
            """
            INSERT INTO workspace_files (scope, rel_path, size, content_hash, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope, rel_path) DO UPDATE SET
              size=excluded.size,
              content_hash=excluded.content_hash,
              updated_at=excluded.updated_at
            """,
            (info.scope, rel, len(data), digest, now),
        )
    return WorkspaceFile(
        scope=info.scope, rel_path=rel, size=len(data), content_hash=digest, updated_at=now
    )


def remove_workspace(db: NotesDB, scope: str, rel_path: str) -> bool:
    info = _require_scope(db, scope)
    rel = rel_path.lstrip("/").lstrip("\\")
    full = _safe_resolve(Path(info.base_path), rel)
    existed_in_db = False
    with db.conn:
        cur = db.conn.execute(
            "DELETE FROM workspace_files WHERE scope = ? AND rel_path = ?",
            (info.scope, rel),
        )
        existed_in_db = cur.rowcount > 0
    if full.exists() and full.is_file():
        full.unlink()
        return True
    return existed_in_db
