"""JavaScript/TypeScript library ingestion via tree-sitter.

We parse ``.d.ts`` declaration files (or ``.ts``) and extract exported
symbols: functions, classes (and their methods), interfaces, type aliases,
enums, and constants. JSDoc comments immediately preceding a declaration
are captured as the docstring.

This intentionally does no resolution or type-checking — it is a structural
extraction. That is sufficient for callable search; agents can fetch the
canonical type by querying the `qualified_name`.
"""

from __future__ import annotations

import importlib
import os
import re
from pathlib import Path
from typing import Any, Iterable

from mneme.index.db import MnemeDB
from mneme.util import stable_id, utc_now_iso


class JsTsIngestError(RuntimeError):
    """Raised when a JS/TS source cannot be parsed or normalized."""


def _ensure_tree_sitter():
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_typescript  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without optional extra.
        raise JsTsIngestError(
            "JavaScript/TypeScript indexing requires tree-sitter and tree-sitter-typescript. "
            "Install with: python -m pip install 'mneme-server[jslib]'"
        ) from exc
    return (
        importlib.import_module("tree_sitter"),
        importlib.import_module("tree_sitter_typescript"),
    )


_JSDOC_RE = re.compile(r"^/\*\*(.*?)\*/", re.DOTALL)
_JSDOC_LINE_RE = re.compile(r"^\s*\*\s?", re.MULTILINE)
_JSDOC_TAG_RE = re.compile(r"^@(\w+)\b", re.MULTILINE)


def _clean_jsdoc(text: str) -> str:
    match = _JSDOC_RE.match(text)
    if not match:
        # Allow plain // line comments as fallback.
        if text.lstrip().startswith("//"):
            return text.strip().lstrip("/").strip()
        return ""
    body = match.group(1)
    body = _JSDOC_LINE_RE.sub("", body)
    return body.strip()


def _doc_summary(text: str) -> str:
    if not text:
        return ""
    cleaned = _JSDOC_TAG_RE.sub("", text)
    first = cleaned.split("\n\n", 1)[0]
    return first.strip().replace("\n", " ")[:400]


def _text(src: bytes, node) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _find_leading_comment(src: bytes, node) -> str:
    """Return the cleaned doc-string of the JSDoc comment immediately preceding ``node``."""

    sibling = node.prev_named_sibling
    if sibling is None or sibling.type != "comment":
        return ""
    raw = _text(src, sibling)
    return _clean_jsdoc(raw)


def _child_by_field(node, field: str):
    return node.child_by_field_name(field)


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


def _make_card(
    *,
    package_id: str,
    package_name: str,
    module_path: str,
    kind: str,
    symbol_name: str,
    signature: str,
    description: str,
    fetched_at: str,
    parameters: list[dict[str, Any]] | None = None,
    returns: dict[str, Any] | None = None,
    parent_qual: list[str] | None = None,
    tags: list[str] | None = None,
    language: str = "typescript",
) -> dict[str, Any]:
    parent_qual = parent_qual or []
    qualified_name = ".".join([package_name, *parent_qual, symbol_name])
    summary = _doc_summary(description)
    card = {
        "language": language,
        "kind": kind,
        "package_id": package_id,
        "package_name": package_name,
        "module_path": module_path,
        "qualified_name": qualified_name,
        "symbol_name": symbol_name,
        "signature": signature,
        "summary": summary,
        "description": description,
        "parameters": parameters or [],
        "returns": returns,
        "tags": tags or [kind],
        "source_url": None,
        "quality_score": 0.5 + (0.1 if description else 0.0),
        "fetched_at": fetched_at,
    }
    card["agent_text"] = _agent_text(card)
    card["symbol_id"] = stable_id("sym", f"{package_id}|{qualified_name}|{signature}", length=20)
    return card


def _extract_function_params(src: bytes, params_node) -> tuple[list[dict[str, Any]], str]:
    if params_node is None:
        return [], "()"
    structured: list[dict[str, Any]] = []
    rendered_bits: list[str] = []
    for child in params_node.children:
        if child.type not in {
            "required_parameter",
            "optional_parameter",
            "rest_pattern",
        }:
            continue
        text = _text(src, child).strip()
        name_node = _child_by_field(child, "pattern") or child.child(0)
        name = _text(src, name_node).strip().lstrip(".") if name_node else ""
        ann_node = _child_by_field(child, "type")
        annotation = _text(src, ann_node).lstrip(":").strip() if ann_node else ""
        required = child.type == "required_parameter"
        structured.append(
            {
                "name": name,
                "annotation": annotation or None,
                "required": required,
            }
        )
        rendered_bits.append(text)
    return structured, "(" + ", ".join(rendered_bits) + ")"


def _extract_return_annotation(src: bytes, fn_node) -> tuple[dict[str, Any] | None, str]:
    ret_node = _child_by_field(fn_node, "return_type")
    if ret_node is None:
        return None, ""
    raw = _text(src, ret_node).lstrip(":").strip()
    if not raw:
        return None, ""
    return {"annotation": raw}, f": {raw}"


