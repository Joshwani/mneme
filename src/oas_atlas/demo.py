"""One-command demo: ingest a bundled spec and run a sample search."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from oas_atlas.index.db import AtlasDB, default_db_path
from oas_atlas.index.ingest import ingest_text
from oas_atlas.index.search import SearchFilters, search_operations

DEMO_QUERY = "create a todo with a due date"

DEMO_SPEC_YAML = """openapi: 3.1.0
info:
  title: Example Todo API
  version: 1.0.0
servers:
  - url: https://api.example.test
paths:
  /todos:
    get:
      operationId: listTodos
      summary: List todos
      description: Returns todos, optionally filtered by completion state.
      tags: [todos]
      parameters:
        - name: completed
          in: query
          required: false
          description: Filter by completion state.
          schema:
            type: boolean
      responses:
        '200':
          description: A list of todos.
          content:
            application/json:
              schema:
                type: object
                properties:
                  todos:
                    type: array
                    items:
                      $ref: '#/components/schemas/Todo'
    post:
      operationId: createTodo
      summary: Create a todo
      description: Creates a new todo item with a title and optional due date.
      tags: [todos]
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [title]
              properties:
                title:
                  type: string
                  description: Todo title.
                due_date:
                  type: string
                  format: date
                  description: Optional due date.
      responses:
        '201':
          description: Created todo.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Todo'
  /todos/{todo_id}:
    get:
      operationId: getTodo
      summary: Get a todo
      tags: [todos]
      parameters:
        - name: todo_id
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Todo detail.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Todo'
components:
  schemas:
    Todo:
      type: object
      required: [id, title, completed]
      properties:
        id:
          type: string
        title:
          type: string
        completed:
          type: boolean
        due_date:
          type: string
          format: date
          nullable: true
"""


def run_demo(
    db_path: str | None = None,
    *,
    query: str = DEMO_QUERY,
    limit: int = 3,
) -> dict[str, Any]:
    """Index the bundled demo spec and run one search.

    Returns a dict with the ingest result, search result, and the resolved DB path,
    so callers (CLI, tests) can render output however they want.
    """

    resolved_db = db_path or default_db_path()
    db = AtlasDB(resolved_db)
    try:
        ingest_result = ingest_text(
            db,
            DEMO_SPEC_YAML,
            source_url="demo://oas-atlas/examples/todo.yaml",
            discovered_via="demo",
        )
        search_result = search_operations(
            db,
            query,
            limit=limit,
            filters=SearchFilters(),
        )
        stats = db.stats()
    finally:
        db.close()

    return {
        "db_path": resolved_db,
        "ingest": ingest_result,
        "query": query,
        "search": search_result,
        "stats": stats,
    }


def format_next_steps(db_path: str) -> str:
    """Human-friendly hints to print after a successful demo run."""

    # Resolve a stable absolute path so the hints are copy-pasteable.
    abs_db = str(Path(db_path).expanduser().resolve())
    mcp_available = shutil.which("oas-atlas") is not None
    cli = "oas-atlas" if mcp_available else "python -m oas_atlas.cli"
    return (
        "\nNext steps:\n"
        f"  1) Search again:        {cli} search 'list todos'\n"
        f"  2) Inspect stats:       {cli} stats\n"
        f"  3) Serve the API:       {cli} serve\n"
        f"  4) Print MCP config:    {cli} mcp-config --client cursor\n"
        f"\nIndex location:         {abs_db}\n"
        "Override with --db <path> or set OAS_ATLAS_DB.\n"
    )
