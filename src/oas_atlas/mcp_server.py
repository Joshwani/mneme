from __future__ import annotations

import os
from typing import Any

from oas_atlas.auth import list_auth_profiles, load_auth_profiles
from oas_atlas.http_client import CallInputs, execute_operation_call, prepare_operation_call
from oas_atlas.index.db import AtlasDB, default_db_path
from oas_atlas.index.search import SearchFilters, search_operations as search_index_operations


def create_mcp_server(db_path: str | None = None, auth_config: str | None = None) -> Any:
    """Create a local MCP server exposing OAS Atlas search and HTTP execution tools."""

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without optional extra.
        raise RuntimeError(
            "The MCP server requires the optional MCP dependency. Install with: "
            "python -m pip install 'oas-atlas[mcp]'"
        ) from exc

    db_path = db_path or default_db_path()
    auth_config = auth_config or os.environ.get("OAS_ATLAS_AUTH_CONFIG")
    mcp = FastMCP("OAS Atlas", json_response=True)

    def get_operation_or_error(operation_id: str) -> dict[str, Any]:
        db = AtlasDB(db_path)
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

        db = AtlasDB(db_path)
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

        from oas_atlas.call_template import build_call_template

        return build_call_template(get_operation_or_error(operation_id))

    @mcp.tool()
    def list_local_auth_profiles() -> dict[str, Any]:
        """List local auth profiles without exposing secret values.

        Profiles are read from OAS_ATLAS_AUTH_CONFIG or ~/.config/oas-atlas/auth.json.
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
    def atlas_stats() -> dict[str, Any]:
        """Return index statistics for this local OAS Atlas database."""

        db = AtlasDB(db_path)
        try:
            stats = db.stats()
            stats["auth_config_present"] = bool(load_auth_profiles(auth_config))
            return stats
        finally:
            db.close()

    return mcp


def run_mcp_server(
    *,
    db_path: str | None = None,
    auth_config: str | None = None,
    transport: str = "stdio",
) -> None:
    mcp = create_mcp_server(db_path=db_path, auth_config=auth_config)
    if transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=transport)


def main() -> None:
    run_mcp_server()


if __name__ == "__main__":
    main()
