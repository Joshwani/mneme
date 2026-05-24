"""Shared pytest fixtures.

The most important job here is keeping tests hermetic: tests must not inherit
the developer's personal auth config or DB path, which would make results
depend on the host machine.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_mneme_env(tmp_path, monkeypatch):
    """Point env-driven config at the test's tmp_path so user config can't leak in."""

    monkeypatch.setenv("MNEME_AUTH_CONFIG", str(tmp_path / "auth.absent.json"))
    monkeypatch.setenv("MNEME_DB", str(tmp_path / "mneme.db"))
    monkeypatch.setenv("MNEME_NOTES_DB", str(tmp_path / "notes.db"))
    monkeypatch.setenv("MNEME_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.delenv("MNEME_HTTP_ALLOW_HOSTS", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    os.makedirs(tmp_path, exist_ok=True)
    yield
