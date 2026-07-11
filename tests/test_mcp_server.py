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

    prepared = asyncio.run(server.call_tool("prepare_http_call", arguments))
    if isinstance(prepared, tuple):
        prepared = prepared[1]
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
    if isinstance(executed, tuple):
        executed = executed[1]
    assert parse_qs(urlparse(captured["url"]).query)["apiKey"] == ["secret-value"]
    assert executed["request"]["query_params"]["apiKey"] == "<redacted>"
    assert executed["response"]["status_code"] == 200
