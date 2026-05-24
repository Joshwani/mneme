from __future__ import annotations

import json
from pathlib import Path

import pytest

from oas_atlas.http_client import (
    CallInputs,
    OperationCallError,
    execute_operation_call,
    prepare_operation_call,
)
from oas_atlas.index.db import AtlasDB
from oas_atlas.index.ingest import ingest_file
from oas_atlas.index.search import SearchFilters, search_operations

ROOT = Path(__file__).resolve().parents[1]


def _operation_for_query(tmp_path, query: str, method: str | None = None):
    db = AtlasDB(tmp_path / "atlas.db")
    try:
        ingest_file(db, ROOT / "examples" / "specs" / "todo.yaml")
        search = search_operations(
            db,
            query,
            limit=1,
            filters=SearchFilters(method=method) if method else None,
        )
        assert search["results"]
        return db.get_operation(search["results"][0]["operation_id"])
    finally:
        db.close()


def test_prepare_call_fills_path_params(tmp_path):
    op = _operation_for_query(tmp_path, "get one todo", method="GET")
    result = prepare_operation_call(op, inputs=CallInputs(path_params={"todo_id": "abc 123"}))

    assert result["method"] == "GET"
    assert result["url"] == "https://api.example.test/todos/abc%20123"
    assert result["can_execute"] is True


def test_prepare_call_uses_local_auth_profile_and_redacts(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_API_KEY", "secret-value")
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "profiles": {
                    "todo": {
                        "provider_domain": "api.example.test",
                        "auth": {
                            "type": "api_key",
                            "in": "header",
                            "name": "X-API-Key",
                            "value_env": "TODO_API_KEY",
                        },
                        "allow_methods": ["GET", "POST"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    op = _operation_for_query(tmp_path, "create a todo", method="POST")
    result = prepare_operation_call(
        op,
        auth_config=str(auth_file),
        inputs=CallInputs(auth_profile="todo", json_body={"title": "ship mcp"}),
    )

    assert result["headers"]["X-API-Key"] == "<redacted>"
    assert result["body"] == {"title": "ship mcp"}
    assert result["auth_profile"]["name"] == "todo"


def test_execute_call_is_dry_run_by_default(tmp_path):
    op = _operation_for_query(tmp_path, "get one todo", method="GET")
    result = execute_operation_call(op, inputs=CallInputs(path_params={"todo_id": "1"}))

    assert result["dry_run"] is True
    assert result["prepared_call"]["url"] == "https://api.example.test/todos/1"


def test_mutating_unauthenticated_real_call_is_blocked(tmp_path):
    op = _operation_for_query(tmp_path, "create a todo", method="POST")
    with pytest.raises(OperationCallError):
        execute_operation_call(
            op,
            inputs=CallInputs(json_body={"title": "ship mcp"}),
            dry_run=False,
            confirm=True,
        )
