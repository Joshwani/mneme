# Changelog

All notable changes to this project are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- MCP tool `index_openapi_url` — fetch and index a remote OpenAPI spec URL (equivalent to `mneme add-spec`).

## [0.3.0] - unreleased

### Added
- **Library indexing.** Python and JavaScript/TypeScript library symbols can now be indexed alongside HTTP operations and surface in unified search.
  - `mneme add-pylib <package>` — index a Python package's public symbols via `griffe` (pure static analysis, no execution). Either an installed package or a local source directory (via `--source-dir`).
  - `mneme add-jslib --package <name> --file <path>.d.ts` — index a TypeScript declaration file via `tree-sitter-typescript`. Captures functions, classes (and their methods), interfaces, type aliases, and enums.
  - `mneme list-libraries [--language python|typescript]` — list indexed packages.
  - `mneme search-callables <query>` — unified search across HTTP operations and library symbols. Filterable by `--kind`, `--language`, `--package`, `--provider-domain`, `--method`.
  - MCP tools: `search_callables`, `get_library_symbol`, `list_libraries`. The existing `search_operations` continues to work HTTP-only.
  - New schema: `library_packages` table, `library_symbols` table, `library_symbols_fts` virtual table; existing `operations` schema is unchanged.
  - New optional extras: `mneme-server[pylib]` (griffe), `mneme-server[jslib]` (tree-sitter), `mneme-server[libraries]` (both).
- **Persistent agent memory.** Two new primitives stored in a separate `notes.db`:
  - **Notebook.** A SQLite + FTS5-backed scratch pad. CLI: `mneme notes-add | notes-search | notes-get | notes-list | notes-update | notes-delete`. MCP tools: `notes_add`, `notes_search`, `notes_get`, `notes_list`, `notes_update`, `notes_delete`. Microsecond-resolution timestamps for stable ordering. Notes support free-text scope and tag filtering.
  - **Scoped file workspace.** Opt-in, per-scope filesystem area for snippets and small artifacts. CLI: `mneme workspace-{status,enable,disable,ls,read,write,rm}`. MCP tools: `workspace_status`, `workspace_ls`, `workspace_read`, `workspace_write`, `workspace_rm`. Disabled by default; the agent cannot create scopes via MCP. Path traversal, symlink, per-file, and per-scope quota safety invariants enforced in code and tested.
- New env vars: `MNEME_NOTES_DB`, `MNEME_WORKSPACE_ROOT`.
- `doctor` now reports the notes DB path and size.

### Changed
- **Renamed the project from "OAS Atlas" to "Mneme".** Mneme is the Greek muse of memory; the new name better fits the broader scope (OpenAPI operations, library symbols, and persistent agent memory).
- FTS5 queries now use prefix matching (`"term"*`), so a search for `greet` also finds `greeting` and `greetings`. Strict increase in recall; no impact on previously matching queries.
- Search results now include a `kind` field (`http_operation`, `pylib_symbol`, or `jslib_symbol`) and a `callable_id` alias alongside the legacy `operation_id` for HTTP operations.
  - Python package: `oas-atlas` -> `mneme-server` (the PyPI distribution name is qualified because the bare `mneme` slug is held by an abandoned 2014 package).
  - Python import: `oas_atlas` -> `mneme`.
  - CLI binary: `oas-atlas` -> `mneme`.
  - Docker image: `ghcr.io/joshwani/oas-atlas` -> `ghcr.io/joshwani/mneme`.
  - Environment variables: `OAS_ATLAS_DB` -> `MNEME_DB`, `OAS_ATLAS_AUTH_CONFIG` -> `MNEME_AUTH_CONFIG`, `OAS_ATLAS_HTTP_ALLOW_HOSTS` -> `MNEME_HTTP_ALLOW_HOSTS`.
  - Default DB path: `~/.local/share/oas-atlas/oas_atlas.db` -> `~/.local/share/mneme/mneme.db` (and the XDG/Windows equivalents).
  - Default auth config path: `~/.config/oas-atlas/auth.json` -> `~/.config/mneme/auth.json`.
  - MCP tool `atlas_stats` -> `mneme_stats`.

### Migration notes
- There is no backward-compatibility shim. Update env vars and paths before upgrading.
- Existing SQLite indexes are forward-compatible. To keep using an existing index, point `MNEME_DB` at the old file or move it to the new default location.

## [0.2.0] - 2026-05-24

Initial public release (under the prior "OAS Atlas" name).

### Added
- Conservative OpenAPI discovery crawler for a domain (well-known catalog, RFC 9727 Link headers, `apis.json`, common OpenAPI/Swagger paths, common docs pages).
- Direct ingestion for OpenAPI/Swagger URLs and local files.
- APIs.guru bulk ingestion with a small default limit.
- Operation-level normalization into compact agent-facing cards.
- SQLite + FTS5 full-text operation search with optional filters and token budgeting.
- FastAPI search service.
- Local MCP server exposing search, spec retrieval, call templates, auth-aware request preparation, and guarded HTTP execution.
- Auth profiles with environment-variable secrets and per-profile method/host allowlists.
- One-command `demo` to index the bundled example spec and run a sample search.
- `mcp-config` to print ready-to-paste MCP client configurations for Cursor, Claude Desktop, Continue, and a generic stdio client.
- `doctor` for environment diagnostics.
- Curated `examples/seeds.popular.txt` with public OpenAPI documents for common developer APIs.
- XDG-aware default database path.
- Docker Compose, systemd, cron, and GitHub Actions deployment examples.

[Unreleased]: https://github.com/Joshwani/mneme/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Joshwani/mneme/releases/tag/v0.3.0
[0.2.0]: https://github.com/Joshwani/mneme/releases/tag/v0.2.0
