# Mneme Desktop

Electron + React + TypeScript companion for the local Mneme server.

## Development

The desktop app uses the repository-local `.venv`; it never installs into the
global Python environment.

```bash
# from the repository root
python -m venv .venv
.venv/bin/python -m pip install -e .

cd desktop
npm install
npm run dev
```

Set `MNEME_DESKTOP_PYTHON=/absolute/path/to/python` to use another explicitly
managed Python environment. The selected interpreter must have `mneme-server`
installed.

## Checks and packaging

```bash
npm run check
npm run build       # builds a native Python sidecar, then packages Electron
npm run make        # creates the platform installer/archive
npm run publish     # creates a draft GitHub release (requires GITHUB_TOKEN)
```

`scripts/build-sidecar.mjs` creates `desktop/.sidecar-venv`, installs the
repository and PyInstaller there, and emits
`resources/sidecar/mneme-sidecar[.exe]`. No global `pip` state is changed.
Electron Forge copies that executable into the application's resources. Set
`MNEME_SIDECAR_PYTHON` if the build should bootstrap from a particular Python.

Sidecars and installers are platform-specific. Build each release artifact on
its target operating system. Packaged macOS and Windows builds check the
project's public GitHub releases through Electron's update service. Publish
signed artifacts before promoting a draft release; macOS auto-update requires
code signing.

## Architecture

- The renderer is an unprivileged web page. Its `BrowserWindow` has
  `contextIsolation`, `sandbox`, and `webSecurity` enabled and Node integration
  disabled.
- The preload exposes only typed operations for overview, catalog, source
  management, server restart, and credential CRUD. There is no arbitrary HTTP,
  filesystem, shell, or process IPC.
- Main chooses an ephemeral `127.0.0.1` port, generates a 256-bit launch token,
  starts the Python process, waits for an authenticated diagnostics response,
  and terminates the child before the app exits.
- Main is the only HTTP client. It sets `MNEME_MANAGEMENT_TOKEN` on the child
  and adds `X-Mneme-Management-Token` to management requests (plus a bearer
  header for forward compatibility); neither the port token nor raw
  credential values are exposed to the renderer.
- Credential values are encrypted with Electron `safeStorage` (Keychain,
  DPAPI, or the Linux secret service) before being persisted under Electron's
  user-data directory. The renderer receives metadata only.
- Every stored secret gets a generated
  `MNEME_DESKTOP_SECRET_<RANDOM_ID>` environment name. Decrypted values are
  injected only into a newly launched sidecar. Creating, rotating, or deleting
  a secret restarts the sidecar.

## Backend contract

The API client accepts common snake_case/camelCase response fields. Mneme
protects management routes with `MNEME_MANAGEMENT_TOKEN`:

- Existing: `GET /health`, `POST /search`, `GET /operations/{id}`,
  `GET /operations/{id}/spec-slice`
- Management: `GET /version`, `GET /specs`, `GET /specs/{id}`,
  `POST /specs/ingest-url`, `POST /specs/ingest-file`, `POST /specs/discover`,
  `DELETE /specs/{id}`, and `GET /operations`
- Optional: `GET /operations/{id}/docs` returning `markdown` or
  `documentation`; the UI falls back to the operation description when absent

The main-process adapter translates the renderer's source union into each
backend payload (`url`, `path`, or `domain`). Reindexing retrieves a spec's
source URL and repeats URL/file ingestion. Lists may be arrays or objects under
`sources`, `operations`, `items`, or `results`.

Auth profile policy remains Mneme's existing environment-reference model. The
desktop credential screen stores and rotates secret values; users place the
generated environment name in `token_env`, `api_key_env`, `username_env`, or
`password_env`. OAuth is intentionally out of scope.
