from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from mneme.index.db import MnemeDB
from mneme.index.ingest import ingest_text
from mneme.index.search import (
    ALL_KINDS,
    KIND_HTTP_OPERATION,
    KIND_JSLIB_SYMBOL,
    KIND_PYLIB_SYMBOL,
    CallableFilters,
    search_callables,
    search_operations,
)
from mneme.library.python import ingest_python_distribution
from mneme.library.typescript import ingest_dts_text


SAMPLE_OAS = textwrap.dedent(
    """
    openapi: 3.0.0
    info:
      title: Greeter API
      version: 1.0.0
    paths:
      /greetings:
        post:
          summary: Create a greeting
          operationId: createGreeting
          requestBody:
            required: true
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    name:
                      type: string
                    enthusiastic:
                      type: boolean
                  required: [name]
          responses:
            '200':
              description: OK
    """
)


SAMPLE_DTS = textwrap.dedent(
    """
    /** Greet a person by name. */
    export function greet(name: string): string;
    """
)


def _write_pkg(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    pkg = root / "greeterpy"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        '"""Greeter library."""\n\n'
        "def greet(name: str) -> str:\n"
        '    """Greet a person by name."""\n'
        '    return f"Hello, {name}!"\n'
    )
    return root


@pytest.fixture()
def populated_db(tmp_path):
    db = MnemeDB(tmp_path / "mneme.db")
    ingest_text(db, SAMPLE_OAS, source_url="local://greeter.yaml")
    ingest_dts_text(db, package_name="greeter", source=SAMPLE_DTS, version="0.1.0")
    _write_pkg(tmp_path / "py")
    ingest_python_distribution(db, "greeterpy", source_dir=str(tmp_path / "py"))
    yield db
    db.close()


def test_unified_search_returns_all_kinds(populated_db):
    result = search_callables(populated_db, "greet", limit=10)
    kinds = {hit.get("kind") for hit in result["results"]}
    assert KIND_HTTP_OPERATION in kinds
    assert KIND_PYLIB_SYMBOL in kinds
    assert KIND_JSLIB_SYMBOL in kinds


def test_unified_search_kind_filter(populated_db):
    only_lib = search_callables(
        populated_db, "greet", filters=CallableFilters(kinds=(KIND_PYLIB_SYMBOL,))
    )
    assert only_lib["results"]
    for hit in only_lib["results"]:
        assert hit["kind"] == KIND_PYLIB_SYMBOL


def test_unified_search_language_filter(populated_db):
    only_ts = search_callables(
        populated_db, "greet", filters=CallableFilters(language="typescript")
    )
    assert only_ts["results"]
    for hit in only_ts["results"]:
        assert hit["kind"] == KIND_JSLIB_SYMBOL
        assert hit["language"] == "typescript"


def test_search_operations_returns_kind_annotation(populated_db):
    result = search_operations(populated_db, "greet")
    assert result["results"]
    assert all(hit["kind"] == KIND_HTTP_OPERATION for hit in result["results"])
    assert all("callable_id" in hit for hit in result["results"])


def test_all_kinds_constant_matches_filter_choices(populated_db):
    for kind in ALL_KINDS:
        result = search_callables(populated_db, "greet", filters=CallableFilters(kinds=(kind,)))
        for hit in result["results"]:
            assert hit["kind"] == kind
