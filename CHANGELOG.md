# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-05-24

Initial public release.

### Added
- Conservative OpenAPI discovery crawler for a domain (well-known catalog, RFC 9727 Link headers, `apis.json`, common OpenAPI/Swagger paths, common docs pages).
- Direct ingestion for OpenAPI/Swagger URLs and local files.
- APIs.guru bulk ingestion with a small default limit.
- Operation-level normalization into compact agent-facing cards (summary, required inputs, auth metadata, response fields, provenance, spec slice).
- SQLite + FTS5 full-text operation search with optional filters and token budgeting.
- FastAPI search service (`oas-atlas serve`).
- Local MCP server (`oas-atlas mcp-server`) exposing `search_operations`, `get_operation`, `get_spec_slice`, `get_call_template`, `list_local_auth_profiles`, `prepare_http_call`, `execute_http_call`, `atlas_stats`.
- Auth profiles with environment-variable secrets and per-profile method/host allowlists.
- Prepare-call (redacted) and execute-call (dry-run by default, requires `--send --confirm`) for both the CLI and the MCP server.
- One-command `oas-atlas demo` to index the bundled example spec and run a sample search.
- `oas-atlas mcp-config` to print ready-to-paste MCP client configurations for Cursor, Claude Desktop, Continue, and a generic stdio client.
- `oas-atlas doctor` for environment diagnostics.
- Curated `examples/seeds.popular.txt` with public OpenAPI documents for common developer APIs.
- XDG-aware default database path (respects `OAS_ATLAS_DB`, then `XDG_DATA_HOME`, then platform default).
- Docker Compose, systemd, cron, and GitHub Actions deployment examples.

[Unreleased]: https://github.com/Joshwani/oas-atlas/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Joshwani/oas-atlas/releases/tag/v0.2.0
