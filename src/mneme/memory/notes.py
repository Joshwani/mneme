from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from mneme.util import (
    json_dumps_compact,
    json_loads_maybe,
    stable_id,
    to_fts_query,
    utc_now_iso_us,
)

from .db import NotesDB


@dataclass
class Note:
    note_id: str
    title: str
    body: str
    tags: list[str] = field(default_factory=list)
    scope: str | None = None
    source: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_tags(tags: list[str] | str | None) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        items = [t.strip() for t in tags.split(",")]
    else:
        items = [str(t).strip() for t in tags]
    seen: set[str] = set()
    out: list[str] = []
    for t in items:
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _row_to_note(row: sqlite3.Row) -> Note:
    return Note(
        note_id=row["note_id"],
        title=row["title"],
        body=row["body"],
        tags=json_loads_maybe(row["tags"], default=[]) or [],
        scope=row["scope"],
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _make_note_id(title: str, created_at: str) -> str:
    return stable_id("note", f"{title}|{created_at}", length=16)


def add_note(
    db: NotesDB,
    *,
    title: str,
    body: str = "",
    tags: list[str] | str | None = None,
    scope: str | None = None,
    source: str | None = None,
) -> Note:
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")

    now = utc_now_iso_us()
    norm_tags = _normalize_tags(tags)
    note_id = _make_note_id(title, now)

    with db.conn:
        db.conn.execute(
            """
            INSERT INTO notes (
              note_id, title, body, tags, scope, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note_id,
                title,
                body or "",
                json_dumps_compact(norm_tags),
                scope,
                source,
                now,
                now,
            ),
        )
        db.conn.execute(
            "INSERT INTO notes_fts (note_id, title, body, tags, scope) VALUES (?, ?, ?, ?, ?)",
            (note_id, title, body or "", " ".join(norm_tags), scope or ""),
        )

    return Note(
        note_id=note_id,
        title=title,
        body=body or "",
        tags=norm_tags,
        scope=scope,
        source=source,
        created_at=now,
        updated_at=now,
    )


def update_note(
    db: NotesDB,
    note_id: str,
    *,
    title: str | None = None,
    body: str | None = None,
    tags: list[str] | str | None = None,
    scope: str | None = None,
) -> Note:
    cur = db.conn.execute("SELECT * FROM notes WHERE note_id = ?", (note_id,))
    row = cur.fetchone()
    if row is None:
        raise KeyError(f"note not found: {note_id}")

    new_title = title.strip() if title is not None else row["title"]
    if not new_title:
        raise ValueError("title cannot be empty")
    new_body = body if body is not None else row["body"]
    if tags is None:
        new_tags = json_loads_maybe(row["tags"], default=[]) or []
    else:
        new_tags = _normalize_tags(tags)
    new_scope = scope if scope is not None else row["scope"]
    now = utc_now_iso_us()

    with db.conn:
        db.conn.execute(
            """
            UPDATE notes
            SET title = ?, body = ?, tags = ?, scope = ?, updated_at = ?
            WHERE note_id = ?
            """,
            (
                new_title,
                new_body,
                json_dumps_compact(new_tags),
                new_scope,
                now,
                note_id,
            ),
        )
        db.conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (note_id,))
        db.conn.execute(
            "INSERT INTO notes_fts (note_id, title, body, tags, scope) VALUES (?, ?, ?, ?, ?)",
            (note_id, new_title, new_body, " ".join(new_tags), new_scope or ""),
        )

    return Note(
        note_id=note_id,
        title=new_title,
        body=new_body,
        tags=new_tags,
        scope=new_scope,
        source=row["source"],
        created_at=row["created_at"],
        updated_at=now,
    )


def delete_note(db: NotesDB, note_id: str) -> bool:
    with db.conn:
        cur = db.conn.execute("DELETE FROM notes WHERE note_id = ?", (note_id,))
        db.conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (note_id,))
    return cur.rowcount > 0


def get_note(db: NotesDB, note_id: str) -> Note | None:
    cur = db.conn.execute("SELECT * FROM notes WHERE note_id = ?", (note_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return _row_to_note(row)


def list_notes(
    db: NotesDB,
    *,
    scope: str | None = None,
    tag: str | None = None,
    limit: int = 50,
) -> list[Note]:
    sql = "SELECT * FROM notes"
    params: list[Any] = []
    clauses: list[str] = []
    if scope is not None:
        clauses.append("scope = ?")
        params.append(scope)
    if tag:
        clauses.append("tags LIKE ?")
        params.append(f'%"{tag}"%')
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(int(limit))

    cur = db.conn.execute(sql, params)
    return [_row_to_note(r) for r in cur.fetchall()]


def search_notes(
    db: NotesDB,
    query: str,
    *,
    scope: str | None = None,
    tag: str | None = None,
    limit: int = 20,
) -> list[Note]:
    fts_query = to_fts_query(query)
    if not fts_query:
        return list_notes(db, scope=scope, tag=tag, limit=limit)

    sql = """
    SELECT notes.*
    FROM notes_fts
    JOIN notes ON notes.note_id = notes_fts.note_id
    WHERE notes_fts MATCH ?
    """
    params: list[Any] = [fts_query]
    if scope is not None:
        sql += " AND notes.scope = ?"
        params.append(scope)
    if tag:
        sql += " AND notes.tags LIKE ?"
        params.append(f'%"{tag}"%')
    sql += " ORDER BY bm25(notes_fts) LIMIT ?"
    params.append(int(limit))

    cur = db.conn.execute(sql, params)
    return [_row_to_note(r) for r in cur.fetchall()]
