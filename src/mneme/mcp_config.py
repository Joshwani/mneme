"""Render ready-to-paste MCP client configurations for popular agents."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from mneme.index.db import default_db_path

CLIENTS = ("cursor", "claude", "continue", "generic")

CLIENT_NOTES = {
    "cursor": (
        "Cursor reads MCP servers from either of these files:\n"
        "  - Per-project: <repo>/.cursor/mcp.json\n"
        "  - Global:      ~/.cursor/mcp.json\n"
        "Merge the snippet below into the top-level `mcpServers` object."
    ),
    "claude": (
        "Claude Desktop reads MCP servers from:\n"
        "  - macOS:   ~/Library/Application Support/Claude/claude_desktop_config.json\n"
        "  - Windows: %APPDATA%/Claude/claude_desktop_config.json\n"
        "Merge the snippet below into the top-level `mcpServers` object.\n"
        "If your client does not apply MCP server instructions, add this to your Claude "
        "project instructions:\n"
        '  "Mneme is a searchable catalog. Use search_callables or search_operations '
        'before deciding an API or library capability is unavailable."'
    ),
    "continue": (
        "Continue (continue.dev) supports MCP via its config.json or config.yaml.\n"
        "  - Default path: ~/.continue/config.json\n"
        "Add the entry below under the `mcpServers` object."
    ),
    "generic": (
        "Generic stdio MCP client config. Place the snippet wherever your client\n"
        "expects an `mcpServers` map. If the client ignores MCP server instructions,\n"
        "tell its agent to use Mneme's search_callables or search_operations tool before\n"
        "deciding a capability is unavailable."
    ),
}


def _resolve_executable() -> str:
    """Return an absolute path to the mneme CLI when possible."""

    found = shutil.which("mneme")
    if found:
        return str(Path(found).resolve())
    # Fall back to the bare command name; the user can edit the JSON.
    return "mneme"


def build_mcp_server_entry(
    *,
    db_path: str | None = None,
    auth_config: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the JSON entry that represents the Mneme MCP server."""

    abs_db = str(Path(db_path or default_db_path()).expanduser().resolve())
    args: list[str] = ["--db", abs_db, "mcp-server"]
    if auth_config:
        args.extend(["--auth-config", str(Path(auth_config).expanduser().resolve())])

    entry: dict[str, Any] = {
        "command": _resolve_executable(),
        "args": args,
    }
    if env:
        entry["env"] = dict(env)
    return entry


def build_mcp_config(
    *,
    client: str,
    db_path: str | None = None,
    auth_config: str | None = None,
    env: dict[str, str] | None = None,
    server_name: str = "mneme",
) -> dict[str, Any]:
    """Build the top-level config snippet for a given client."""

    if client not in CLIENTS:
        raise ValueError(f"unknown client {client!r}; expected one of {', '.join(CLIENTS)}")

    entry = build_mcp_server_entry(db_path=db_path, auth_config=auth_config, env=env)
    return {"mcpServers": {server_name: entry}}


def render(
    *,
    client: str,
    db_path: str | None = None,
    auth_config: str | None = None,
    env: dict[str, str] | None = None,
    server_name: str = "mneme",
    include_notes: bool = True,
) -> str:
    """Render the snippet plus optional human-readable notes."""

    config = build_mcp_config(
        client=client,
        db_path=db_path,
        auth_config=auth_config,
        env=env,
        server_name=server_name,
    )
    snippet = json.dumps(config, indent=2)
    if not include_notes:
        return snippet
    note = CLIENT_NOTES.get(client, "")
    header = f"# {client} MCP configuration"
    return f"{header}\n{note}\n\n{snippet}\n"


def parse_env_pairs(items: list[str] | None) -> dict[str, str]:
    """Parse repeated --env KEY=VALUE arguments into a dict."""

    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            print(f"warning: ignoring malformed --env {item!r}", file=sys.stderr)
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            continue
        out[key] = value
    return out
