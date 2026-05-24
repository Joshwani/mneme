from __future__ import annotations

from pathlib import Path

from oas_atlas.index.db import AtlasDB
from oas_atlas.index.ingest import ingest_file
from oas_atlas.index.search import SearchFilters, search_operations

ROOT = Path(__file__).resolve().parents[1]


def test_ingest_and_search_create_todo(tmp_path):
    db = AtlasDB(tmp_path / "atlas.db")
    try:
        result = ingest_file(db, ROOT / "examples" / "specs" / "todo.yaml")
        assert result["operations"] == 3

        search = search_operations(db, "create a new todo with a due date", limit=3)
        assert search["results"]
        top = search["results"][0]
        assert top["method"] == "POST"
        assert top["path"] == "/todos"
        assert "title" in top["required_inputs"]
    finally:
        db.close()


def test_method_filter(tmp_path):
    db = AtlasDB(tmp_path / "atlas.db")
    try:
        ingest_file(db, ROOT / "examples" / "specs" / "todo.yaml")
        search = search_operations(
            db,
            "find todo",
            limit=5,
            filters=SearchFilters(method="GET"),
        )
        assert search["results"]
        assert all(result["method"] == "GET" for result in search["results"])
    finally:
        db.close()
