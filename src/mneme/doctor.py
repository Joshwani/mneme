"""Environment diagnostics for Mneme."""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from mneme.index.db import MnemeDB, default_db_path
from mneme.memory.db import default_notes_db_path


def _module_info(module_name: str, distribution: str | None = None) -> dict[str, Any]:
    info: dict[str, Any] = {"installed": False, "version": None}
    try:
        importlib.import_module(module_name)
        info["installed"] = True
    except Exception:
        info["installed"] = False
    try:
        info["version"] = importlib.metadata.version(distribution or module_name)
    except importlib.metadata.PackageNotFoundError:
        pass
    except Exception:
        pass
    return info


def _network_check() -> dict[str, Any]:
    """Best-effort, no-side-effect check that outbound HTTPS reaches a stable host."""

    try:
        import httpx
    except Exception as exc:
        return {"ok": False, "error": f"httpx not importable: {exc}"}

    url = "https://api.apis.guru/v2/list.json"
    try:
        with httpx.Client(timeout=4.0, follow_redirects=True) as client:
            response = client.head(url)
        return {
            "ok": response.status_code < 500,
            "checked": url,
            "status": response.status_code,
        }
    except Exception as exc:
        return {"ok": False, "checked": url, "error": str(exc)}


def collect(db_path: str | None = None) -> dict[str, Any]:
    """Collect a dict of diagnostics. Safe to call without a DB."""

    resolved_db = db_path or default_db_path()
    db_file = Path(resolved_db).expanduser()
    db_exists = db_file.exists()
    db_size = db_file.stat().st_size if db_exists else None

    stats: dict[str, Any] = {}
    db_open_error: str | None = None
    if db_exists:
        try:
            db = MnemeDB(resolved_db)
            try:
                stats = db.stats()
            finally:
                db.close()
        except Exception as exc:
            db_open_error = str(exc)

    notes_db_path = Path(default_notes_db_path()).expanduser()
    notes_db_exists = notes_db_path.exists()
    notes_db_size = notes_db_path.stat().st_size if notes_db_exists else None

    return {
        "mneme": _module_info("mneme", "mneme-server"),
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "implementation": platform.python_implementation(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "cli_on_path": shutil.which("mneme") or None,
        "env": {
            "MNEME_DB": os.environ.get("MNEME_DB"),
            "MNEME_NOTES_DB": os.environ.get("MNEME_NOTES_DB"),
            "MNEME_WORKSPACE_ROOT": os.environ.get("MNEME_WORKSPACE_ROOT"),
            "MNEME_AUTH_CONFIG": os.environ.get("MNEME_AUTH_CONFIG"),
            "MNEME_HTTP_ALLOW_HOSTS": os.environ.get("MNEME_HTTP_ALLOW_HOSTS"),
            "XDG_DATA_HOME": os.environ.get("XDG_DATA_HOME"),
        },
        "db": {
            "path": str(db_file),
            "exists": db_exists,
            "size_bytes": db_size,
            "stats": stats,
            "open_error": db_open_error,
        },
        "notes_db": {
            "path": str(notes_db_path),
            "exists": notes_db_exists,
            "size_bytes": notes_db_size,
        },
        "extras": {
            "mcp": _module_info("mcp"),
            "fastapi": _module_info("fastapi"),
            "uvicorn": _module_info("uvicorn"),
            "httpx": _module_info("httpx"),
            "pydantic": _module_info("pydantic"),
            "yaml": _module_info("yaml", "PyYAML"),
            "bs4": _module_info("bs4", "beautifulsoup4"),
        },
        "dev_tools": {
            "ruff": shutil.which("ruff") or None,
            "pytest": shutil.which("pytest") or None,
        },
        "network": _network_check(),
    }
