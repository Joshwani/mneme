from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from mneme.index.db import MnemeDB
from mneme.library.python import ingest_python_distribution


@pytest.fixture()
def db(tmp_path):
    instance = MnemeDB(tmp_path / "mneme.db")
    yield instance
    instance.close()


def _write_sample_package(root: Path) -> Path:
    pkg_dir = root / "samplepkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text(
        textwrap.dedent(
            '''
            """Sample package for testing griffe ingestion."""

            from .core import add, Greeter
            '''
        )
    )
    (pkg_dir / "core.py").write_text(
        textwrap.dedent(
            '''
            """Core utilities."""

            def add(a: int, b: int = 1) -> int:
                """Return the sum of two integers."""
                return a + b


            def _private(): ...


            class Greeter:
                """Greet a person."""

                def hello(self, name: str) -> str:
                    """Return a greeting for ``name``."""
                    return f"Hello, {name}!"

                def _private_method(self): ...
            '''
        )
    )
    return root


def test_ingest_python_distribution_collects_public_symbols(db, tmp_path):
    root = _write_sample_package(tmp_path)
    result = ingest_python_distribution(db, "samplepkg", source_dir=root)

    assert result["language"] == "python"
    assert result["name"] == "samplepkg"
    assert result["symbols"] >= 3

    packages = db.list_library_packages(language="python")
    assert len(packages) == 1
    assert packages[0]["name"] == "samplepkg"


def test_ingest_skips_private_names(db, tmp_path):
    root = _write_sample_package(tmp_path)
    ingest_python_distribution(db, "samplepkg", source_dir=root)

    rows = db.conn.execute(
        "SELECT qualified_name FROM library_symbols WHERE language = 'python'"
    ).fetchall()
    qnames = {r["qualified_name"] for r in rows}

    assert any(q.endswith("samplepkg.core.add") for q in qnames), qnames
    assert any(q.endswith("samplepkg.core.Greeter") for q in qnames), qnames
    assert any(q.endswith("samplepkg.core.Greeter.hello") for q in qnames), qnames

    assert not any("_private" in q for q in qnames), qnames
    assert not any("_private_method" in q for q in qnames), qnames


def test_signatures_and_docstrings_captured(db, tmp_path):
    root = _write_sample_package(tmp_path)
    ingest_python_distribution(db, "samplepkg", source_dir=root)

    add_row = db.conn.execute(
        "SELECT signature, summary FROM library_symbols WHERE symbol_name = 'add'"
    ).fetchone()
    assert add_row is not None
    assert "a: int" in add_row["signature"]
    assert "b: int = 1" in add_row["signature"]
    assert "int" in add_row["signature"].split("->")[-1]
    assert "Return the sum" in add_row["summary"]


def test_re_ingest_replaces_previous_symbols(db, tmp_path):
    root = _write_sample_package(tmp_path)
    ingest_python_distribution(db, "samplepkg", source_dir=root)
    first = db.conn.execute(
        "SELECT COUNT(*) FROM library_symbols WHERE language = 'python'"
    ).fetchone()[0]

    (root / "samplepkg" / "core.py").write_text(
        textwrap.dedent(
            '''
            """Trimmed core module."""

            def add(a: int, b: int = 1) -> int:
                """Return the sum of two integers."""
                return a + b
            '''
        )
    )
    ingest_python_distribution(db, "samplepkg", source_dir=root)
    second = db.conn.execute(
        "SELECT COUNT(*) FROM library_symbols WHERE language = 'python'"
    ).fetchone()[0]

    assert second < first
