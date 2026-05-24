from __future__ import annotations

from typing import Any

from .refs import resolve_object

SCHEMA_KEYS = {
    "type",
    "format",
    "title",
    "description",
    "default",
    "enum",
    "const",
    "nullable",
    "readOnly",
    "writeOnly",
    "deprecated",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "pattern",
}


def simplify_schema(
    schema: Any,
    root: dict[str, Any],
    *,
    depth: int = 0,
    max_depth: int = 4,
    max_properties: int = 30,
    seen_refs: set[str] | None = None,
) -> Any:
    """Return a compact, agent-readable schema summary.

    Full dereferencing can explode token count and cycles are common. This bounded resolver
    keeps the fields most useful for deciding whether an operation matches a task.
    """
    if seen_refs is None:
        seen_refs = set()
    if schema is None:
        return None
    if not isinstance(schema, dict):
        return schema

    ref = schema.get("$ref")
    if isinstance(ref, str):
        if not ref.startswith("#/"):
            return {"$ref": ref}
        if ref in seen_refs:
            return {"$ref": ref, "recursive": True}
        if depth >= max_depth:
            return {"$ref": ref}
        seen_refs.add(ref)
        resolved = resolve_object(schema, root, depth=0, seen=set(seen_refs))
        result = simplify_schema(
            resolved,
            root,
            depth=depth + 1,
            max_depth=max_depth,
            max_properties=max_properties,
            seen_refs=seen_refs,
        )
        seen_refs.remove(ref)
        return result

    out: dict[str, Any] = {}
    for key in SCHEMA_KEYS:
        if key in schema:
            out[key] = schema[key]

    for combiner in ("oneOf", "anyOf", "allOf"):
        if isinstance(schema.get(combiner), list):
            out[combiner] = [
                simplify_schema(
                    item,
                    root,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_properties=max_properties,
                    seen_refs=seen_refs,
                )
                for item in schema[combiner][:8]
            ]

    if "items" in schema:
        out["items"] = simplify_schema(
            schema["items"],
            root,
            depth=depth + 1,
            max_depth=max_depth,
            max_properties=max_properties,
            seen_refs=seen_refs,
        )

    properties = schema.get("properties")
    if isinstance(properties, dict):
        out["properties"] = {}
        for idx, (name, prop_schema) in enumerate(properties.items()):
            if idx >= max_properties:
                out["x-oas-atlas-truncated-properties"] = len(properties) - max_properties
                break
            out["properties"][name] = simplify_schema(
                prop_schema,
                root,
                depth=depth + 1,
                max_depth=max_depth,
                max_properties=max_properties,
                seen_refs=seen_refs,
            )

    if isinstance(schema.get("required"), list):
        out["required"] = schema["required"][:max_properties]

    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        out["additionalProperties"] = simplify_schema(
            additional,
            root,
            depth=depth + 1,
            max_depth=max_depth,
            max_properties=max_properties,
            seen_refs=seen_refs,
        )
    elif isinstance(additional, bool):
        out["additionalProperties"] = additional

    if not out and schema:
        # Last-resort summary for unusual schema dialects.
        for key in list(schema.keys())[:10]:
            if key != "example" and not key.startswith("x-"):
                out[key] = schema[key]
    return out


def schema_field_names(schema: Any, *, max_names: int = 50) -> list[str]:
    names: list[str] = []

    def visit(value: Any) -> None:
        if len(names) >= max_names:
            return
        if isinstance(value, dict):
            props = value.get("properties")
            if isinstance(props, dict):
                for name, child in props.items():
                    if name not in names:
                        names.append(name)
                    visit(child)
            if "items" in value:
                visit(value["items"])
            for key in ("oneOf", "anyOf", "allOf"):
                if isinstance(value.get(key), list):
                    for item in value[key]:
                        visit(item)

    visit(schema)
    return names[:max_names]
