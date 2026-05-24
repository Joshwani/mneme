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

    return parser


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

    run_mcp_server(db_path=args.db, auth_config=args.auth_config, transport=args.transport)
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


if __name__ == "__main__":
    raise SystemExit(main())
