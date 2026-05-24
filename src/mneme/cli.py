from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from mneme.auth import list_auth_profiles, load_auth_profiles
from mneme.call_template import build_call_template
from mneme.crawl.discover import discover_domain
from mneme.crawl.seeds import read_seed_file, seed_looks_like_spec_url
from mneme.fetch import Fetcher
from mneme.http_client import CallInputs, execute_operation_call, prepare_operation_call
from mneme.index.db import MnemeDB, DEFAULT_DB_PATH
from mneme.index.ingest import ingest_apis_guru, ingest_file, ingest_url
from mneme.index.search import SearchFilters, search_operations
from mneme.util import pretty_json


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        if getattr(args, "json", False):
            print(pretty_json({"error": str(exc)}), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mneme",
        description="Agent-optimized search over OpenAPI specifications.",
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite index path")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add-spec", help="Fetch and index one OpenAPI spec URL")
    add.add_argument("url")
    add.add_argument("--discovered-via", default="manual")
    add.set_defaults(func=cmd_add_spec)

    add_file = sub.add_parser("add-file", help="Index one local OpenAPI file")
    add_file.add_argument("path")
    add_file.set_defaults(func=cmd_add_file)

    discover = sub.add_parser("discover", help="Discover OpenAPI specs for a domain")
    discover.add_argument("domain")
    discover.add_argument(
        "--no-validate", action="store_true", help="Return unvalidated candidates"
    )
    discover.add_argument("--ingest", action="store_true", help="Index discovered valid specs")
    discover.add_argument("--max-candidates", type=int, default=50)
    discover.set_defaults(func=cmd_discover)

    seeds = sub.add_parser("crawl-seeds", help="Index seeds from a file of domains/spec URLs")
    seeds.add_argument("seed_file")
    seeds.add_argument("--max-candidates-per-domain", type=int, default=20)
    seeds.set_defaults(func=cmd_crawl_seeds)

    guru = sub.add_parser("ingest-apis-guru", help="Index specs from APIs.guru directory")
    guru.add_argument(
        "--limit", type=int, default=25, help="Default is 25 for MVP safety; use 0 for no limit"
    )
    guru.set_defaults(func=cmd_ingest_apis_guru)

    search = sub.add_parser("search", help="Search indexed operations")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--method")
    search.add_argument("--provider-domain")
    search.add_argument("--auth-required", choices=["true", "false"])
    search.add_argument("--token-budget", type=int)
    search.set_defaults(func=cmd_search)

    template = sub.add_parser(
        "template", help="Build a non-executing call template for an operation"
    )
    template.add_argument("operation_id")
    template.set_defaults(func=cmd_template)

    auth = sub.add_parser("auth-profiles", help="List local auth profiles without secrets")
    auth.add_argument("--auth-config")
    auth.set_defaults(func=cmd_auth_profiles)

    prepare = sub.add_parser("prepare-call", help="Prepare a redacted HTTP call for an operation")
    _add_call_args(prepare)
    prepare.set_defaults(func=cmd_prepare_call)

    execute = sub.add_parser(
        "execute-call", help="Execute an HTTP call for an operation; dry-run by default"
    )
    _add_call_args(execute)
    execute.add_argument("--send", action="store_true", help="Send the request instead of dry-run")
    execute.add_argument("--confirm", action="store_true", help="Required with --send")
    execute.add_argument("--timeout", type=float, default=30.0)
    execute.set_defaults(func=cmd_execute_call)

    mcp = sub.add_parser("mcp-server", help="Run the local MCP server over stdio by default")
    mcp.add_argument("--auth-config")
    mcp.add_argument("--notes-db", default=None, help="Override notes DB path")
    mcp.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    mcp.set_defaults(func=cmd_mcp_server)

    stats = sub.add_parser("stats", help="Show index stats")
    stats.set_defaults(func=cmd_stats)

    serve = sub.add_parser("serve", help="Serve the HTTP search API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    demo = sub.add_parser(
        "demo",
        help="Index the bundled example spec and run a sample search.",
    )
    demo.add_argument(
        "--query",
        default=None,
        help="Override the demo search query.",
    )
    demo.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the human-friendly summary.",
    )
    demo.set_defaults(func=cmd_demo)

    mcp_config = sub.add_parser(
        "mcp-config",
        help="Print a ready-to-paste MCP client config for Mneme.",
    )
    mcp_config.add_argument(
        "--client",
        choices=("cursor", "claude", "continue", "generic"),
        default="generic",
    )
    mcp_config.add_argument(
        "--auth-config",
        help="Absolute path to your auth.json (optional).",
    )
    mcp_config.add_argument(
        "--server-name",
        default="mneme",
        help="Key used under mcpServers (default: mneme).",
    )
    mcp_config.add_argument(
        "--env",
        action="append",
        metavar="KEY=VALUE",
        help="Add an environment variable to the server entry. Repeatable.",
    )
    mcp_config.add_argument(
        "--json",
        action="store_true",
        help="Print only the JSON snippet, no header notes.",
    )
    mcp_config.set_defaults(func=cmd_mcp_config)

    doctor = sub.add_parser(
        "doctor",
        help="Print environment diagnostics for triage.",
    )
    doctor.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the human-friendly summary.",
    )
    doctor.set_defaults(func=cmd_doctor)

    _add_notes_parsers(sub)
    _add_workspace_parsers(sub)

    return parser


