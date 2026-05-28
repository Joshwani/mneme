from __future__ import annotations

from pathlib import Path

import pytest

from mneme.fetch import FetchResult
from mneme.index.db import MnemeDB
from mneme.index.ingest import IngestError, ingest_url

ROOT = Path(__file__).resolve().parents[1]
TODO_SPEC = ROOT / "examples" / "specs" / "todo.yaml"


class _MockFetcher:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_text(self, url: str, **kwargs) -> FetchResult:
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            headers={},
            text=self.text,
            content_type="application/yaml",
        )


def test_ingest_url_with_mcp_provenance(tmp_path):
    db = MnemeDB(tmp_path / "mneme.db")
    try:
        result = ingest_url(
            db,
            "https://api.example.test/openapi.yaml",
            fetcher=_MockFetcher(TODO_SPEC.read_text(encoding="utf-8")),
            discovered_via="mcp",
        )
        assert result["operations"] == 3
        assert result["title"] == "Example Todo API"

        spec = db.get_spec(result["spec_id"])
        assert spec is not None
        assert spec["discovered_via"] == "mcp"
    finally:
        db.close()


def test_ingest_url_invalid_spec_raises_ingest_error(tmp_path):
    db = MnemeDB(tmp_path / "mneme.db")
    try:
        with pytest.raises(IngestError):
            ingest_url(
                db,
                "https://api.example.test/openapi.yaml",
                fetcher=_MockFetcher("not an openapi document"),
                discovered_via="mcp",
            )
    finally:
        db.close()


def test_create_mcp_server_smoke(tmp_path):
    pytest.importorskip("mcp")
    from mneme.mcp_server import create_mcp_server

    mcp = create_mcp_server(db_path=str(tmp_path / "mneme.db"))
    tools = {tool.name for tool in mcp._tool_manager.list_tools()}
    assert "index_openapi_url" in tools
