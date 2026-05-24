# Contributing to Mneme

Thanks for considering a contribution. Mneme is intentionally small and self-hostable, and the goal is to keep the surface area tight and the developer experience first-class.

## Quick start

```bash
git clone https://github.com/Joshwani/mneme.git
cd mneme
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,mcp]'
pytest
ruff check
ruff format --check
```

If `pytest` is green and `ruff` is clean, you're set up correctly.

## Project layout

```
src/mneme/            # library + CLI + MCP server
  api/                # FastAPI search service
  crawl/              # domain discovery, seed handling
  index/              # SQLite + FTS5 storage and search
  normalize/          # OpenAPI -> operation card normalization
  http_client.py      # prepare/execute call with auth profiles
  mcp_server.py       # local MCP server
  cli.py              # argparse entry point
tests/                # pytest suite
examples/             # sample specs, seeds, auth example
deploy/               # docker-compose, systemd, cron examples
.github/workflows/    # CI
```

## Making changes

1. Open an issue first for non-trivial changes so we can agree on scope.
2. Branch from `main`. Use a descriptive branch name (`fix/discovery-link-header`, `feat/mcp-config-helper`, etc.).
3. Keep PRs small and focused. One feature or one fix per PR is ideal.
4. Add or update tests when you change behavior. New CLI subcommands should at minimum have a smoke test.
5. Run `ruff check`, `ruff format --check`, and `pytest` locally before opening the PR.

## Commit messages

We loosely follow Conventional Commits. The prefix is informative, not enforced:

- `feat:` new user-visible capability
- `fix:` bug fix
- `docs:` README, CONTRIBUTING, examples
- `chore:` repo plumbing, CI, dependencies
- `refactor:` no behavior change
- `test:` test-only changes

Imperative mood, present tense, no trailing period.

## Adding a popular API to the starter seed list

`examples/seeds.popular.txt` is a curated list of public OpenAPI documents. To add one:

1. Confirm the spec URL is upstream-maintained or hosted on APIs.guru.
2. Add a comment with the source attribution and the canonical homepage.
3. Run `mneme crawl-seeds examples/seeds.popular.txt` locally and confirm it ingests cleanly.
4. Include the `mneme stats` output in the PR description.

## Crawl policy

Mneme is conservative on purpose. Please do not add features that scan domains beyond what is already in `crawl/discover.py`, and do not add specs that require authentication to fetch.

## Reporting security issues

Please do not file public issues for security problems. Open a private security advisory on GitHub instead.

## License

By contributing, you agree that your contributions are licensed under the Apache License 2.0, the same license as the project.
