from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import unquote


def _pointer_parts(ref: str) -> list[str]:
    if not ref.startswith("#/"):
        return []
    raw_parts = ref[2:].split("/")
    return [unquote(part).replace("~1", "/").replace("~0", "~") for part in raw_parts]


def resolve_local_ref(ref: str, root: dict[str, Any]) -> Any:
    current: Any = root
    for part in _pointer_parts(ref):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise KeyError(f"unresolvable local ref: {ref}")
    return current


def resolve_object(
    obj: Any, root: dict[str, Any], *, depth: int = 0, seen: set[str] | None = None
) -> Any:
    """Resolve a local $ref object, preserving sibling fields when present.

    This intentionally resolves only local references. Remote references are left as-is.
    """
    if seen is None:
        seen = set()
    if depth > 20:
        return obj
    if isinstance(obj, dict) and isinstance(obj.get("$ref"), str):
        ref = obj["$ref"]
        if not ref.startswith("#/"):
            return obj
        if ref in seen:
            return {"$ref": ref, "x-oas-atlas-recursive-ref": True}
        try:
            target = deepcopy(resolve_local_ref(ref, root))
        except KeyError:
            return obj
        seen.add(ref)
        resolved = resolve_object(target, root, depth=depth + 1, seen=seen)
        if isinstance(resolved, dict):
            # OpenAPI allows sibling fields next to $ref in 3.1-ish contexts. Keep them.
            siblings = {k: deepcopy(v) for k, v in obj.items() if k != "$ref"}
            resolved.update(siblings)
        seen.remove(ref)
        return resolved
    return obj
