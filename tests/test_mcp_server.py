from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlparse

import httpx

from mneme.index.db import MnemeDB
from mneme.index.ingest import ingest_text
from mneme.index.search import search_operations
from mneme.mcp_server import create_mcp_server


GEOAPIFY_SPEC = """
openapi: 3.0.3
info:
  title: Geocoding API
  version: 1.0.0
servers:
  - url: https://api.geoapify.test/v1
paths:
  /geocode/search:
    get:
      operationId: geocodeSearch
      summary: Search for an address
      parameters:
        - name: apiKey
          in: query
          required: true
          schema:
            type: string
        - name: text
          in: query
          required: true
          schema:
            type: string
      responses:
        "200":
          description: Successful response
"""


def _unwrap_tool_result(result):
    if isinstance(result, tuple):
        return result[1]
    return result


def test_mcp_discovery_metadata_exposes_indexed_catalog(tmp_path):
    db_path = tmp_path / "mneme.db"
    db = MnemeDB(db_path)
    try:
        ingest_text(
            db,
            GEOAPIFY_SPEC,
            source_url="https://example.test/geoapify.yaml",
        )
        db.upsert_library_package(
            package={
                "package_id": "pylib:httpx",
                "language": "python",
                "name": "httpx",
                "version": "1.0",
                "source": "installed",
                "fetched_at": "2026-07-11T00:00:00Z",
            }
        )
    finally:
        db.close()

    server = create_mcp_server(db_path=str(db_path))

    assert "searchable catalog of capabilities selected by the user" in server.instructions
    assert "1 HTTP operations" in server.instructions
    assert "api.geoapify.test" in server.instructions
    assert "search_callables first" in server.instructions

    tools = asyncio.run(server.list_tools())
    tools_by_name = {tool.name: tool for tool in tools}
    assert "catalog_summary" in tools_by_name
    assert "Primary discovery tool" in tools_by_name["search_callables"].description
    assert (
        tools_by_name["search_operations"]
        .inputSchema["properties"]["query"]["description"]
        .lower()
        .startswith("natural-language")
    )
    assert (
        "Do not invent"
        in tools_by_name["get_operation"].inputSchema["properties"]["operation_id"]["description"]
    )

    summary = _unwrap_tool_result(asyncio.run(server.call_tool("catalog_summary", {})))
    assert summary["operations"] == 1
    assert summary["libraries"] == 1
    assert summary["indexed_providers"] == [
        {
            "provider_domain": "api.geoapify.test",
            "operation_count": 1,
            "api_titles": ["Geocoding API"],
        }
    ]
    assert summary["indexed_libraries"] == [
        {"language": "python", "name": "httpx", "version": "1.0"}
    ]


def test_mcp_http_tools_inject_required_query_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GEOAPIFY_API_KEY", "secret-value")
    db_path = tmp_path / "mneme.db"
    db = MnemeDB(db_path)
    try:
        ingest_text(
            db,
            GEOAPIFY_SPEC,
            source_url="https://example.test/geoapify.yaml",
        )
        result = search_operations(db, "search for an address", limit=1)
        operation_id = result["results"][0]["operation_id"]
    finally:
        db.close()

    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "profiles": {
                    "geoapify": {
                        "provider_domain": "api.geoapify.test",
                        "base_url": "https://api.geoapify.test/v1",
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
    server = create_mcp_server(db_path=str(db_path), auth_config=str(auth_file))
    arguments = {
        "operation_id": operation_id,
        "auth_profile": "geoapify",
        "query_params": {"text": "Denver, Colorado"},
    }

    prepared = _unwrap_tool_result(asyncio.run(server.call_tool("prepare_http_call", arguments)))
    assert prepared["can_execute"] is True
    assert prepared["missing_required"] == []
    assert prepared["query_params"]["apiKey"] == "<redacted>"

    executed = asyncio.run(
        server.call_tool(
            "execute_http_call",
            {
                **arguments,
                "dry_run": False,
                "confirm": True,
            },
        )
    )
    executed = _unwrap_tool_result(executed)
    assert parse_qs(urlparse(captured["url"]).query)["apiKey"] == ["secret-value"]
    assert executed["request"]["query_params"]["apiKey"] == "<redacted>"
    assert executed["response"]["status_code"] == 200
