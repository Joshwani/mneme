from __future__ import annotations

from typing import Any


def build_call_template(operation: dict[str, Any]) -> dict[str, Any]:
    servers = operation.get("servers") or []
    server_url = "{server_url}"
    if servers and isinstance(servers[0], dict) and isinstance(servers[0].get("url"), str):
        server_url = servers[0]["url"]
    path = operation.get("path") or ""
    method = operation.get("method") or "GET"

    path_params: dict[str, str] = {}
    query_params: dict[str, str] = {}
    headers: dict[str, str] = {}
    for param in operation.get("parameters") or []:
        name = param.get("name")
        location = param.get("in")
        if not name:
            continue
        placeholder = "{" + str(name) + "}"
        if location == "path":
            path_params[str(name)] = placeholder
        elif location == "query":
            query_params[str(name)] = placeholder
        elif location == "header":
            headers[str(name)] = placeholder

    auth = operation.get("auth") or {}
    for scheme in auth.get("schemes") or []:
        typ = (scheme.get("type") or "").lower()
        name = scheme.get("name") or "Authorization"
        location = scheme.get("in")
        scheme_name = (scheme.get("scheme") or "").lower()
        if typ == "http" and scheme_name == "bearer":
            headers["Authorization"] = "Bearer ${TOKEN}"
        elif typ == "apiKey" and location == "header":
            headers[str(name)] = "${API_KEY}"
        elif typ == "apiKey" and location == "query":
            query_params[str(name)] = "${API_KEY}"

    body = None
    request_body = operation.get("request_body")
    if isinstance(request_body, dict) and request_body.get("schema"):
        body = _sample_from_schema(request_body.get("schema"))

    return {
        "operation_id": operation.get("operation_id"),
        "method": method,
        "url_template": server_url.rstrip("/") + path,
        "path_params": path_params,
        "query_params": query_params,
        "headers": headers,
        "content_type": request_body.get("content_type")
        if isinstance(request_body, dict)
        else None,
        "json_body_template": body,
        "notes": [
            "This is a call template, not an executed request.",
            "Fill placeholders and credentials before sending.",
        ],
    }


def _sample_from_schema(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return None
    typ = schema.get("type")
    if typ == "object" or isinstance(schema.get("properties"), dict):
        required = set(schema.get("required") or [])
        out: dict[str, Any] = {}
        for name, prop in list((schema.get("properties") or {}).items())[:25]:
            if name in required or len(out) < 8:
                out[name] = _sample_from_schema(prop)
        return out
    if typ == "array":
        return [_sample_from_schema(schema.get("items"))]
    if "enum" in schema and isinstance(schema.get("enum"), list) and schema["enum"]:
        return schema["enum"][0]
    if typ in {"integer", "number"}:
        return 0
    if typ == "boolean":
        return False
    if typ == "string" or typ is None:
        fmt = schema.get("format")
        if fmt == "date-time":
            return "2026-05-23T00:00:00Z"
        if fmt == "date":
            return "2026-05-23"
        return "string"
    return None
