from __future__ import annotations

import textwrap

import pytest

from mneme.index.db import MnemeDB
from mneme.library.typescript import ingest_dts_file, ingest_dts_text


@pytest.fixture()
def db(tmp_path):
    instance = MnemeDB(tmp_path / "mneme.db")
    yield instance
    instance.close()


SAMPLE_DTS = textwrap.dedent(
    """
    /** Add two numbers. */
    export function add(a: number, b: number): number;

    /** Options object for the Greeter. */
    export interface GreeterOptions {
      name: string;
      enthusiastic?: boolean;
    }

    export declare class Greeter {
      /** Construct a new greeter. */
      constructor(options: GreeterOptions);

      /** Return a greeting for ``name``. */
      greet(name: string): Promise<string>;
    }

    export type Maybe<T> = T | null | undefined;

    export enum Status {
      Pending,
      Ready,
    }
    """
)


def test_ingest_dts_text_captures_top_level_exports(db):
    result = ingest_dts_text(db, package_name="greeter", source=SAMPLE_DTS, version="0.1.0")

    assert result["language"] == "typescript"
    assert result["name"] == "greeter"
    assert result["symbols"] >= 6

    rows = db.conn.execute(
        "SELECT qualified_name, kind, signature FROM library_symbols ORDER BY qualified_name"
    ).fetchall()
    qnames = {r["qualified_name"]: r for r in rows}

    assert "greeter.add" in qnames
    assert qnames["greeter.add"]["kind"] == "function"
    assert "number" in qnames["greeter.add"]["signature"]

    assert "greeter.Greeter" in qnames
    assert qnames["greeter.Greeter"]["kind"] == "class"

    assert "greeter.Greeter.greet" in qnames
    assert qnames["greeter.Greeter.greet"]["kind"] == "method"
    assert "Promise<string>" in qnames["greeter.Greeter.greet"]["signature"]

    assert "greeter.GreeterOptions" in qnames
    assert qnames["greeter.GreeterOptions"]["kind"] == "interface"

    assert "greeter.Maybe" in qnames
    assert qnames["greeter.Maybe"]["kind"] == "type"

    assert "greeter.Status" in qnames
    assert qnames["greeter.Status"]["kind"] == "enum"


def test_jsdoc_summary_captured(db):
    ingest_dts_text(db, package_name="greeter", source=SAMPLE_DTS)
    add = db.conn.execute(
        "SELECT summary FROM library_symbols WHERE qualified_name = 'greeter.add'"
    ).fetchone()
    assert add is not None
    assert "Add two numbers" in (add["summary"] or "")


def test_ingest_dts_file_reads_disk(db, tmp_path):
    dts_path = tmp_path / "greeter.d.ts"
    dts_path.write_text(SAMPLE_DTS)

    result = ingest_dts_file(db, package_name="greeter", path=dts_path)
    assert result["symbols"] >= 6


def test_re_ingest_replaces_previous_symbols(db):
    ingest_dts_text(db, package_name="greeter", source=SAMPLE_DTS)
    first = db.conn.execute(
        "SELECT COUNT(*) FROM library_symbols WHERE language = 'typescript'"
    ).fetchone()[0]

    trimmed = textwrap.dedent(
        """
        /** Add two numbers. */
        export function add(a: number, b: number): number;
        """
    )
    ingest_dts_text(db, package_name="greeter", source=trimmed)
    second = db.conn.execute(
        "SELECT COUNT(*) FROM library_symbols WHERE language = 'typescript'"
    ).fetchone()[0]

    assert second == 1
    assert first > second