def _add_notes_parsers(sub: argparse._SubParsersAction) -> None:
    from mneme.memory.db import default_notes_db_path

    common_kwargs = {"default": default_notes_db_path(), "help": "Notes DB path"}

    notes_add = sub.add_parser("notes-add", help="Add a note to the notebook")
    notes_add.add_argument("--notes-db", **common_kwargs)
    notes_add.add_argument("--title", required=True)
    notes_add.add_argument("--body", default=None)
    notes_add.add_argument(
        "--body-file", default=None, help="Read body from file (use '-' for stdin)"
    )
    notes_add.add_argument("--tag", action="append", default=[], help="Repeatable tag")
    notes_add.add_argument("--scope", default=None)
    notes_add.add_argument("--source", default=None)
    notes_add.add_argument("--json", action="store_true")
    notes_add.set_defaults(func=cmd_notes_add)

    notes_search = sub.add_parser("notes-search", help="Full-text search notes")
    notes_search.add_argument("--notes-db", **common_kwargs)
    notes_search.add_argument("query")
    notes_search.add_argument("--scope", default=None)
    notes_search.add_argument("--tag", default=None)
    notes_search.add_argument("--limit", type=int, default=20)
    notes_search.add_argument("--json", action="store_true")
    notes_search.set_defaults(func=cmd_notes_search)

    notes_get = sub.add_parser("notes-get", help="Get a single note by ID")
    notes_get.add_argument("--notes-db", **common_kwargs)
    notes_get.add_argument("note_id")
    notes_get.add_argument("--json", action="store_true")
    notes_get.set_defaults(func=cmd_notes_get)

    notes_list = sub.add_parser("notes-list", help="List recent notes")
    notes_list.add_argument("--notes-db", **common_kwargs)
    notes_list.add_argument("--scope", default=None)
    notes_list.add_argument("--tag", default=None)
    notes_list.add_argument("--limit", type=int, default=50)
    notes_list.add_argument("--json", action="store_true")
    notes_list.set_defaults(func=cmd_notes_list)

    notes_update = sub.add_parser("notes-update", help="Update an existing note")
    notes_update.add_argument("--notes-db", **common_kwargs)
    notes_update.add_argument("note_id")
    notes_update.add_argument("--title", default=None)
    notes_update.add_argument("--body", default=None)
    notes_update.add_argument("--body-file", default=None)
    notes_update.add_argument(
        "--tag", action="append", default=None, help="Replaces all tags. Repeatable."
    )
    notes_update.add_argument("--scope", default=None)
    notes_update.add_argument("--json", action="store_true")
    notes_update.set_defaults(func=cmd_notes_update)

    notes_delete = sub.add_parser("notes-delete", help="Delete a note")
    notes_delete.add_argument("--notes-db", **common_kwargs)
    notes_delete.add_argument("note_id")
    notes_delete.add_argument("--json", action="store_true")
    notes_delete.set_defaults(func=cmd_notes_delete)


