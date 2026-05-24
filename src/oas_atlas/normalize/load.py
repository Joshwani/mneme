from __future__ import annotations

import json
from typing import Any

import yaml


class OpenAPILoadError(ValueError):
    pass


def load_openapi_text(text: str) -> dict[str, Any]:
    """Load JSON or YAML OpenAPI text into a Python dict."""
    if not text.strip():
        raise OpenAPILoadError("empty OpenAPI document")
    try:
        if text.lstrip().startswith("{"):
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except Exception as exc:
        raise OpenAPILoadError(f"could not parse OpenAPI JSON/YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise OpenAPILoadError("OpenAPI document root must be an object")
    if not is_openapi_document(data):
        raise OpenAPILoadError("document does not look like OpenAPI/Swagger")
    return data


def is_openapi_document(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    has_version = "openapi" in data or "swagger" in data
    has_shape = isinstance(data.get("paths"), dict) or isinstance(data.get("webhooks"), dict)
    return bool(has_version and has_shape)
