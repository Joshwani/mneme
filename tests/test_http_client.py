from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from mneme.auth import AuthConfigError
from mneme.http_client import (
    CallInputs,
    OperationCallError,
    execute_operation_call,
    prepare_operation_call,
)
from mneme.index.db import MnemeDB
from mneme.index.ingest import ingest_file
from mneme.index.search import SearchFilters, search_operations

ROOT = Path(__file__).resolve().parents[1]


def _query_api_key_operation():
    return {
        "operation_id": "geo-search",
        "api_title": "Geocoding API",
        "provider_domain": "api.geoapify.test",
        "method": "GET",
        "path": "/v1/geocode/search",
        "servers": [{"url": "https://api.geoapify.test"}],
        "parameters": [
            {"name": "apiKey", "in": "query", "required": True},
            {"name": "text", "in": "query", "required": True},
        ],
    }


def _write_query_api_key_auth(tmp_path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "profiles": {
                    "geoapify": {
                        "provider_domain": "api.geoapify.test",
                        "auth": {
                            "type": "api_key",
                            "in": "query",
                            "name": "apiKey",
                            "value_env": "GEOAPIFY_API_KEY",
                        },
                        "allowed_hosts": ["api.geoapify.test"],
                        "allow_methods": ["GET"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return auth_file


def _operation_for_query(tmp_path, query: str, method: str | None = None):
    db = MnemeDB(tmp_path / "mneme.db")
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


def test_profile_injected_query_key_satisfies_required_input(tmp_path, monkeypatch):
    monkeypatch.setenv("GEOAPIFY_API_KEY", "secret-value")
    auth_file = _write_query_api_key_auth(tmp_path)

    result = prepare_operation_call(
        _query_api_key_operation(),
        auth_config=str(auth_file),
        inputs=CallInputs(
            auth_profile="geoapify",
            query_params={"text": "Denver, Colorado"},
        ),
    )

    assert result["can_execute"] is True
    assert result["missing_required"] == []
    assert result["query_params"]["apiKey"] == "<redacted>"
    assert parse_qs(urlparse(result["url"]).query)["apiKey"] == ["<redacted>"]


def test_execute_uses_injected_query_key_but_returns_redacted_request(tmp_path, monkeypatch):
    monkeypatch.setenv("GEOAPIFY_API_KEY", "secret-value")
    auth_file = _write_query_api_key_auth(tmp_path)
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def request(self, **kwargs):
            captured.update(kwargs)
            request = httpx.Request(kwargs["method"], kwargs["url"])
            return httpx.Response(
                200,
                json={"results": [{"city": "Denver"}]},
                headers={"content-type": "application/json"},
                request=request,
            )

    monkeypatch.setattr("mneme.http_client.httpx.Client", FakeClient)

    result = execute_operation_call(
        _query_api_key_operation(),
        auth_config=str(auth_file),
        inputs=CallInputs(
            auth_profile="geoapify",
            query_params={"text": "Denver, Colorado"},
        ),
        dry_run=False,
        confirm=True,
    )

    assert parse_qs(urlparse(captured["url"]).query)["apiKey"] == ["secret-value"]
    assert result["request"]["query_params"]["apiKey"] == "<redacted>"
    assert parse_qs(urlparse(result["request"]["url"]).query)["apiKey"] == ["<redacted>"]
    assert result["response"]["status_code"] == 200


def test_profile_with_missing_query_key_env_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("GEOAPIFY_API_KEY", raising=False)
    auth_file = _write_query_api_key_auth(tmp_path)

    with pytest.raises(AuthConfigError, match="missing an API key/env"):
        prepare_operation_call(
            _query_api_key_operation(),
            auth_config=str(auth_file),
            inputs=CallInputs(
                auth_profile="geoapify",
                query_params={"text": "Denver, Colorado"},
            ),
        )


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