def _add_workspace_parsers(sub: argparse._SubParsersAction) -> None:
    from mneme.memory.db import default_notes_db_path
    from mneme.memory.workspace import DEFAULT_MAX_BYTES

    common_kwargs = {"default": default_notes_db_path(), "help": "Notes DB path"}

    ws_status = sub.add_parser(
        "workspace-status",
        help="Show enabled workspace scopes and usage",
    )
    ws_status.add_argument("--notes-db", **common_kwargs)
    ws_status.add_argument("--json", action="store_true")
    ws_status.set_defaults(func=cmd_workspace_status)

    ws_enable = sub.add_parser(
        "workspace-enable",
        help="Enable a workspace scope so the agent can read/write files within it",
    )
    ws_enable.add_argument("--notes-db", **common_kwargs)
    ws_enable.add_argument("--scope", required=True)
    ws_enable.add_argument("--root", default=None, help="Override the workspace root directory")
    ws_enable.add_argument("--max-mb", type=int, default=max(1, DEFAULT_MAX_BYTES // (1024 * 1024)))
    ws_enable.add_argument("--json", action="store_true")
    ws_enable.set_defaults(func=cmd_workspace_enable)

    ws_disable = sub.add_parser("workspace-disable", help="Disable a workspace scope")
    ws_disable.add_argument("--notes-db", **common_kwargs)
    ws_disable.add_argument("--scope", required=True)
    ws_disable.add_argument(
        "--remove-files",
        action="store_true",
        help="Also delete the scope directory on disk",
    )
    ws_disable.add_argument("--json", action="store_true")
    ws_disable.set_defaults(func=cmd_workspace_disable)

    ws_ls = sub.add_parser("workspace-ls", help="List files in a workspace scope")
    ws_ls.add_argument("--notes-db", **common_kwargs)
    ws_ls.add_argument("--scope", required=True)
    ws_ls.add_argument("--prefix", default="")
    ws_ls.add_argument("--json", action="store_true")
    ws_ls.set_defaults(func=cmd_workspace_ls)

    ws_read = sub.add_parser("workspace-read", help="Read a file from a workspace scope")
    ws_read.add_argument("--notes-db", **common_kwargs)
    ws_read.add_argument("--scope", required=True)
    ws_read.add_argument("--path", required=True)
    ws_read.add_argument("--json", action="store_true")
    ws_read.set_defaults(func=cmd_workspace_read)

    ws_write = sub.add_parser("workspace-write", help="Write a file to a workspace scope")
    ws_write.add_argument("--notes-db", **common_kwargs)
    ws_write.add_argument("--scope", required=True)
    ws_write.add_argument("--path", required=True)
    ws_write.add_argument("--content", default=None)
    ws_write.add_argument(
        "--content-file", default=None, help="Read content from file (use '-' for stdin)"
    )
    ws_write.add_argument("--json", action="store_true")
    ws_write.set_defaults(func=cmd_workspace_write)

    ws_rm = sub.add_parser("workspace-rm", help="Remove a file from a workspace scope")
    ws_rm.add_argument("--notes-db", **common_kwargs)
    ws_rm.add_argument("--scope", required=True)
    ws_rm.add_argument("--path", required=True)
    ws_rm.add_argument("--json", action="store_true")
    ws_rm.set_defaults(func=cmd_workspace_rm)


def _add_call_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("operation_id")
    parser.add_argument("--auth-config")
    parser.add_argument("--auth-profile")
    parser.add_argument("--base-url")
    parser.add_argument("--path-param", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--query-param", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--header", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--json-body", help="JSON request body string")
    parser.add_argument(
        "--form", action="append", default=[], metavar="KEY=VALUE", help="Form field"
    )


def cmd_add_spec(args: argparse.Namespace) -> int:
    db = MnemeDB(args.db)
    try:
        result = ingest_url(db, args.url, discovered_via=args.discovered_via)
        print(pretty_json(result))
    finally:
        db.close()
    return 0


def cmd_add_file(args: argparse.Namespace) -> int:
    db = MnemeDB(args.db)
    try:
        result = ingest_file(db, args.path)
        print(pretty_json(result))
    finally:
        db.close()
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    fetcher = Fetcher()
    candidates = discover_domain(
        args.domain,
        fetcher=fetcher,
        validate=not args.no_validate,
        max_candidates=args.max_candidates,
    )
    if not args.ingest:
        from dataclasses import asdict

        print(pretty_json([asdict(candidate) for candidate in candidates]))
        return 0
    db = MnemeDB(args.db)
    results: list[dict[str, Any]] = []
    try:
        for candidate in candidates:
            try:
                result = ingest_url(
                    db,
                    candidate.url,
                    fetcher=fetcher,
                    discovered_via=candidate.discovered_via,
                )
                result["url"] = candidate.url
                results.append(result)
            except Exception as exc:
                results.append({"url": candidate.url, "error": str(exc)})
    finally:
        db.close()
    print(pretty_json({"domain": args.domain, "results": results}))
    return 0


def cmd_crawl_seeds(args: argparse.Namespace) -> int:
    db = MnemeDB(args.db)
    fetcher = Fetcher()
    summary = {"seeds": 0, "specs_ok": 0, "operations": 0, "errors": []}
    try:
        for seed in read_seed_file(args.seed_file):
            summary["seeds"] += 1
            if seed_looks_like_spec_url(seed):
                try:
                    result = ingest_url(db, seed, fetcher=fetcher, discovered_via="seed_file")
                    summary["specs_ok"] += 1
                    summary["operations"] += int(result.get("operations", 0))
                except Exception as exc:
                    summary["errors"].append({"seed": seed, "error": str(exc)})
            else:
                try:
                    candidates = discover_domain(
                        seed,
                        fetcher=fetcher,
                        validate=True,
                        max_candidates=args.max_candidates_per_domain,
                    )
                    for candidate in candidates:
                        try:
                            result = ingest_url(
                                db,
                                candidate.url,
                                fetcher=fetcher,
                                discovered_via=candidate.discovered_via,
                            )
                            summary["specs_ok"] += 1
                            summary["operations"] += int(result.get("operations", 0))
                        except Exception as exc:
                            summary["errors"].append(
                                {"seed": seed, "url": candidate.url, "error": str(exc)}
                            )
                except Exception as exc:
                    summary["errors"].append({"seed": seed, "error": str(exc)})
    finally:
        db.close()
    print(pretty_json(summary))
    return 0


def cmd_ingest_apis_guru(args: argparse.Namespace) -> int:
    db = MnemeDB(args.db)
    try:
        limit = None if args.limit == 0 else args.limit
        result = ingest_apis_guru(db, limit=limit)
        print(pretty_json(result))
    finally:
        db.close()
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    db = MnemeDB(args.db)
    try:
        auth_required = None
        if args.auth_required == "true":
            auth_required = True
        elif args.auth_required == "false":
            auth_required = False
        filters = SearchFilters(
            provider_domain=args.provider_domain,
            method=args.method,
            auth_required=auth_required,
        )
        result = search_operations(
            db,
            args.query,
            limit=args.limit,
            filters=filters,
            token_budget=args.token_budget,
        )
        print(pretty_json(result))
    finally:
        db.close()
    return 0


def cmd_template(args: argparse.Namespace) -> int:
    db = MnemeDB(args.db)
    try:
        op = db.get_operation(args.operation_id)
        if op is None:
            raise ValueError(f"operation not found: {args.operation_id}")
        print(pretty_json(build_call_template(op)))
    finally:
        db.close()
    return 0


def cmd_auth_profiles(args: argparse.Namespace) -> int:
    print(pretty_json(list_auth_profiles(args.auth_config)))
    return 0


def cmd_prepare_call(args: argparse.Namespace) -> int:
    db = MnemeDB(args.db)
    try:
        op = db.get_operation(args.operation_id)
        if op is None:
            raise ValueError(f"operation not found: {args.operation_id}")
        profiles = load_auth_profiles(args.auth_config)
        result = prepare_operation_call(
            op,
            auth_profiles=profiles,
            inputs=_call_inputs_from_args(args),
        )
        print(pretty_json(result))
    finally:
        db.close()
    return 0


def cmd_execute_call(args: argparse.Namespace) -> int:
    db = MnemeDB(args.db)
    try:
        op = db.get_operation(args.operation_id)
        if op is None:
            raise ValueError(f"operation not found: {args.operation_id}")
        profiles = load_auth_profiles(args.auth_config)
        result = execute_operation_call(
            op,
            auth_profiles=profiles,
            inputs=_call_inputs_from_args(args),
            dry_run=not args.send,
            confirm=args.confirm,
            timeout=args.timeout,
        )
        print(pretty_json(result))
    finally:
        db.close()
    return 0


def cmd_mcp_server(args: argparse.Namespace) -> int:
    from mneme.mcp_server import run_mcp_server

    run_mcp_server(
        db_path=args.db,
        auth_config=args.auth_config,
        notes_db_path=getattr(args, "notes_db", None),
        transport=args.transport,
    )
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    db = MnemeDB(args.db)
    try:
        print(pretty_json(db.stats()))
    finally:
        db.close()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import os

    import uvicorn

    os.environ["MNEME_DB"] = args.db
    uvicorn.run("mneme.api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from mneme.demo import DEMO_QUERY, format_next_steps, run_demo

    query = args.query or DEMO_QUERY
    result = run_demo(args.db, query=query, limit=3)
    if getattr(args, "json", False):
        print(pretty_json(result))
        return 0

    ingest = result["ingest"]
    print(f"Indexed demo spec: {ingest.get('title') or ingest.get('spec_id')}")
    print(f"Operations: {ingest.get('operations')}")
    print(f"\nSearch:  {query!r}")
    hits = result["search"].get("results", []) or []
    if not hits:
        print("  (no results)")
    for hit in hits:
        method = hit.get("method", "?")
        path = hit.get("path", "?")
        summary = hit.get("summary") or ""
        print(f"  - [{method}] {path}  {summary}".rstrip())
    print(format_next_steps(result["db_path"]))
    return 0


def cmd_mcp_config(args: argparse.Namespace) -> int:
    from mneme.mcp_config import parse_env_pairs, render

    output = render(
        client=args.client,
        db_path=args.db,
        auth_config=args.auth_config,
        env=parse_env_pairs(args.env),
        server_name=args.server_name,
        include_notes=not args.json,
    )
    print(output)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from mneme.doctor import collect

    diagnostics = collect(args.db)
    if getattr(args, "json", False):
        print(pretty_json(diagnostics))
        return 0

    def _line(label: str, value: Any) -> None:
        print(f"  {label:<22} {value}")

    print("mneme doctor")
    pkg = diagnostics["mneme"]
    _line("package version:", pkg.get("version") or "unknown")
    _line("python:", diagnostics["python"]["version"])
    _line("python executable:", diagnostics["python"]["executable"])
    _line("platform:", f"{diagnostics['platform']['system']} {diagnostics['platform']['release']}")
    _line("cli on PATH:", diagnostics["cli_on_path"] or "(not found)")

    print("\ndatabase")
    db_info = diagnostics["db"]
    _line("path:", db_info["path"])
    _line("exists:", db_info["exists"])
    if db_info["exists"]:
        _line("size (bytes):", db_info["size_bytes"])
        stats = db_info.get("stats") or {}
        _line("specs:", stats.get("specs"))
        _line("operations:", stats.get("operations"))
    if db_info.get("open_error"):
        _line("open error:", db_info["open_error"])

    print("\nnotes db")
    notes_info = diagnostics.get("notes_db") or {}
    _line("path:", notes_info.get("path"))
    _line("exists:", notes_info.get("exists"))
    if notes_info.get("exists"):
        _line("size (bytes):", notes_info.get("size_bytes"))

    print("\nenvironment")
    for key, value in diagnostics["env"].items():
        _line(f"{key}:", value if value is not None else "(unset)")

    print("\nextras")
    for name, info in diagnostics["extras"].items():
        marker = "ok " if info["installed"] else "no "
        version = info["version"] or ""
        _line(f"{marker} {name}:", version)

    print("\ndev tools")
    for name, path in diagnostics["dev_tools"].items():
        _line(f"{name}:", path or "(not found)")

    print("\nnetwork")
    network = diagnostics["network"]
    _line("apis.guru reachable:", network.get("ok"))
    if not network.get("ok"):
        _line("error:", network.get("error") or network.get("status"))

    return 0


def _call_inputs_from_args(args: argparse.Namespace) -> CallInputs:
    return CallInputs(
        path_params=_parse_kv_list(args.path_param),
        query_params=_parse_kv_list(args.query_param),
        headers=_parse_kv_list(args.header),
        json_body=_parse_json_arg(args.json_body),
        form_body=_parse_kv_list(args.form) if args.form else None,
        base_url=args.base_url,
        auth_profile=args.auth_profile,
    )


def _parse_kv_list(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"expected KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"empty key in {item!r}")
        out[key] = value
    return out


def _parse_json_arg(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON body: {exc}") from exc


def _read_text_input(inline: str | None, from_file: str | None) -> str:
    if inline is not None:
        return inline
    if from_file == "-":
        return sys.stdin.read()
    if from_file:
        with open(from_file, "r", encoding="utf-8") as fh:
            return fh.read()
    return ""


def _open_notes_db(path: str):
    from mneme.memory.db import NotesDB

    return NotesDB(path)


def cmd_notes_add(args: argparse.Namespace) -> int:
    from mneme.memory.notes import add_note

    body = _read_text_input(args.body, args.body_file)
    db = _open_notes_db(args.notes_db)
    try:
        note = add_note(
            db,
            title=args.title,
            body=body,
            tags=args.tag or [],
            scope=args.scope,
            source=args.source,
        )
    finally:
        db.close()

    if args.json:
        print(pretty_json(note.to_dict()))
    else:
        print(note.note_id)
    return 0


def cmd_notes_search(args: argparse.Namespace) -> int:
    from mneme.memory.notes import search_notes

    db = _open_notes_db(args.notes_db)
    try:
        notes = search_notes(
            db,
            args.query,
            scope=args.scope,
            tag=args.tag,
            limit=args.limit,
        )
    finally:
        db.close()

    payload = [n.to_dict() for n in notes]
    if args.json:
        print(pretty_json(payload))
        return 0
    if not payload:
        print("(no matches)")
        return 0
    for n in payload:
        tags = f"  [{', '.join(n['tags'])}]" if n["tags"] else ""
        scope = f"  scope={n['scope']}" if n["scope"] else ""
        print(f"{n['note_id']}  {n['title']}{tags}{scope}")
    return 0


def cmd_notes_get(args: argparse.Namespace) -> int:
    from mneme.memory.notes import get_note

    db = _open_notes_db(args.notes_db)
    try:
        note = get_note(db, args.note_id)
    finally:
        db.close()
    if note is None:
        print(f"note not found: {args.note_id}", file=sys.stderr)
        return 1
    if args.json:
        print(pretty_json(note.to_dict()))
    else:
        print(f"# {note.title}")
        if note.tags:
            print(f"tags: {', '.join(note.tags)}")
        if note.scope:
            print(f"scope: {note.scope}")
        print(f"id: {note.note_id}")
        print(f"updated: {note.updated_at}")
        print()
        print(note.body)
    return 0


def cmd_notes_list(args: argparse.Namespace) -> int:
    from mneme.memory.notes import list_notes

    db = _open_notes_db(args.notes_db)
    try:
        notes = list_notes(db, scope=args.scope, tag=args.tag, limit=args.limit)
    finally:
        db.close()
    payload = [n.to_dict() for n in notes]
    if args.json:
        print(pretty_json(payload))
        return 0
    if not payload:
        print("(no notes)")
        return 0
    for n in payload:
        tags = f"  [{', '.join(n['tags'])}]" if n["tags"] else ""
        print(f"{n['updated_at']}  {n['note_id']}  {n['title']}{tags}")
    return 0


def cmd_notes_update(args: argparse.Namespace) -> int:
    from mneme.memory.notes import update_note

    body: str | None
    if args.body is not None or args.body_file is not None:
        body = _read_text_input(args.body, args.body_file)
    else:
        body = None

    db = _open_notes_db(args.notes_db)
    try:
        note = update_note(
            db,
            args.note_id,
            title=args.title,
            body=body,
            tags=args.tag,
            scope=args.scope,
        )
    finally:
        db.close()
    if args.json:
        print(pretty_json(note.to_dict()))
    else:
        print(f"updated: {note.note_id}")
    return 0


def cmd_notes_delete(args: argparse.Namespace) -> int:
    from mneme.memory.notes import delete_note

    db = _open_notes_db(args.notes_db)
    try:
        deleted = delete_note(db, args.note_id)
    finally:
        db.close()
    if args.json:
        print(pretty_json({"deleted": deleted, "note_id": args.note_id}))
    else:
        print("deleted" if deleted else "no such note")
    return 0 if deleted else 1


def cmd_workspace_status(args: argparse.Namespace) -> int:
    from mneme.memory.workspace import workspace_status

    db = _open_notes_db(args.notes_db)
    try:
        status = workspace_status(db)
    finally:
        db.close()
    if args.json:
        print(pretty_json(status))
        return 0
    print(f"workspace root: {status['default_root']}")
    if not status["scopes"]:
        print("(no scopes enabled)")
        return 0
    for s in status["scopes"]:
        print(
            f"  {s['scope']}  base={s['base_path']}  "
            f"used={s['used_bytes']}B  files={s['file_count']}  "
            f"max={s['max_bytes']}B  enabled={s['enabled_at']}"
        )
    return 0


def cmd_workspace_enable(args: argparse.Namespace) -> int:
    from mneme.memory.workspace import enable_scope

    db = _open_notes_db(args.notes_db)
    try:
        info = enable_scope(
            db,
            args.scope,
            base_root=args.root,
            max_bytes=int(args.max_mb) * 1024 * 1024,
        )
    finally:
        db.close()
    if args.json:
        print(pretty_json(info.to_dict()))
    else:
        print(f"enabled: {info.scope} -> {info.base_path} (max {info.max_bytes} bytes)")
    return 0


def cmd_workspace_disable(args: argparse.Namespace) -> int:
    from mneme.memory.workspace import disable_scope

    db = _open_notes_db(args.notes_db)
    try:
        ok = disable_scope(db, args.scope, remove_files=args.remove_files)
    finally:
        db.close()
    if args.json:
        print(pretty_json({"disabled": ok, "scope": args.scope}))
    else:
        print("disabled" if ok else "no such scope")
    return 0 if ok else 1


def cmd_workspace_ls(args: argparse.Namespace) -> int:
    from mneme.memory.workspace import list_workspace

    db = _open_notes_db(args.notes_db)
    try:
        files = list_workspace(db, args.scope, prefix=args.prefix)
    finally:
        db.close()
    payload = [f.to_dict() for f in files]
    if args.json:
        print(pretty_json(payload))
        return 0
    if not payload:
        print("(empty)")
        return 0
    for f in payload:
        print(f"{f['updated_at']}  {f['size']:>10}B  {f['rel_path']}")
    return 0


def cmd_workspace_read(args: argparse.Namespace) -> int:
    from mneme.memory.workspace import read_workspace

    db = _open_notes_db(args.notes_db)
    try:
        result = read_workspace(db, args.scope, args.path)
    finally:
        db.close()
    if args.json:
        print(pretty_json(result))
    else:
        sys.stdout.write(result["content"])
        if not result["binary"] and not result["content"].endswith("\n"):
            sys.stdout.write("\n")
    return 0


def cmd_workspace_write(args: argparse.Namespace) -> int:
    from mneme.memory.workspace import write_workspace

    content = _read_text_input(args.content, args.content_file)
    db = _open_notes_db(args.notes_db)
    try:
        info = write_workspace(db, args.scope, args.path, content)
    finally:
        db.close()
    if args.json:
        print(pretty_json(info.to_dict()))
    else:
        print(f"wrote {info.rel_path} ({info.size} bytes)")
    return 0


def cmd_workspace_rm(args: argparse.Namespace) -> int:
    from mneme.memory.workspace import remove_workspace

    db = _open_notes_db(args.notes_db)
    try:
        removed = remove_workspace(db, args.scope, args.path)
    finally:
        db.close()
    if args.json:
        print(pretty_json({"removed": removed, "scope": args.scope, "path": args.path}))
    else:
        print("removed" if removed else "no such file")
    return 0 if removed else 1


if __name__ == "__main__":
    raise SystemExit(main())