def _handle_function(
    src: bytes,
    node,
    *,
    package_id: str,
    package_name: str,
    module_path: str,
    description: str,
    fetched_at: str,
) -> dict[str, Any] | None:
    name_node = _child_by_field(node, "name") or node.child_by_field_name("name")
    if name_node is None:
        for child in node.children:
            if child.type in {"identifier", "property_identifier"}:
                name_node = child
                break
    if name_node is None:
        return None
    name = _text(src, name_node).strip()
    parameters, params_signature = _extract_function_params(
        src, _child_by_field(node, "parameters")
    )
    returns, returns_signature = _extract_return_annotation(src, node)
    signature = f"{name}{params_signature}{returns_signature}"
    return _make_card(
        package_id=package_id,
        package_name=package_name,
        module_path=module_path,
        kind="function",
        symbol_name=name,
        signature=signature,
        description=description,
        fetched_at=fetched_at,
        parameters=parameters,
        returns=returns,
    )


def _handle_method(
    src: bytes,
    node,
    *,
    package_id: str,
    package_name: str,
    module_path: str,
    parent_qual: list[str],
    description: str,
    fetched_at: str,
) -> dict[str, Any] | None:
    name_node = _child_by_field(node, "name")
    if name_node is None:
        for child in node.children:
            if child.type in {"property_identifier", "identifier"}:
                name_node = child
                break
    if name_node is None:
        return None
    name = _text(src, name_node).strip()
    parameters, params_signature = _extract_function_params(
        src, _child_by_field(node, "parameters")
    )
    returns, returns_signature = _extract_return_annotation(src, node)
    signature = f"{name}{params_signature}{returns_signature}"
    return _make_card(
        package_id=package_id,
        package_name=package_name,
        module_path=module_path,
        kind="method",
        symbol_name=name,
        signature=signature,
        description=description,
        fetched_at=fetched_at,
        parameters=parameters,
        returns=returns,
        parent_qual=parent_qual,
        tags=["method"],
    )


def _handle_class(
    src: bytes,
    node,
    *,
    package_id: str,
    package_name: str,
    module_path: str,
    description: str,
    fetched_at: str,
) -> Iterable[dict[str, Any]]:
    name_node = _child_by_field(node, "name")
    if name_node is None:
        for child in node.children:
            if child.type == "type_identifier":
                name_node = child
                break
    if name_node is None:
        return
    class_name = _text(src, name_node).strip()
    yield _make_card(
        package_id=package_id,
        package_name=package_name,
        module_path=module_path,
        kind="class",
        symbol_name=class_name,
        signature=f"class {class_name}",
        description=description,
        fetched_at=fetched_at,
        tags=["class"],
    )

    body = _child_by_field(node, "body")
    if body is None:
        return
    for child in body.children:
        if child.type in {"method_signature", "method_definition", "abstract_method_signature"}:
            method_doc = _find_leading_comment(src, child)
            card = _handle_method(
                src,
                child,
                package_id=package_id,
                package_name=package_name,
                module_path=module_path,
                parent_qual=[class_name],
                description=method_doc,
                fetched_at=fetched_at,
            )
            if card is not None:
                yield card


def _handle_interface(
    src: bytes,
    node,
    *,
    package_id: str,
    package_name: str,
    module_path: str,
    description: str,
    fetched_at: str,
) -> Iterable[dict[str, Any]]:
    name_node = _child_by_field(node, "name")
    if name_node is None:
        for child in node.children:
            if child.type == "type_identifier":
                name_node = child
                break
    if name_node is None:
        return
    iface_name = _text(src, name_node).strip()
    body_node = _child_by_field(node, "body")
    body_text = _text(src, body_node).strip() if body_node else "{}"
    signature = f"interface {iface_name} {body_text}"
    if len(signature) > 400:
        signature = signature[:397] + "..."
    yield _make_card(
        package_id=package_id,
        package_name=package_name,
        module_path=module_path,
        kind="interface",
        symbol_name=iface_name,
        signature=signature,
        description=description,
        fetched_at=fetched_at,
        tags=["interface"],
    )


def _handle_type_alias(
    src: bytes,
    node,
    *,
    package_id: str,
    package_name: str,
    module_path: str,
    description: str,
    fetched_at: str,
) -> dict[str, Any] | None:
    name_node = _child_by_field(node, "name")
    if name_node is None:
        return None
    name = _text(src, name_node).strip()
    value_node = _child_by_field(node, "value")
    body = _text(src, value_node).strip() if value_node else "unknown"
    signature = f"type {name} = {body}"
    if len(signature) > 400:
        signature = signature[:397] + "..."
    return _make_card(
        package_id=package_id,
        package_name=package_name,
        module_path=module_path,
        kind="type",
        symbol_name=name,
        signature=signature,
        description=description,
        fetched_at=fetched_at,
        tags=["type"],
    )


