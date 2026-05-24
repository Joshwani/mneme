from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from oas_atlas.util import provider_domain_from_url, stable_id

from .refs import resolve_object
from .schemas import schema_field_names, simplify_schema

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


@dataclass(slots=True)
class OperationCard:
    operation_id: str
    spec_id: str
    api_title: str | None
    api_version: str | None
    provider_domain: str | None
    method: str
    path: str
    operation_id_native: str | None
    summary: str | None
    description: str | None
    tags: list[str]
    servers: list[dict[str, Any]]
    auth: dict[str, Any]
    parameters: list[dict[str, Any]]
    request_body: dict[str, Any] | None
    responses: dict[str, Any]
    agent_text: str
    spec_slice: dict[str, Any]
    source_url: str | None
    quality_score: float
    fetched_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_spec_id(source_url: str | None, content_hash: str) -> str:
    stable = source_url or content_hash
    return stable_id("spec", stable)


def make_operation_id(spec_id: str, method: str, path: str, native_id: str | None) -> str:
    return stable_id("op", f"{spec_id}:{method.upper()}:{path}:{native_id or ''}")


def normalize_operations(
    spec: dict[str, Any],
    *,
    source_url: str | None,
    content_hash: str,
    fetched_at: str,
) -> tuple[dict[str, Any], list[OperationCard]]:
    info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
    api_title = info.get("title")
    api_version = info.get("version")
    provider_domain = _provider_domain(spec, source_url)
    openapi_version = str(spec.get("openapi") or spec.get("swagger") or "")
    spec_id = make_spec_id(source_url, content_hash)

    spec_meta = {
        "spec_id": spec_id,
        "source_url": source_url,
        "title": api_title,
        "version": api_version,
        "provider_domain": provider_domain,
        "content_hash": content_hash,
        "openapi_version": openapi_version,
        "fetched_at": fetched_at,
    }

    cards: list[OperationCard] = []
    paths = spec.get("paths") if isinstance(spec.get("paths"), dict) else {}
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        path_parameters = _normalize_parameters(path_item.get("parameters", []), spec)
        for method, op in path_item.items():
            method_lower = method.lower()
            if method_lower not in HTTP_METHODS or not isinstance(op, dict):
                continue
            native_id = op.get("operationId") if isinstance(op.get("operationId"), str) else None
            operation_id = make_operation_id(spec_id, method_lower, path, native_id)
            operation_parameters = _normalize_parameters(op.get("parameters", []), spec)
            parameters = _dedupe_params(path_parameters + operation_parameters)
            request_body = _normalize_request_body(op, spec, parameters=parameters)
            responses = _normalize_responses(op.get("responses", {}), spec)
            servers = _operation_servers(spec, path_item, op)
            auth = _normalize_auth(spec, op)
            tags = [str(tag) for tag in op.get("tags", []) if tag is not None]
            summary = op.get("summary") if isinstance(op.get("summary"), str) else None
            description = op.get("description") if isinstance(op.get("description"), str) else None
            agent_text = _make_agent_text(
                api_title=api_title,
                method=method_lower,
                path=path,
                native_id=native_id,
                summary=summary,
                description=description,
                tags=tags,
                parameters=parameters,
                request_body=request_body,
                responses=responses,
                auth=auth,
            )
            spec_slice = _make_spec_slice(
                spec=spec,
                path=path,
                method=method_lower,
                operation=op,
                parameters=parameters,
                request_body=request_body,
                responses=responses,
                servers=servers,
                api_title=api_title,
                api_version=api_version,
            )
            quality_score = _quality_score(
                summary=summary,
                description=description,
                parameters=parameters,
                request_body=request_body,
                responses=responses,
                auth=auth,
            )
            cards.append(
                OperationCard(
                    operation_id=operation_id,
                    spec_id=spec_id,
                    api_title=api_title,
                    api_version=api_version,
                    provider_domain=provider_domain,
                    method=method_lower.upper(),
                    path=path,
                    operation_id_native=native_id,
                    summary=summary,
                    description=description,
                    tags=tags,
                    servers=servers,
                    auth=auth,
                    parameters=parameters,
                    request_body=request_body,
                    responses=responses,
                    agent_text=agent_text,
                    spec_slice=spec_slice,
                    source_url=source_url,
                    quality_score=quality_score,
                    fetched_at=fetched_at,
                )
            )
    return spec_meta, cards


def _provider_domain(spec: dict[str, Any], source_url: str | None) -> str | None:
    servers = spec.get("servers")
    if isinstance(servers, list):
        for server in servers:
            if isinstance(server, dict) and isinstance(server.get("url"), str):
                domain = provider_domain_from_url(server["url"])
                if domain:
                    return domain
    host = spec.get("host")
    if isinstance(host, str):
        return host.lower()
    return provider_domain_from_url(source_url)


