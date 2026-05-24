"""Python library ingestion via griffe.

We support two entry points:

1. ``ingest_python_package(db, package_name, version=None)``: load the package
   via griffe's static analysis (no execution). The package must be installed
   in the current Python environment OR locatable on sys.path. Most users will
   ``pip install`` the target package first, then run ``mneme add-pylib``.

2. ``ingest_python_distribution(db, package_name, source_dir)``: point griffe
   at a source directory directly (e.g., a checked-out repo).

We deliberately skip dynamic loading and never ``import`` user code. Static
analysis is enough for the searchable surface (signatures, docstrings).
"""

from __future__ import annotations

import importlib.metadata
import os
import sys
from pathlib import Path
from typing import Any, Iterable

from mneme.index.db import MnemeDB
from mneme.util import stable_id, utc_now_iso


class PythonIngestError(RuntimeError):
    """Raised when a Python library cannot be loaded or normalized."""


def _resolve_version(package_name: str, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _resolve_homepage(package_name: str) -> tuple[str | None, str | None]:
    """Return (homepage_url, summary) for a package or (None, None)."""

    try:
        meta = importlib.metadata.metadata(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None, None
    homepage = meta.get("Home-page")
    if not homepage:
        for value in meta.get_all("Project-URL") or []:
            if isinstance(value, str) and ", " in value:
                label, url = value.split(", ", 1)
                if label.lower() in {"homepage", "documentation", "source"}:
                    homepage = url
                    break
    return homepage, meta.get("Summary")


def _ensure_griffe():
    try:
        import griffe  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without optional extra.
        raise PythonIngestError(
            "Python library indexing requires griffe. Install with: "
            "python -m pip install 'mneme-server[pylib]'"
        ) from exc
    return importlib.import_module("griffe")


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _is_private(name: str) -> bool:
    return name.startswith("_") and not _is_dunder(name)


def _join_qual(module_path: str, parts: list[str]) -> str:
    return ".".join([module_path, *parts]) if parts else module_path


def _format_parameters(parameters) -> tuple[list[dict[str, Any]], str]:
    """Return (structured params list, single-line signature suffix)."""

    rendered: list[str] = []
    structured: list[dict[str, Any]] = []
    for p in parameters or []:
        name = p.name
        kind = str(getattr(p, "kind", "") or "")
        annotation = ""
        try:
            if getattr(p, "annotation", None) is not None:
                annotation = str(p.annotation)
        except Exception:
            annotation = ""
        default = ""
        try:
            if getattr(p, "default", None) is not None:
                default = str(p.default)
        except Exception:
            default = ""
        bit = name
        if kind == "ParameterKind.var_positional":
            bit = f"*{name}"
        elif kind == "ParameterKind.var_keyword":
            bit = f"**{name}"
        if annotation:
            bit += f": {annotation}"
        if default:
            bit += f" = {default}"
        rendered.append(bit)
        structured.append(
            {
                "name": name,
                "kind": kind.replace("ParameterKind.", "") or None,
                "annotation": annotation or None,
                "default": default or None,
                "required": not default
                and kind not in {"ParameterKind.var_positional", "ParameterKind.var_keyword"},
            }
        )
    return structured, ", ".join(rendered)


def _format_returns(obj) -> str | None:
    try:
        r = getattr(obj, "returns", None)
        if r is None:
            return None
        return str(r)
    except Exception:
        return None


def _docstring_text(obj) -> str:
    try:
        ds = getattr(obj, "docstring", None)
        if ds is None:
            return ""
        return (ds.value or "").strip()
    except Exception:
        return ""


def _docstring_summary(text: str) -> str:
    if not text:
        return ""
    first = text.split("\n\n", 1)[0]
    return first.strip().replace("\n", " ")[:400]


def _agent_text(card: dict[str, Any]) -> str:
    parts = [
        f"{card['language']} {card['kind']} {card['qualified_name']}",
    ]
    if card.get("signature"):
        parts.append(card["signature"])
    if card.get("summary"):
        parts.append(card["summary"])
    if card.get("description") and card["description"] != card.get("summary"):
        parts.append(card["description"][:2000])
    return "\n".join(parts)


def _normalize_function(
    *,
    package_id: str,
    package_name: str,
    obj,
    module_path: str,
    parent_qual: list[str],
    fetched_at: str,
) -> dict[str, Any]:
    structured, sig_args = _format_parameters(getattr(obj, "parameters", None))
    returns = _format_returns(obj)
    docstring = _docstring_text(obj)
    summary = _docstring_summary(docstring)

    is_method = bool(parent_qual)
    qualified_name = _join_qual(module_path, parent_qual + [obj.name])
    signature = f"{obj.name}({sig_args})"
    if returns:
        signature += f" -> {returns}"

    card = {
        "language": "python",
        "kind": "method" if is_method else "function",
        "package_id": package_id,
        "package_name": package_name,
        "module_path": module_path,
        "qualified_name": qualified_name,
        "symbol_name": obj.name,
        "signature": signature,
        "summary": summary,
        "description": docstring,
        "parameters": structured,
        "returns": {"annotation": returns} if returns else None,
        "tags": ["method"] if is_method else ["function"],
        "source_url": None,
        "quality_score": 0.5 + (0.1 if docstring else 0.0),
        "fetched_at": fetched_at,
    }
    card["agent_text"] = _agent_text(card)
    card["symbol_id"] = stable_id("sym", f"{package_id}|{qualified_name}|{signature}", length=20)
    return card


def _normalize_class(
    *,
    package_id: str,
    package_name: str,
    obj,
    module_path: str,
    parent_qual: list[str],
    fetched_at: str,
) -> dict[str, Any]:
    docstring = _docstring_text(obj)
    summary = _docstring_summary(docstring)
    qualified_name = _join_qual(module_path, parent_qual + [obj.name])

    card = {
        "language": "python",
        "kind": "class",
        "package_id": package_id,
        "package_name": package_name,
        "module_path": module_path,
        "qualified_name": qualified_name,
        "symbol_name": obj.name,
        "signature": f"class {obj.name}",
        "summary": summary,
        "description": docstring,
        "parameters": [],
        "returns": None,
        "tags": ["class"],
        "source_url": None,
        "quality_score": 0.6 + (0.1 if docstring else 0.0),
        "fetched_at": fetched_at,
    }
    card["agent_text"] = _agent_text(card)
    card["symbol_id"] = stable_id("sym", f"{package_id}|{qualified_name}|class", length=20)
    return card


def _walk_module(
    *,
    package_id: str,
    package_name: str,
    module,
    module_path: str,
    parent_qual: list[str],
    fetched_at: str,
) -> Iterable[dict[str, Any]]:
    members = getattr(module, "members", None) or {}
    for name, member in members.items():
        if _is_private(name):
            continue
        if _is_dunder(name):
            continue
        kind = str(getattr(member, "kind", "") or "").lower()
        if "function" in kind:
            yield _normalize_function(
                package_id=package_id,
                package_name=package_name,
                obj=member,
                module_path=module_path,
                parent_qual=parent_qual,
                fetched_at=fetched_at,
            )
        elif "class" in kind:
            yield _normalize_class(
                package_id=package_id,
                package_name=package_name,
                obj=member,
                module_path=module_path,
                parent_qual=parent_qual,
                fetched_at=fetched_at,
            )
            # Walk methods inside the class.
            yield from _walk_module(
                package_id=package_id,
                package_name=package_name,
                module=member,
                module_path=module_path,
                parent_qual=parent_qual + [member.name],
                fetched_at=fetched_at,
            )
        elif "module" in kind:
            child_module_path = f"{module_path}.{member.name}" if module_path else member.name
            yield from _walk_module(
                package_id=package_id,
                package_name=package_name,
                module=member,
                module_path=child_module_path,
                parent_qual=parent_qual,
                fetched_at=fetched_at,
            )


def _load_griffe(package_name: str, source_dir: str | None):
    griffe = _ensure_griffe()
    try:
        if source_dir:
            return griffe.load(
                package_name, search_paths=[str(Path(source_dir).expanduser().resolve())]
            )
        return griffe.load(package_name)
    except Exception as exc:
        raise PythonIngestError(f"failed to load {package_name!r} via griffe: {exc}") from exc


def ingest_python_package(
    db: MnemeDB,
    package_name: str,
    *,
    version: str | None = None,
) -> dict[str, Any]:
    """Index a Python package that is importable in the current environment."""

    return _ingest(db, package_name, version=version, source_dir=None, source="installed")


def ingest_python_distribution(
    db: MnemeDB,
    package_name: str,
    source_dir: str | os.PathLike[str],
    *,
    version: str | None = None,
) -> dict[str, Any]:
    """Index a Python package from a local source directory."""

    return _ingest(db, package_name, version=version, source_dir=str(source_dir), source="source")


def _ingest(
    db: MnemeDB,
    package_name: str,
    *,
    version: str | None,
    source_dir: str | None,
    source: str,
) -> dict[str, Any]:
    if not package_name or not package_name.strip():
        raise PythonIngestError("package name is required")
    package_name = package_name.strip()

    fetched_at = utc_now_iso()
    resolved_version = _resolve_version(package_name, version)
    homepage, summary = (None, None)
    if not source_dir:
        homepage, summary = _resolve_homepage(package_name)

    # Allow installed-site-packages discovery when running in a venv that
    # exposes a different sys.path than the caller's PWD.
    if not source_dir and not any(p == "" for p in sys.path):
        sys.path.insert(0, "")

    root = _load_griffe(package_name, source_dir)
    package_id = stable_id("pkg", f"python|{package_name}|{resolved_version or ''}", length=16)

    symbols = list(
        _walk_module(
            package_id=package_id,
            package_name=package_name,
            module=root,
            module_path=package_name,
            parent_qual=[],
            fetched_at=fetched_at,
        )
    )

    db.upsert_library_package(
        package={
            "package_id": package_id,
            "language": "python",
            "name": package_name,
            "version": resolved_version,
            "source": source,
            "source_url": None,
            "homepage": homepage,
            "summary": summary,
            "fetched_at": fetched_at,
            "content_hash": None,
        }
    )
    count = db.replace_library_symbols(package_id=package_id, symbols=symbols)

    return {
        "package_id": package_id,
        "language": "python",
        "name": package_name,
        "version": resolved_version,
        "source": source,
        "symbols": count,
    }