def _handle_enum(
    src: bytes,
    node,
    *,
    package_id: str,
    package_name: str,
    module_path: str,
    description: str,
    fetched_at: str,
) -> dict[str, Any] | None:
    name_node = _child_by_field(node, "name")
    if name_node is None:
        return None
    name = _text(src, name_node).strip()
    body_node = _child_by_field(node, "body")
    body = _text(src, body_node).strip() if body_node else "{}"
    signature = f"enum {name} {body}"
    if len(signature) > 400:
        signature = signature[:397] + "..."
    return _make_card(
        package_id=package_id,
        package_name=package_name,
        module_path=module_path,
        kind="enum",
        symbol_name=name,
        signature=signature,
        description=description,
        fetched_at=fetched_at,
        tags=["enum"],
    )


def _iter_declarations(node):
    """Yield (declaration_node, leading_doc_node_or_None) pairs from a program node."""

    for child in node.children:
        if child.type == "comment":
            continue
        if child.type == "export_statement":
            for inner in child.children:
                if inner.type == "ambient_declaration":
                    for nested in inner.children:
                        if nested.type != "declare":
                            yield child, nested
                elif inner.type in {
                    "function_signature",
                    "function_declaration",
                    "class_declaration",
                    "interface_declaration",
                    "type_alias_declaration",
                    "enum_declaration",
                    "variable_declaration",
                    "lexical_declaration",
                }:
                    yield child, inner
        elif child.type == "ambient_declaration":
            for nested in child.children:
                if nested.type != "declare":
                    yield child, nested
        elif child.type in {
            "function_declaration",
            "class_declaration",
            "interface_declaration",
            "type_alias_declaration",
            "enum_declaration",
        }:
            yield child, child


def _parse_typescript(src: bytes):
    ts, tt = _ensure_tree_sitter()
    lang = ts.Language(tt.language_typescript())
    parser = ts.Parser(lang)
    return parser.parse(src)


def ingest_dts_text(
    db: MnemeDB,
    *,
    package_name: str,
    source: str,
    version: str | None = None,
    module_path: str | None = None,
    source_url: str | None = None,
    homepage: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    """Index a single .d.ts (or .ts) source string for a package."""

    if not package_name or not package_name.strip():
        raise JsTsIngestError("package name is required")
    package_name = package_name.strip()
    module_path = module_path or package_name
    fetched_at = utc_now_iso()
    src = source.encode("utf-8")
    tree = _parse_typescript(src)

    package_id = stable_id("pkg", f"typescript|{package_name}|{version or ''}", length=16)
    symbols: list[dict[str, Any]] = []

    for outer, decl in _iter_declarations(tree.root_node):
        doc = _find_leading_comment(src, outer)
        if decl.type in {"function_signature", "function_declaration"}:
            card = _handle_function(
                src,
                decl,
                package_id=package_id,
                package_name=package_name,
                module_path=module_path,
                description=doc,
                fetched_at=fetched_at,
            )
            if card is not None:
                symbols.append(card)
        elif decl.type == "class_declaration":
            symbols.extend(
                _handle_class(
                    src,
                    decl,
                    package_id=package_id,
                    package_name=package_name,
                    module_path=module_path,
                    description=doc,
                    fetched_at=fetched_at,
                )
            )
        elif decl.type == "interface_declaration":
            symbols.extend(
                _handle_interface(
                    src,
                    decl,
                    package_id=package_id,
                    package_name=package_name,
                    module_path=module_path,
                    description=doc,
                    fetched_at=fetched_at,
                )
            )
        elif decl.type == "type_alias_declaration":
            card = _handle_type_alias(
                src,
                decl,
                package_id=package_id,
                package_name=package_name,
                module_path=module_path,
                description=doc,
                fetched_at=fetched_at,
            )
            if card is not None:
                symbols.append(card)
        elif decl.type == "enum_declaration":
            card = _handle_enum(
                src,
                decl,
                package_id=package_id,
                package_name=package_name,
                module_path=module_path,
                description=doc,
                fetched_at=fetched_at,
            )
            if card is not None:
                symbols.append(card)

    db.upsert_library_package(
        package={
            "package_id": package_id,
            "language": "typescript",
            "name": package_name,
            "version": version,
            "source": "dts",
            "source_url": source_url,
            "homepage": homepage,
            "summary": summary,
            "fetched_at": fetched_at,
            "content_hash": None,
        }
    )
    count = db.replace_library_symbols(package_id=package_id, symbols=symbols)
    return {
        "package_id": package_id,
        "language": "typescript",
        "name": package_name,
        "version": version,
        "source": "dts",
        "symbols": count,
    }


def ingest_dts_file(
    db: MnemeDB,
    *,
    package_name: str,
    path: str | os.PathLike[str],
    version: str | None = None,
    module_path: str | None = None,
) -> dict[str, Any]:
    """Index a .d.ts (or .ts) file from disk."""

    file_path = Path(path).expanduser().resolve()
    if not file_path.is_file():
        raise JsTsIngestError(f"file not found: {file_path}")
    return ingest_dts_text(
        db,
        package_name=package_name,
        source=file_path.read_text(encoding="utf-8"),
        version=version,
        module_path=module_path or package_name,
        source_url=str(file_path),
    )