def _operation_servers(
    spec: dict[str, Any], path_item: dict[str, Any], op: dict[str, Any]
) -> list[dict[str, Any]]:
    for value in (op.get("servers"), path_item.get("servers"), spec.get("servers")):
        if isinstance(value, list) and value:
            return [server for server in value if isinstance(server, dict)][:8]
    # Swagger 2.0 fallback.
    host = spec.get("host")
    if isinstance(host, str):
        schemes = spec.get("schemes") if isinstance(spec.get("schemes"), list) else ["https"]
        base_path = spec.get("basePath") if isinstance(spec.get("basePath"), str) else ""
        return [{"url": f"{schemes[0]}://{host}{base_path}"}]
    return []


def _normalize_parameters(raw_parameters: Any, root: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(raw_parameters, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw_parameters:
        resolved = resolve_object(item, root)
        if not isinstance(resolved, dict):
            continue
        location = resolved.get("in")
        name = resolved.get("name")
        if not isinstance(location, str) or not isinstance(name, str):
            continue
        schema = resolved.get("schema")
        if not isinstance(schema, dict):
            # Swagger 2.0 sometimes puts primitive parameter schema at the top level.
            schema = {
                key: resolved[key]
                for key in ("type", "format", "items", "enum", "default", "minimum", "maximum")
                if key in resolved
            }
        out.append(
            {
                "name": name,
                "in": location,
                "required": bool(resolved.get("required")),
                "description": resolved.get("description"),
                "schema": simplify_schema(schema, root) if schema else {},
            }
        )
    return out


def _dedupe_params(params: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    # Later operation-level params should override path-level params. Walk reversed.
    for param in reversed(params):
        key = (str(param.get("in")), str(param.get("name")))
        if key not in seen:
            seen.add(key)
            out.append(param)
    return list(reversed(out))


def _normalize_request_body(
    op: dict[str, Any],
    root: dict[str, Any],
    *,
    parameters: list[dict[str, Any]],
) -> dict[str, Any] | None:
    raw = op.get("requestBody")
    if isinstance(raw, dict):
        resolved = resolve_object(raw, root)
        if isinstance(resolved, dict):
            content = resolved.get("content")
            content_type, schema, example = _first_content_schema(content, root)
            return {
                "required": bool(resolved.get("required")),
                "description": resolved.get("description"),
                "content_type": content_type,
                "schema": schema,
                "schema_fields": schema_field_names(schema),
                "example": example,
            }

    # Swagger 2.0 body/formData parameters.
    body_params = [p for p in parameters if p.get("in") == "body"]
    if body_params:
        body = body_params[0]
        return {
            "required": bool(body.get("required")),
            "description": body.get("description"),
            "content_type": "application/json",
            "schema": body.get("schema") or {},
            "schema_fields": schema_field_names(body.get("schema") or {}),
            "example": None,
        }
    form_params = [p for p in parameters if p.get("in") == "formData"]
    if form_params:
        schema = {
            "type": "object",
            "properties": {p["name"]: p.get("schema", {}) for p in form_params if p.get("name")},
            "required": [p["name"] for p in form_params if p.get("required") and p.get("name")],
        }
        return {
            "required": bool(schema["required"]),
            "description": "Form data request body inferred from Swagger 2.0 parameters.",
            "content_type": "application/x-www-form-urlencoded",
            "schema": schema,
            "schema_fields": schema_field_names(schema),
            "example": None,
        }
    return None


def _first_content_schema(content: Any, root: dict[str, Any]) -> tuple[str | None, Any, Any]:
    if not isinstance(content, dict) or not content:
        return None, {}, None
    preferred = None
    for candidate in ("application/json", "application/*+json"):
        if candidate in content:
            preferred = candidate
            break
    if preferred is None:
        preferred = next(iter(content.keys()))
    media = content.get(preferred)
    if not isinstance(media, dict):
        return preferred, {}, None
    schema = simplify_schema(media.get("schema"), root) if media.get("schema") else {}
    example = media.get("example")
    examples = media.get("examples")
    if example is None and isinstance(examples, dict):
        first = next(iter(examples.values()), None)
        if isinstance(first, dict):
            example = first.get("value")
    return preferred, schema, example


def _normalize_responses(raw_responses: Any, root: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw_responses, dict):
        return {}
    out: dict[str, Any] = {}
    for status, response in raw_responses.items():
        resolved = resolve_object(response, root)
        if not isinstance(resolved, dict):
            continue
        content_type, schema, example = _first_content_schema(resolved.get("content"), root)
        if not schema and isinstance(resolved.get("schema"), dict):
            schema = simplify_schema(resolved.get("schema"), root)
            content_type = content_type or "application/json"
        out[str(status)] = {
            "description": resolved.get("description"),
            "content_type": content_type,
            "schema": schema,
            "schema_fields": schema_field_names(schema),
            "example": example,
        }
    return out


def _normalize_auth(spec: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    schemes = (
        spec.get("components", {}).get("securitySchemes")
        if isinstance(spec.get("components"), dict)
        else None
    )
    if schemes is None:
        schemes = spec.get("securityDefinitions")
    if not isinstance(schemes, dict):
        schemes = {}
    raw_security = op.get("security", spec.get("security"))
    if raw_security == []:
        return {"required": False, "schemes": []}
    used: list[dict[str, Any]] = []
    if isinstance(raw_security, list):
        for requirement in raw_security:
            if isinstance(requirement, dict):
                for name, scopes in requirement.items():
                    scheme = (
                        schemes.get(name, {}) if isinstance(schemes.get(name, {}), dict) else {}
                    )
                    used.append(
                        {
                            "name": name,
                            "type": scheme.get("type"),
                            "in": scheme.get("in"),
                            "scheme": scheme.get("scheme"),
                            "bearerFormat": scheme.get("bearerFormat"),
                            "scopes": scopes if isinstance(scopes, list) else [],
                        }
                    )
    if not used and schemes:
        for name, scheme in list(schemes.items())[:8]:
            if isinstance(scheme, dict):
                used.append(
                    {
                        "name": name,
                        "type": scheme.get("type"),
                        "in": scheme.get("in"),
                        "scheme": scheme.get("scheme"),
                        "bearerFormat": scheme.get("bearerFormat"),
                        "scopes": [],
                    }
                )
    return {"required": bool(used), "schemes": used}


def _make_agent_text(
    *,
    api_title: str | None,
    method: str,
    path: str,
    native_id: str | None,
    summary: str | None,
    description: str | None,
    tags: list[str],
    parameters: list[dict[str, Any]],
    request_body: dict[str, Any] | None,
    responses: dict[str, Any],
    auth: dict[str, Any],
) -> str:
    bits: list[str] = []
    if api_title:
        bits.append(f"API: {api_title}.")
    bits.append(f"Operation: {method.upper()} {path}.")
    if native_id:
        bits.append(f"Operation ID: {native_id}.")
    if summary:
        bits.append(f"Summary: {summary}")
    if description:
        bits.append(f"Description: {description}")
    if tags:
        bits.append("Tags: " + ", ".join(tags) + ".")
    if parameters:
        parts = []
        for p in parameters[:20]:
            req = "required" if p.get("required") else "optional"
            parts.append(f"{p.get('name')} ({p.get('in')}, {req})")
        bits.append("Parameters: " + "; ".join(parts) + ".")
    if request_body:
        fields = request_body.get("schema_fields") or []
        if fields:
            bits.append("Request body fields: " + ", ".join(fields[:30]) + ".")
        elif request_body.get("schema"):
            bits.append("Request body is present.")
    success_fields: list[str] = []
    for status in ("200", "201", "202", "204", "default"):
        if status in responses and responses[status].get("schema_fields"):
            success_fields.extend(responses[status]["schema_fields"])
    if success_fields:
        bits.append("Response fields: " + ", ".join(dict.fromkeys(success_fields).keys()) + ".")
    if auth.get("required"):
        auth_names = [s.get("name") or s.get("type") for s in auth.get("schemes", [])]
        bits.append("Authentication required: " + ", ".join(str(x) for x in auth_names if x) + ".")
    else:
        bits.append("Authentication: none declared.")
    return "\n".join(bits)


def _make_spec_slice(
    *,
    spec: dict[str, Any],
    path: str,
    method: str,
    operation: dict[str, Any],
    parameters: list[dict[str, Any]],
    request_body: dict[str, Any] | None,
    responses: dict[str, Any],
    servers: list[dict[str, Any]],
    api_title: str | None,
    api_version: str | None,
) -> dict[str, Any]:
    return {
        "openapi": "3.1.0" if "openapi" in spec else "2.0",
        "info": {"title": api_title, "version": api_version},
        "servers": servers,
        "paths": {
            path: {
                method: {
                    "operationId": operation.get("operationId"),
                    "summary": operation.get("summary"),
                    "description": operation.get("description"),
                    "tags": operation.get("tags", []),
                    "parameters": parameters,
                    "requestBody": request_body,
                    "responses": responses,
                }
            }
        },
    }


def _quality_score(
    *,
    summary: str | None,
    description: str | None,
    parameters: list[dict[str, Any]],
    request_body: dict[str, Any] | None,
    responses: dict[str, Any],
    auth: dict[str, Any],
) -> float:
    score = 0.25
    if summary:
        score += 0.20
    if description:
        score += 0.20
    if responses:
        score += 0.15
    if parameters or request_body:
        score += 0.10
    if request_body and request_body.get("schema"):
        score += 0.05
    if auth.get("schemes") is not None:
        score += 0.05
    return min(1.0, round(score, 3))
