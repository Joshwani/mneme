from __future__ import annotations

import os
from typing import Any

from mneme.auth import list_auth_profiles, load_auth_profiles
from mneme.http_client import CallInputs, execute_operation_call, prepare_operation_call
from mneme.index.db import MnemeDB, default_db_path
from mneme.index.ingest import IngestError, ingest_url
from mneme.index.search import (
    ALL_KINDS,
    CallableFilters,
    SearchFilters,
    search_callables as search_index_callables,
    search_operations as search_index_operations,
)
from mneme.memory.db import NotesDB, default_notes_db_path
from mneme.memory.notes import (
    add_note as _add_note,
    delete_note as _delete_note,
    get_note as _get_note,
    list_notes as _list_notes,
    search_notes as _search_notes,
    update_note as _update_note,
)
from mneme.memory.workspace import (
    list_workspace as _list_workspace,
    read_workspace as _read_workspace,
    remove_workspace as _remove_workspace,
    workspace_status as _workspace_status,
    write_workspace as _write_workspace,
)


def create_mcp_server(
    db_path: str | None = None,
    auth_config: str | None = None,
    *,
    notes_db_path: str | None = None,
) -> Any:
    """Create a local MCP server exposing Mneme search, HTTP execution, and memory tools."""

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without optional extra.
        raise RuntimeError(
            "The MCP server requires the optional MCP dependency. Install with: "
            "python -m pip install 'mneme-server[mcp]'"
        ) from exc

    db_path = db_path or default_db_path()
    auth_config = auth_config or os.environ.get("MNEME_AUTH_CONFIG")
    notes_db_path = notes_db_path or default_notes_db_path()
    mcp = FastMCP("Mneme", json_response=True)

    def get_operation_or_error(operation_id: str) -> dict[str, Any]:
        db = MnemeDB(db_path)
        try:
            op = db.get_operation(operation_id)
            if op is None:
                raise ValueError(f"operation not found: {operation_id}")
            return op
        finally:
            db.close()

    @mcp.tool()
    def search_operations(
        query: str,
        limit: int = 10,
        provider_domain: str | None = None,
        method: str | None = None,
        auth_required: bool | None = None,
        token_budget: int | None = 4000,
    ) -> dict[str, Any]:
        """Search indexed OpenAPI operations for a natural-language task.

        Use this first when you need to find an API operation. Results include
        operation_id values that can be passed to get_operation, get_spec_slice,
        prepare_http_call, or execute_http_call.
        """

        db = MnemeDB(db_path)
        try:
            return search_index_operations(
                db,
                query,
                limit=max(1, min(limit, 50)),
                filters=SearchFilters(
                    provider_domain=provider_domain,
                    method=method,
                    auth_required=auth_required,
                ),
                token_budget=token_budget,
            )
        finally:
            db.close()

    @mcp.tool()
    def get_operation(operation_id: str) -> dict[str, Any]:
        """Return the normalized operation card for an operation_id."""

        return get_operation_or_error(operation_id)

    @mcp.tool()
    def get_spec_slice(operation_id: str) -> dict[str, Any]:
        """Return the minimal OpenAPI-style slice needed to understand one operation."""

        return get_operation_or_error(operation_id).get("spec_slice") or {}

    @mcp.tool()
    def get_call_template(operation_id: str) -> dict[str, Any]:
        """Return a non-executing call template for one operation."""

        from mneme.call_template import build_call_template

        return build_call_template(get_operation_or_error(operation_id))

    @mcp.tool()
    def list_local_auth_profiles() -> dict[str, Any]:
        """List local auth profiles without exposing secret values.

        Profiles are read from MNEME_AUTH_CONFIG or ~/.config/mneme/auth.json.
        Pass a profile name to prepare_http_call or execute_http_call when an API
        requires credentials.
        """

        return list_auth_profiles(auth_config)

    @mcp.tool()
    def prepare_http_call(
        operation_id: str,
        auth_profile: str | None = None,
        path_params: dict[str, Any] | None = None,
        query_params: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        json_body: Any = None,
        form_body: dict[str, Any] | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        """Prepare a redacted HTTP request for an indexed operation without sending it.

        Use this to check the resolved URL, missing required inputs, selected auth
        profile, and request body before making a real call.
        """

        op = get_operation_or_error(operation_id)
        profiles = load_auth_profiles(auth_config)
        return prepare_operation_call(
            op,
            auth_profiles=profiles,
            inputs=CallInputs(
                path_params=path_params,
                query_params=query_params,
                headers=headers,
                json_body=json_body,
                form_body=form_body,
                base_url=base_url,
                auth_profile=auth_profile,
            ),
        )

    @mcp.tool()
    def execute_http_call(
        operation_id: str,
        auth_profile: str | None = None,
        path_params: dict[str, Any] | None = None,
        query_params: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        json_body: Any = None,
        form_body: dict[str, Any] | None = None,
        base_url: str | None = None,
        confirm: bool = False,
        dry_run: bool = True,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Execute an indexed operation using local credentials.

        Defaults to dry_run=true, which returns a redacted prepared request and sends
        no network traffic. To perform a real request, set dry_run=false and
        confirm=true. Secrets are injected locally from the selected auth profile and
        never returned in tool output.
        """

        op = get_operation_or_error(operation_id)
        profiles = load_auth_profiles(auth_config)
        return execute_operation_call(
            op,
            auth_profiles=profiles,
            inputs=CallInputs(
                path_params=path_params,
                query_params=query_params,
                headers=headers,
                json_body=json_body,
                form_body=form_body,
                base_url=base_url,
                auth_profile=auth_profile,
            ),
            timeout=max(1.0, min(float(timeout), 120.0)),
            confirm=confirm,
            dry_run=dry_run,
        )

    @mcp.tool()
    def search_callables(
        query: str,
        limit: int = 10,
        kinds: list[str] | None = None,
        provider_domain: str | None = None,
        method: str | None = None,
        auth_required: bool | None = None,
        language: str | None = None,
        package_name: str | None = None,
        token_budget: int | None = 4000,
    ) -> dict[str, Any]:
        """Unified search across HTTP operations and library symbols.

        Returns a mixed list ranked by BM25. Each hit has a ``kind`` field of
        'http_operation', 'pylib_symbol', or 'jslib_symbol'. Use ``kinds`` to
        restrict to a subset. For HTTP-only behavior, use search_operations.
        """

        kind_tuple: tuple[str, ...] | None = None
        if kinds:
            invalid = [k for k in kinds if k not in ALL_KINDS]
            if invalid:
                raise ValueError(f"unknown kinds: {invalid}. Allowed: {', '.join(ALL_KINDS)}")
            kind_tuple = tuple(kinds)
        db = MnemeDB(db_path)
        try:
            return search_index_callables(
                db,
                query,
                limit=max(1, min(int(limit), 50)),
                filters=CallableFilters(
                    kinds=kind_tuple,
                    provider_domain=provider_domain,
                    method=method,
                    auth_required=auth_required,
                    language=language,
                    package_name=package_name,
                ),
                token_budget=token_budget,
            )
        finally:
            db.close()

    @mcp.tool()
    def get_library_symbol(symbol_id: str) -> dict[str, Any]:
        """Return the normalized library symbol card for a symbol_id."""

        db = MnemeDB(db_path)
        try:
            sym = db.get_library_symbol(symbol_id)
        finally:
            db.close()
        if sym is None:
            raise ValueError(f"symbol not found: {symbol_id}")
        return sym

    @mcp.tool()
    def list_libraries(language: str | None = None) -> dict[str, Any]:
        """List indexed library packages, optionally filtered by language."""

        db = MnemeDB(db_path)
        try:
            return {"packages": db.list_library_packages(language=language)}
        finally:
            db.close()

    @mcp.tool()
    def mneme_stats() -> dict[str, Any]:
        """Return index statistics for this local Mneme database."""

        db = MnemeDB(db_path)
        try:
            stats = db.stats()
            stats["auth_config_present"] = bool(load_auth_profiles(auth_config))
            return stats
        finally:
            db.close()

    @mcp.tool()
    def index_openapi_url(url: str) -> dict[str, Any]:
        """Fetch and index one remote OpenAPI/Swagger spec URL into the local Mneme database.

        Use when the user asks to add, index, or ingest an API spec. After indexing,
        use search_operations to find operations. Returns spec_id, operation count, and title.
        """

        db = MnemeDB(db_path)
        try:
            return ingest_url(db, url, discovered_via="mcp")
        except IngestError as exc:
            raise ValueError(str(exc)) from exc
        finally:
            db.close()

    @mcp.tool()
    def notes_search(
        query: str,
        scope: str | None = None,
        tag: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Full-text search the persistent agent notebook.

        Notes are written by the agent itself (or the user) using notes_add.
        Use this when you need to recall something previously saved across sessions.
        """

        db = NotesDB(notes_db_path)
        try:
            notes = _search_notes(
                db, query, scope=scope, tag=tag, limit=max(1, min(int(limit), 50))
            )
        finally:
            db.close()
        return {"query": query, "results": [n.to_dict() for n in notes]}

    @mcp.tool()
    def notes_get(note_id: str) -> dict[str, Any]:
        """Return a single note by ID."""

        db = NotesDB(notes_db_path)
        try:
            note = _get_note(db, note_id)
        finally:
            db.close()
        if note is None:
            raise ValueError(f"note not found: {note_id}")
        return note.to_dict()

    @mcp.tool()
    def notes_list(
        scope: str | None = None,
        tag: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List the most recently updated notes, optionally filtered by scope or tag."""

        db = NotesDB(notes_db_path)
        try:
            notes = _list_notes(db, scope=scope, tag=tag, limit=max(1, min(int(limit), 100)))
        finally:
            db.close()
        return {"results": [n.to_dict() for n in notes]}

    @mcp.tool()
    def notes_add(
        title: str,
        body: str = "",
        tags: list[str] | None = None,
        scope: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Add a new note to the persistent notebook.

        Use this whenever the user tells you something worth remembering across
        sessions, when you discover something non-obvious about a tool/API,
        or when the user explicitly asks you to remember something.
        """

        db = NotesDB(notes_db_path)
        try:
            note = _add_note(
                db, title=title, body=body, tags=tags or [], scope=scope, source=source
            )
        finally:
            db.close()
        return note.to_dict()

    @mcp.tool()
    def notes_update(
        note_id: str,
        title: str | None = None,
        body: str | None = None,
        tags: list[str] | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing note. Omitted fields are left unchanged."""

        db = NotesDB(notes_db_path)
        try:
            note = _update_note(db, note_id, title=title, body=body, tags=tags, scope=scope)
        finally:
            db.close()
        return note.to_dict()

    @mcp.tool()
    def notes_delete(note_id: str) -> dict[str, Any]:
        """Delete a note by ID."""

        db = NotesDB(notes_db_path)
        try:
            deleted = _delete_note(db, note_id)
        finally:
            db.close()
        return {"deleted": deleted, "note_id": note_id}

    @mcp.tool()
    def workspace_status() -> dict[str, Any]:
        """Show which scoped file workspaces are enabled and how much they're using.

        The workspace is OFF by default. The user must enable scopes manually with
        ``mneme workspace-enable --scope NAME`` before any workspace_* write/read
        operations can succeed.
        """

        db = NotesDB(notes_db_path)
        try:
            return _workspace_status(db)
        finally:
            db.close()

    @mcp.tool()
    def workspace_ls(scope: str, prefix: str = "") -> dict[str, Any]:
        """List files in a workspace scope, optionally filtered by a path prefix."""

        db = NotesDB(notes_db_path)
        try:
            files = _list_workspace(db, scope, prefix=prefix)
        finally:
            db.close()
        return {"scope": scope, "results": [f.to_dict() for f in files]}

    @mcp.tool()
    def workspace_read(scope: str, path: str) -> dict[str, Any]:
        """Read a file from a workspace scope.

        Returns ``content`` (UTF-8) or base64 in ``content`` with ``binary=true``.
        """

        db = NotesDB(notes_db_path)
        try:
            return _read_workspace(db, scope, path)
        finally:
            db.close()

    @mcp.tool()
    def workspace_write(scope: str, path: str, content: str) -> dict[str, Any]:
        """Write text to a file inside a workspace scope.

        The scope must already be enabled by the user. Paths are restricted to the
        scope directory; ``..`` segments and symlinks are rejected.
        """

        db = NotesDB(notes_db_path)
        try:
            info = _write_workspace(db, scope, path, content)
        finally:
            db.close()
        return info.to_dict()

    @mcp.tool()
    def workspace_rm(scope: str, path: str) -> dict[str, Any]:
        """Remove a file from a workspace scope."""

        db = NotesDB(notes_db_path)
        try:
            removed = _remove_workspace(db, scope, path)
        finally:
            db.close()
        return {"removed": removed, "scope": scope, "path": path}

    return mcp


def run_mcp_server(
    *,
    db_path: str | None = None,
    auth_config: str | None = None,
    notes_db_path: str | None = None,
    transport: str = "stdio",
) -> None:
    mcp = create_mcp_server(
        db_path=db_path,
        auth_config=auth_config,
        notes_db_path=notes_db_path,
    )
    if transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=transport)


def main() -> None:
    run_mcp_server()


if __name__ == "__main__":
    main()
