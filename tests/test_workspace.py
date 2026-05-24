from __future__ import annotations

import os
from pathlib import Path

import pytest

from mneme.memory.db import NotesDB
from mneme.memory.workspace import (
    WorkspaceError,
    disable_scope,
    enable_scope,
    list_workspace,
    read_workspace,
    remove_workspace,
    workspace_status,
    write_workspace,
)


@pytest.fixture()
def workspace_db(tmp_path):
    db = NotesDB(tmp_path / "notes.db")
    yield db, tmp_path
    db.close()


def test_workspace_status_starts_empty(workspace_db):
    db, _ = workspace_db
    status = workspace_status(db)
    assert status["enabled"] is False
    assert status["scopes"] == []


def test_enable_creates_scope_and_directory(workspace_db, tmp_path):
    db, _ = workspace_db
    root = tmp_path / "ws"
    info = enable_scope(db, "proj-a", base_root=str(root), max_bytes=1024 * 1024)

    assert info.scope == "proj-a"
    assert Path(info.base_path).is_dir()
    assert info.used_bytes == 0
    assert info.file_count == 0


def test_enable_rejects_bad_scope_names(workspace_db, tmp_path):
    db, _ = workspace_db
    for bad in ["", "..", "a b", "x/y", "a$", " "]:
        with pytest.raises(WorkspaceError):
            enable_scope(db, bad, base_root=str(tmp_path / "ws"))


def test_write_and_read_roundtrip(workspace_db, tmp_path):
    db, _ = workspace_db
    enable_scope(db, "p", base_root=str(tmp_path / "ws"))

    written = write_workspace(db, "p", "notes/a.md", "hello world")
    assert written.size == len("hello world")
    assert written.rel_path == "notes/a.md"

    read = read_workspace(db, "p", "notes/a.md")
    assert read["content"] == "hello world"
    assert read["binary"] is False
    assert read["size"] == len("hello world")


def test_path_traversal_blocked(workspace_db, tmp_path):
    db, _ = workspace_db
    enable_scope(db, "p", base_root=str(tmp_path / "ws"))

    for bad in ["../escape.txt", "a/../../escape.txt", "a/../b/../../c"]:
        with pytest.raises(WorkspaceError):
            write_workspace(db, "p", bad, "x")


def test_absolute_path_treated_as_relative(workspace_db, tmp_path):
    db, _ = workspace_db
    info = enable_scope(db, "p", base_root=str(tmp_path / "ws"))

    written = write_workspace(db, "p", "/leading/slash.txt", "x")
    assert written.rel_path == "leading/slash.txt"

    base = Path(info.base_path)
    assert (base / "leading" / "slash.txt").read_text() == "x"


def test_symlink_traversal_blocked(workspace_db, tmp_path):
    db, _ = workspace_db
    info = enable_scope(db, "p", base_root=str(tmp_path / "ws"))

    outside = tmp_path / "outside.txt"
    outside.write_text("private")

    base = Path(info.base_path)
    base.mkdir(parents=True, exist_ok=True)
    link = base / "bad_link"
    os.symlink(outside, link)

    with pytest.raises(WorkspaceError):
        read_workspace(db, "p", "bad_link")


def test_quota_enforced(workspace_db, tmp_path):
    db, _ = workspace_db
    enable_scope(db, "tiny", base_root=str(tmp_path / "ws"), max_bytes=10)

    write_workspace(db, "tiny", "a.txt", "1234567890")
    with pytest.raises(WorkspaceError):
        write_workspace(db, "tiny", "b.txt", "1")


def test_file_size_limit_enforced(workspace_db, tmp_path):
    db, _ = workspace_db
    enable_scope(db, "p", base_root=str(tmp_path / "ws"))

    with pytest.raises(WorkspaceError):
        write_workspace(db, "p", "big.bin", "x" * 1024, max_file_bytes=8)


def test_overwrite_uses_delta_against_quota(workspace_db, tmp_path):
    db, _ = workspace_db
    enable_scope(db, "p", base_root=str(tmp_path / "ws"), max_bytes=10)

    write_workspace(db, "p", "a.txt", "1234567890")
    write_workspace(db, "p", "a.txt", "abcdefghij")
    info = list_workspace(db, "p")
    assert len(info) == 1
    assert info[0].size == 10


def test_list_workspace_filters_by_prefix(workspace_db, tmp_path):
    db, _ = workspace_db
    enable_scope(db, "p", base_root=str(tmp_path / "ws"))
    write_workspace(db, "p", "notes/a.md", "1")
    write_workspace(db, "p", "notes/b.md", "1")
    write_workspace(db, "p", "logs/c.txt", "1")

    notes_only = list_workspace(db, "p", prefix="notes/")
    assert sorted(f.rel_path for f in notes_only) == ["notes/a.md", "notes/b.md"]


def test_remove_workspace(workspace_db, tmp_path):
    db, _ = workspace_db
    info = enable_scope(db, "p", base_root=str(tmp_path / "ws"))
    write_workspace(db, "p", "a.txt", "hi")

    assert remove_workspace(db, "p", "a.txt") is True
    assert remove_workspace(db, "p", "a.txt") is False
    assert not (Path(info.base_path) / "a.txt").exists()


def test_operations_require_scope_to_be_enabled(workspace_db):
    db, _ = workspace_db
    with pytest.raises(WorkspaceError):
        write_workspace(db, "missing", "a.txt", "x")
    with pytest.raises(WorkspaceError):
        read_workspace(db, "missing", "a.txt")
    with pytest.raises(WorkspaceError):
        list_workspace(db, "missing")


def test_disable_removes_scope_metadata(workspace_db, tmp_path):
    db, _ = workspace_db
    info = enable_scope(db, "p", base_root=str(tmp_path / "ws"))
    write_workspace(db, "p", "a.txt", "x")

    assert disable_scope(db, "p") is True
    status = workspace_status(db)
    assert status["scopes"] == []
    assert Path(info.base_path).exists(), "disable should keep files unless remove_files=True"


def test_disable_with_remove_files_wipes_directory(workspace_db, tmp_path):
    db, _ = workspace_db
    info = enable_scope(db, "p", base_root=str(tmp_path / "ws"))
    write_workspace(db, "p", "a.txt", "x")

    assert disable_scope(db, "p", remove_files=True) is True
    assert not Path(info.base_path).exists()
