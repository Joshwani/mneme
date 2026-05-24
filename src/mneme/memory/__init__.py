"""Mneme persistent memory: notebook + opt-in scoped file workspace."""

from .db import NotesDB, default_notes_db_path
from .notes import Note, add_note, delete_note, get_note, list_notes, search_notes, update_note
from .workspace import (
    WorkspaceError,
    WorkspaceFile,
    WorkspaceScope,
    default_workspace_root,
    disable_scope,
    enable_scope,
    list_scopes,
    list_workspace,
    read_workspace,
    remove_workspace,
    workspace_status,
    write_workspace,
)

__all__ = [
    "NotesDB",
    "default_notes_db_path",
    "Note",
    "add_note",
    "delete_note",
    "get_note",
    "list_notes",
    "search_notes",
    "update_note",
    "WorkspaceError",
    "WorkspaceFile",
    "WorkspaceScope",
    "default_workspace_root",
    "disable_scope",
    "enable_scope",
    "list_scopes",
    "list_workspace",
    "read_workspace",
    "remove_workspace",
    "workspace_status",
    "write_workspace",
]
