from __future__ import annotations

import os
from typing import Annotated, Any

from pydantic import Field

from mneme.auth import list_auth_profiles, load_auth_profiles
from mneme.http_client import CallInputs, execute_operation_call, prepare_operation_call
from mneme.index.db import MnemeDB, default_db_path
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

SearchQuery = Annotated[
    str,
    Field(
        description=(
            "Natural-language description of the capability needed. Describe the task rather "
            "than guessing an endpoint or symbol name."
        )
    ),
]
OperationID = Annotated[
    str,
    Field(
        description=(
            "Opaque operation_id returned by search_operations or an http_operation result from "
            "search_callables. Do not invent this value."
        )
    ),
]
ProviderDomain = Annotated[
    str | None,
    Field(
        description=(
            "Optional exact provider domain from catalog_summary, such as api.github.com. "
            "Omit this filter when unsure."
        )
    ),
]


def _build_server_instructions(db_path: str) -> str:
    db = MnemeDB(db_path)
    try:
        summary = db.catalog_summary()
    finally:
        db.close()

    provider_domains = [
        provider["provider_domain"] for provider in summary["indexed_providers"][:12]
    ]
    provider_preview = ", ".join(provider_domains) or "none"
    if len(summary["indexed_providers"]) > len(provider_domains):
        provider_preview += ", ..."

    return f"""Mneme is a local searchable catalog of capabilities selected by the user.
The visible MCP tools are discovery and execution tools, not the full capability set.

Current catalog snapshot: {summary["operations"]} HTTP operations across
{summary["providers"]} providers and {summary["library_symbols"]} library symbols across
{summary["libraries"]} packages. Indexed provider domains: {provider_preview}.

For any task that may be served by an API or library, call search_callables first. For an
HTTP-only task, call search_operations. Use catalog_summary when you need the exact inventory.
Search with a natural-language task description. If a search returns no results, retry with
broader terms and without optional filters before concluding the capability is unavailable.

For HTTP calls, use the returned operation_id with get_operation or get_spec_slice, then call
prepare_http_call. Only call execute_http_call when execution is requested. It defaults to a
dry run; a real request requires dry_run=false and confirm=true. Credentials are injected from
local auth profiles, remain redacted, and must not be requested from the user or passed directly."""


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
    mcp = FastMCP(
        "Mneme",
        instructions=_build_server_instructions(db_path),
        json_response=True,
    )

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
        query: SearchQuery,
        limit: int = 10,
        provider_domain: ProviderDomain = None,
        method: str | None = None,
        auth_required: bool | None = None,
        token_budget: int | None = 4000,
    ) -> dict[str, Any]:
        """Discover HTTP capabilities in the user's indexed API catalog.

        This is the primary HTTP discovery tool; indexed operations do not appear
        individually in tools/list. Search by task, then use a returned operation_id
        with get_operation, get_spec_slice, prepare_http_call, or execute_http_call.
        Retry with broader terms and no provider filter before deciding an operation
        is unavailable.
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
    def get_operation(operation_id: OperationID) -> dict[str, Any]:
        """Inspect an operation found by search_operations or search_callables."""

        return get_operation_or_error(operation_id)

    @mcp.tool()
    def get_spec_slice(operation_id: OperationID) -> dict[str, Any]:
        """Return the minimal OpenAPI-style slice for an operation found through search."""

        return get_operation_or_error(operation_id).get("spec_slice") or {}

    @mcp.tool()
    def get_call_template(operation_id: OperationID) -> dict[str, Any]:
        """Return a non-executing call template for an operation found through search."""

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
        operation_id: OperationID,
        auth_profile: str | None = None,
        path_params: dict[str, Any] | None = None,
        query_params: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        json_body: Any = None,
        form_body: dict[str, Any] | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        """Prepare a redacted HTTP request for an indexed operation without sending it.

        The operation_id must come from search_operations or search_callables. Use this
        after inspecting the operation to check the resolved URL, missing required
        inputs, selected auth profile, and request body before making a real call.
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
        operation_id: OperationID,
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

        The operation_id must come from search_operations or search_callables. Defaults
        to dry_run=true, which returns a redacted prepared request and sends no network
        traffic. To perform a real request, set dry_run=false and confirm=true. Secrets
        are injected locally from the selected auth profile and never returned.
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
        query: SearchQuery,
        limit: int = 10,
        kinds: list[str] | None = None,
        provider_domain: ProviderDomain = None,
        method: str | None = None,
        auth_required: bool | None = None,
        language: str | None = None,
        package_name: str | None = None,
        token_budget: int | None = 4000,
    ) -> dict[str, Any]:
        """Primary discovery tool for all capabilities selected and indexed by the user.

        Indexed operations and symbols do not appear individually in tools/list. This
        searches the full catalog and returns mixed results ranked by relevance. Each
        hit has a ``kind`` of 'http_operation', 'pylib_symbol', or 'jslib_symbol'.
        Use ``kinds`` to restrict the search; for HTTP-only discovery, use
        search_operations. Retry more broadly before concluding a capability is absent.
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
    def catalog_summary() -> dict[str, Any]:
        """List the user's indexed providers, API titles, operation counts, and libraries.

        Use this to understand the exact capability inventory before searching. The
        provider_domain values returned here can be passed as exact filters to
        search_operations or search_callables.
        """

        db = MnemeDB(db_path)
        try:
            return db.catalog_summary()
        finally:
            db.close()

    @mcp.tool()
    def mneme_stats() -> dict[str, Any]:
        """Return aggregate index diagnostics; use catalog_summary for capability names."""

        db = MnemeDB(db_path)
        try:
            stats = db.stats()
            stats["auth_config_present"] = bool(load_auth_profiles(auth_config))
            return stats
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
