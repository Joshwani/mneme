# Mneme

[![CI](https://github.com/Joshwani/mneme/actions/workflows/test.yml/badge.svg)](https://github.com/Joshwani/mneme/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/mneme-server.svg)](https://pypi.org/project/mneme-server/)
[![Python](https://img.shields.io/pypi/pyversions/mneme-server.svg)](https://pypi.org/project/mneme-server/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![GHCR](https://img.shields.io/badge/ghcr.io-joshwani%2Fmneme-2b5797)](https://github.com/Joshwani/mneme/pkgs/container/mneme)

**A self-hosted catalog of callables and persistent memory for AI agents.**

Mneme is a small, local-first indexer and search service that an LLM agent can consult to find the right HTTP API operation, library symbol, or saved note — and then retrieve a minimal slice instead of dumping everything into context. The package is published on PyPI as [`mneme-server`](https://pypi.org/project/mneme-server/); the CLI is `mneme`.

The searchable unit is **a callable** — not "an API" or "a library." That can be:

- an OpenAPI operation (e.g., `POST /v1/refunds`),
- a Python library symbol (e.g., `matplotlib.pyplot.bar`),
- a JavaScript/TypeScript library symbol (e.g., `axios.create`),
- a saved note in the agent's persistent memory.

All four live in the same SQLite + FTS5 index and are searchable from one MCP tool (`search_callables`).

## Quickstart in 60 seconds

```bash
pip install mneme-server
mneme demo                            # indexes a bundled spec, runs a search
mneme mcp-config --client cursor      # prints ready-to-paste MCP config
mneme doctor                          # environment diagnostics
```

The default index lives in a per-user directory (XDG-aware), so subsequent commands work without `--db`.

> Want to see it on real APIs? `mneme crawl-seeds examples/seeds.popular.txt` will pull in GitHub, Stripe, Slack, DigitalOcean, Twilio, and others.

## Why callable-level search?

An agent rarely needs the entire Stripe, GitHub, or Slack API in context. It needs to find callables that match a task:

- `POST /repos/{owner}/{repo}/issues`
- `POST /v1/refunds`
- `matplotlib.pyplot.errorbar`  *(library symbols, coming soon)*
- `the note I saved about our auth flow`  *(memory, coming soon)*

Mneme indexes each callable with a compact agent-facing summary, required inputs, auth metadata, response fields (for HTTP), signatures/docstrings (for library symbols), provenance, and a minimal usage slice. Search returns a small list; the agent then pulls only the slice it actually needs.

## Architecture

```mermaid
flowchart LR
  seeds[seeds.txt or seeds.popular.txt] --> crawler
  url[direct spec URL] --> crawler
  file[local OpenAPI file] --> normalize
  guru[APIs.guru] --> normalize
  pypi[PyPI wheel] --> symbols
  npmtarball[npm tarball / .d.ts] --> symbols
  crawler[discover + fetch] --> normalize
  normalize[normalize operations] --> sqlite[(SQLite + FTS5)]
  symbols[normalize library symbols] --> sqlite
  notes[agent notes / workspace] --> notesdb[(notes.db)]
  sqlite --> httpapi[FastAPI search service]
  sqlite --> mcp[Local MCP server]
  notesdb --> mcp
  httpapi --> agent[Agents / clients]
  mcp --> agent
```

## What you get today (0.2.0)

- Conservative OpenAPI discovery crawler for a domain.
- Direct ingestion for OpenAPI/Swagger URLs and local files.
- APIs.guru bulk ingestion for bootstrapping a public corpus.
- A normalizer that converts each HTTP method/path into a compact operation card.
- SQLite + FTS5 operation search (no Postgres, no vector DB required).
- A FastAPI search service for agents.
- A local MCP server with search, spec retrieval, auth-aware request preparation, and guarded HTTP execution.
- Docker Compose, systemd, cron, and GitHub Actions examples.

## New in 0.3

- **Agent memory.** A searchable notebook (FTS5-backed) plus an opt-in scoped file workspace for persisting notes, snippets, and small artifacts across MCP sessions. Stored in a separate `notes.db`. See [Memory](#memory-notebook--workspace) below.
- **Library indexing.** `mneme add-pylib <package>` ingests a Python package's public symbols via `griffe` (static analysis, no execution). `mneme add-jslib --package <name> --file <path>.d.ts` ingests a TypeScript declaration file via tree-sitter. Library symbols are searchable side-by-side with HTTP operations via the unified `search_callables` tool. See [Library indexing](#library-indexing-python--jsts) below.

## What's coming next

- Library indexing from npm tarballs (right now you supply a local `.d.ts` file).
- Library indexing from PyPI sdists/wheels (right now the package must already be installed or be a local source directory).
- Hybrid retrieval (BM25 + embeddings) for callable search.

## Install

From source while the project is pre-PyPI:

```bash
git clone https://github.com/Joshwani/mneme.git
cd mneme
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
# include MCP support when you want the local MCP server
python -m pip install -e '.[dev,mcp]'
```

Once on PyPI:

```bash
pip install mneme-server         # base
pip install 'mneme-server[mcp]'  # with MCP server
```

## CLI cheatsheet

```bash
# one-command demo
mneme demo

# index sources
mneme add-file examples/specs/todo.yaml
mneme add-spec https://example.com/openapi.yaml
mneme discover example.com --ingest
mneme crawl-seeds examples/seeds.popular.txt
mneme ingest-apis-guru --limit 25

# search
mneme search "create a todo with a due date"
mneme search "create refund" --method POST --provider-domain api.stripe.com

# inspect
mneme stats
mneme doctor

# memory: notebook
mneme notes-add --title "T" --body "B" --tag x
mneme notes-search "query"
mneme notes-list

# memory: scoped file workspace (must enable first)
mneme workspace-enable --scope notes --max-mb 10
mneme workspace-write --scope notes --path a.md --content "..."
mneme workspace-ls --scope notes

# library indexing
mneme add-pylib httpx                             # an installed package
mneme add-pylib mymod --source-dir ./src          # a local source tree
mneme add-jslib --package axios --file ./axios.d.ts
mneme list-libraries
mneme search-callables "create a request"
mneme search-callables "send" --kind pylib_symbol --kind jslib_symbol

# serve / run as MCP
mneme serve --host 127.0.0.1 --port 8080
mneme mcp-server
mneme mcp-config --client cursor
```

All commands accept `--db <path>` to override the per-user default.

## Popular APIs starter pack

`examples/seeds.popular.txt` is a curated list of stable, public OpenAPI documents you can ingest in one shot:

```bash
mneme crawl-seeds examples/seeds.popular.txt
mneme stats
```

To suggest an API for the starter pack, open an issue using the **Add a public API to the starter index** template.

## Local MCP server

Mneme runs as a local MCP server. API credentials and notes stay on the user's machine.

```bash
python -m pip install -e '.[mcp]'
mneme mcp-server                          # stdio (default)
mneme mcp-server --transport streamable-http
```

Tools exposed:

```text
# Unified callable search (HTTP + library symbols)
search_callables           # unified search; restrict via kinds=[...]
get_library_symbol         # full symbol card for a symbol_id
list_libraries             # list indexed library packages

# OpenAPI / HTTP
search_operations          # HTTP-only operation search
get_operation              # full normalized operation card
get_spec_slice             # minimal OpenAPI-style operation slice
get_call_template          # non-executing request template
list_local_auth_profiles   # local auth profiles without secrets
prepare_http_call          # redacted prepared request, no network traffic
execute_http_call          # dry-run by default; real calls require confirm=true
mneme_stats                # local index stats

# Memory: notebook
notes_search               # full-text search the agent's notebook
notes_get                  # fetch a note by ID
notes_list                 # list recent notes, optionally by scope/tag
notes_add                  # add a new note
notes_update               # update an existing note
notes_delete               # delete a note

# Memory: scoped file workspace (off by default)
workspace_status           # list enabled scopes and current usage
workspace_ls               # list files in a scope
workspace_read             # read a file in a scope
workspace_write            # write a file in a scope (must be enabled first)
workspace_rm               # remove a file in a scope
```

Print a paste-ready config for your client:

```bash
mneme mcp-config --client cursor      # ~/.cursor/mcp.json or repo .cursor/mcp.json
mneme mcp-config --client claude      # Claude Desktop
mneme mcp-config --client continue    # Continue.dev
mneme mcp-config --client generic     # bare mcpServers snippet
mneme mcp-config --client cursor --auth-config ~/.config/mneme/auth.json
```

## Local auth profiles

Auth profiles are JSON files mapping a friendly profile name to credentials stored in environment variables. Agents see profile names and redacted previews, never raw secrets.

Default path:

```text
~/.config/mneme/auth.json
```

Example:

```json
{
  "profiles": {
    "github": {
      "provider_domain": "api.github.com",
      "base_url": "https://api.github.com",
      "allow_methods": ["GET", "POST", "PATCH"],
      "auth": {
        "type": "bearer",
        "token_env": "GITHUB_TOKEN"
      }
    },
    "todo-local": {
      "provider_domain": "api.example.test",
      "base_url": "https://api.example.test",
      "allow_methods": ["GET", "POST"],
      "auth": {
        "type": "api_key",
        "in": "header",
        "name": "X-API-Key",
        "value_env": "TODO_API_KEY"
      }
    }
  }
}
```

List profiles without leaking secrets:

```bash
mneme auth-profiles --auth-config ~/.config/mneme/auth.json
```

Prepare a call without sending it:

```bash
mneme prepare-call op_... \
  --auth-config ~/.config/mneme/auth.json \
  --auth-profile todo-local \
  --json-body '{"title":"ship mcp"}'
```

Execute a real call only when explicitly confirmed:

```bash
mneme execute-call op_... \
  --auth-config ~/.config/mneme/auth.json \
  --auth-profile todo-local \
  --json-body '{"title":"ship mcp"}' \
  --send --confirm
```

Guardrails:

- dry-run is the default for the MCP and CLI executor;
- real HTTP execution requires `confirm=true` / `--confirm`;
- mutating unauthenticated calls are blocked;
- auth profiles can restrict allowed HTTP methods and hosts;
- `MNEME_HTTP_ALLOW_HOSTS` can globally restrict execution hosts.

## HTTP Search API

### `POST /search`

```json
{
  "query": "create a refund for a previous payment",
  "limit": 10,
  "filters": {
    "method": "POST",
    "provider_domain": null,
    "auth_required": null
  },
  "token_budget": 4000
}
```

Response:

```json
{
  "query": "create a refund for a previous payment",
  "results": [
    {
      "operation_id": "op_...",
      "score": 1.06,
      "api_title": "Example Payments API",
      "provider_domain": "api.example.com",
      "method": "POST",
      "path": "/v1/refunds",
      "summary": "Create a refund",
      "why_relevant": "Creates a full or partial refund for an existing payment.",
      "auth_required": true,
      "required_inputs": ["payment_id", "amount"],
      "links": {
        "operation": "/operations/op_...",
        "spec_slice": "/operations/op_.../spec-slice",
        "call_template": "/operations/op_.../call-template"
      }
    }
  ]
}
```

### Other endpoints

```text
GET  /health
GET  /stats
GET  /search?q=create%20todo
GET  /operations/{operation_id}
GET  /operations/{operation_id}/spec-slice
GET  /operations/{operation_id}/call-template
POST /operations/{operation_id}/prepare-call
POST /operations/{operation_id}/execute-call
```

## Library indexing (Python + JS/TS)

Mneme can index callables that aren't HTTP operations. Library symbols (functions, classes, methods, interfaces, type aliases, enums) live in the same SQLite + FTS5 index and surface in `search_callables` results with `kind="pylib_symbol"` or `kind="jslib_symbol"`.

### Python via `griffe`

```bash
pip install 'mneme-server[pylib]'

# Index an installed package
mneme add-pylib httpx

# Index a checked-out source tree
mneme add-pylib mymod --source-dir ./src

mneme list-libraries
mneme search-callables "create a request" --language python
```

Implementation notes:

- We use [`griffe`](https://mkdocstrings.github.io/griffe/) for **static analysis** — Mneme never imports or executes user code.
- The package must be installed in the current Python environment, or a `--source-dir` must be provided.
- Private names (`_foo`, `_Bar`) and dunder names (`__init__`, `__repr__`) are skipped.

### JavaScript / TypeScript via `.d.ts`

```bash
pip install 'mneme-server[jslib]'

mneme add-jslib --package axios --file ./node_modules/axios/index.d.ts
mneme add-jslib --package @types/node --file ./node_modules/@types/node/fs.d.ts
mneme search-callables "axios.create"
```

Implementation notes:

- We use `tree-sitter-typescript` to parse `.d.ts` files. No `node`/`npm` is required at runtime.
- Currently you supply a local `.d.ts` file. Pulling tarballs from npm registry is a follow-up.
- Captured kinds: `function`, `class` (+ its `method`s), `interface`, `type`, `enum`.
- JSDoc comments immediately preceding a declaration are captured as the docstring; `@param`/`@returns` tags are preserved in the description but not yet parsed structurally.

### Unified search

`search_callables` returns a mixed list of hits ranked by BM25 with structural bonuses. Each hit has a `kind` field. Common filters:

```bash
mneme search-callables "create a refund"                         # all kinds
mneme search-callables "create a refund" --kind http_operation   # HTTP only
mneme search-callables "axios" --kind jslib_symbol               # JS/TS only
mneme search-callables "DataFrame" --language python             # Python lib only
mneme search-callables "post" --package httpx                    # one Python pkg
```

## Memory: notebook + workspace

Mneme exposes two memory primitives to an agent. Both live in a separate `notes.db` so you can back up, sync, or wipe your agent memory without touching the API catalog.

### Notebook

A persistent, FTS5-searchable scratch pad. The agent (or you) saves short notes — design decisions, gotchas, "here's the call that works." Later, the agent searches its own notebook to recall context.

```bash
mneme notes-add --title "Stripe refund flow" \
  --body "POST /v1/refunds needs payment_id, amount. 25h refund window." \
  --tag stripe --tag payments

mneme notes-search refund
mneme notes-list --scope finops
mneme notes-get note_<id>
mneme notes-update note_<id> --body "New body"
mneme notes-delete note_<id>
```

Notes have optional `tags`, an optional `scope` (free-text grouping like a project name or task ID), and microsecond-resolution timestamps for stable ordering.

### Scoped file workspace (off by default)

A small, opt-in directory the agent can read and write within. Useful for snippets, generated config, scratch artifacts that should persist across MCP sessions. **The workspace is OFF until you explicitly enable a scope**, and the agent cannot create new scopes via MCP.

```bash
mneme workspace-enable --scope notes --max-mb 10
mneme workspace-write --scope notes --path daily.md --content "## 2026-05-24"
mneme workspace-ls --scope notes
mneme workspace-read --scope notes --path daily.md
mneme workspace-rm --scope notes --path daily.md
mneme workspace-disable --scope notes              # keeps files on disk
mneme workspace-disable --scope notes --remove-files
```

Safety invariants:

- scopes must match `[a-zA-Z0-9_][a-zA-Z0-9_.\-]{0,63}`;
- paths cannot escape the scope directory (`..` segments and symlinks are rejected);
- per-file size limit (1 MiB default);
- per-scope quota (10 MiB default, configurable via `--max-mb`);
- the agent cannot enable or disable scopes through MCP — only the human operator can.

## Database location

Mneme picks sensible default paths so commands work without `--db`:

OpenAPI/library index:

1. `$MNEME_DB` if set
2. `$XDG_DATA_HOME/mneme/mneme.db` if set
3. `~/.local/share/mneme/mneme.db` on Linux/macOS
4. `%LOCALAPPDATA%\mneme\mneme.db` on Windows

Notes index (separate file):

1. `$MNEME_NOTES_DB` if set
2. `$XDG_DATA_HOME/mneme/notes.db` if set
3. `~/.local/share/mneme/notes.db` on Linux/macOS
4. `%LOCALAPPDATA%\mneme\notes.db` on Windows

Workspace root (one directory per enabled scope under it):

1. `$MNEME_WORKSPACE_ROOT` if set
2. `$XDG_DATA_HOME/mneme/workspace/` if set
3. `~/.local/share/mneme/workspace/` on Linux/macOS
4. `%LOCALAPPDATA%\mneme\workspace\` on Windows

Override the index path with `--db /path/to/mneme.db` and the notes index with `--notes-db /path/to/notes.db` on memory subcommands.

## Self-host with Docker Compose

```bash
cd deploy
docker compose up --build -d
```

Ingest the demo spec into the Docker volume:

```bash
docker compose run --rm mneme \
  mneme --db /data/mneme.db add-file /examples/specs/todo.yaml
```

```bash
curl -s -X POST http://127.0.0.1:8080/search \
  -H 'content-type: application/json' \
  -d '{"query":"create a todo","limit":3}' | jq
```

Pre-built container images are published to GHCR on tagged releases:

```bash
docker pull ghcr.io/joshwani/mneme:latest
```

## Self-host crawler deployment

For now, the recommended deployment model is bring-your-own-infra:

1. Run the API container or systemd service.
2. Store the SQLite index on a persistent volume.
3. Keep a curated `seeds.txt` file of domains and spec URLs.
4. Run `mneme crawl-seeds` from cron or another scheduler.
5. Back up the SQLite file like any other application data.

Example cron entry:

```cron
10 2 * * * cd /opt/mneme && /opt/mneme/.venv/bin/mneme --db /var/lib/mneme/mneme.db crawl-seeds /etc/mneme/seeds.txt >> /var/log/mneme-crawl.log 2>&1
```

## Troubleshooting

Run `mneme doctor` first. It prints the resolved DB path, index size, installed extras, and a network reachability check. Most reports should include its output.

Common issues:

- **"operation not found" or empty search results.** The index is empty. Run `mneme demo` or `mneme crawl-seeds examples/seeds.popular.txt`.
- **MCP server fails to start with an ImportError.** The optional MCP extra isn't installed. Run `python -m pip install -e '.[mcp]'` (or `pip install 'mneme-server[mcp]'`).
- **`mneme mcp-config --client cursor` shows `command: mneme` instead of an absolute path.** The `mneme` binary isn't on PATH in the shell that launches your MCP client. Activate the venv first or edit `command` to the absolute path printed by `which mneme`.
- **401/403 when executing a call.** Check `mneme auth-profiles --auth-config ~/.config/mneme/auth.json` and confirm the referenced `*_env` environment variable is set in the launching shell.
- **"host not allowed" errors when executing.** Either widen `allow_methods` / `allowed_hosts` in your profile, or unset/relax `MNEME_HTTP_ALLOW_HOSTS`.

## When to move beyond SQLite

SQLite + FTS5 is enough for the MVP and for private/team indexes. Move to a larger architecture when you need:

- concurrent crawler workers,
- millions of callables,
- vector retrieval,
- public multi-tenant search,
- owner verification workflows,
- moderation/takedown flows,
- crawl queues and retry policies.

A later hosted architecture could use:

- object storage for raw specs,
- Postgres for metadata,
- Tantivy / Meilisearch / OpenSearch for lexical search,
- pgvector / Qdrant / LanceDB for embeddings,
- a queue for crawler jobs,
- a read-only search API for agents.

## Crawl policy

The MVP is intentionally conservative. It should index intentionally published API descriptions, not private or accidentally exposed internal specs.

Recommended rules for operators:

- crawl only submitted domains, submitted URLs, known public directories, and API discovery endpoints;
- respect rate limits and robots/policy pages where applicable;
- do not index specs requiring authentication;
- store provenance for every spec;
- provide opt-out or takedown instructions if operating a public index;
- rank owner-verified specs above community/crawler-discovered specs.

## Development

```bash
python -m pip install -e '.[dev,mcp]'
ruff check
ruff format --check
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for details and [CHANGELOG.md](CHANGELOG.md) for release notes.

## Roadmap

- Agent memory: searchable notebook + opt-in scoped file workspace (0.3).
- Library indexing: Python (via `griffe`) and JS/TS (via `.d.ts`) (0.3).
- Owner-verified submissions via DNS TXT or GitHub repo verification.
- Better RFC 9727 Linkset parsing.
- Duplicate clustering by callable similarity.
- Hybrid lexical + embedding retrieval.
- Reranking with task/callable labels.
- Callable graph edges for multi-step workflows.
- Public benchmark: natural-language task to expected callable IDs.

## License

[Apache License 2.0](LICENSE).
