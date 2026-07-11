from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

import httpx

from mneme.auth import (
    AuthProfile,
    apply_auth_profile,
    choose_auth_profile,
    load_auth_profiles,
    profile_allows_host,
    redact_headers,
    redact_query,
)
from mneme.call_template import build_call_template


class OperationCallError(ValueError):
    """Raised when an operation cannot be prepared or executed safely."""


@dataclass(slots=True)
class CallInputs:
    path_params: dict[str, Any] | None = None
    query_params: dict[str, Any] | None = None
    headers: dict[str, Any] | None = None
    json_body: Any = None
    form_body: dict[str, Any] | None = None
    base_url: str | None = None
    auth_profile: str | None = None


def prepare_operation_call(
    operation: dict[str, Any],
    *,
    auth_profiles: dict[str, AuthProfile] | None = None,
    inputs: CallInputs | None = None,
    auth_config: str | None = None,
    include_secrets: bool = False,
) -> dict[str, Any]:
    """Prepare an HTTP request from a normalized operation card.

    By default the returned headers/query values are redacted so the result is safe
    to show to an LLM. Set include_secrets=True only immediately before execution.
    """

    inputs = inputs or CallInputs()
    profiles = auth_profiles if auth_profiles is not None else load_auth_profiles(auth_config)
    profile = choose_auth_profile(profiles, operation=operation, profile_name=inputs.auth_profile)

    method = str(operation.get("method") or "GET").upper()
    url = _operation_url(operation, profile=profile, base_url=inputs.base_url)
    url = _fill_path_params(url, inputs.path_params or {})

    query = {str(k): str(v) for k, v in (inputs.query_params or {}).items() if v is not None}
    headers = {str(k): str(v) for k, v in (inputs.headers or {}).items() if v is not None}
    request_body = (
        operation.get("request_body") if isinstance(operation.get("request_body"), dict) else {}
    )
    content_type = request_body.get("content_type") if isinstance(request_body, dict) else None

    warnings: list[str] = []
    if content_type and method not in {"GET", "HEAD"}:
        headers.setdefault("Content-Type", str(content_type))
    headers.setdefault("Accept", "application/json")

    warnings.extend(apply_auth_profile(profile, headers=headers, query=query))
    missing_required = _missing_required_inputs(
        operation,
        inputs,
        effective_query=query,
        effective_headers=headers,
    )
    if missing_required:
        warnings.append("missing required inputs: " + ", ".join(missing_required))

    parsed = urlparse(url)
    if query:
        merged_query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        merged_query.update(query)
        parsed = parsed._replace(query=urlencode(merged_query, doseq=True))
        url = urlunparse(parsed)

    host = parsed.hostname or ""
    _validate_host(host, profile=profile)

    body_mode = None
    body_preview = None
    if inputs.form_body is not None:
        body_mode = "form"
        body_preview = inputs.form_body
    elif inputs.json_body is not None:
        body_mode = "json"
        body_preview = inputs.json_body
    elif (
        method not in {"GET", "HEAD"}
        and isinstance(request_body, dict)
        and request_body.get("example") is not None
    ):
        body_mode = "json"
        body_preview = request_body.get("example")

    return {
        "operation_id": operation.get("operation_id"),
        "api_title": operation.get("api_title"),
        "provider_domain": operation.get("provider_domain"),
        "method": method,
        "url": url if include_secrets else _redact_url(url),
        "headers": headers if include_secrets else redact_headers(headers),
        "query_params": query if include_secrets else redact_query(query),
        "body_mode": body_mode,
        "body": body_preview,
        "auth_profile": profile.safe_dict() if profile else None,
        "missing_required": missing_required,
        "warnings": warnings,
        "call_template": build_call_template(operation),
        "can_execute": not missing_required,
    }


def execute_operation_call(
    operation: dict[str, Any],
    *,
    auth_profiles: dict[str, AuthProfile] | None = None,
    inputs: CallInputs | None = None,
    auth_config: str | None = None,
    timeout: float = 30.0,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Execute an operation call with local credentials.

    The default is dry_run=True, so agents can ask for a prepared call without
    sending network traffic. Any real request requires confirm=True.
    """

    inputs = inputs or CallInputs()
    profiles = auth_profiles if auth_profiles is not None else load_auth_profiles(auth_config)
    profile = choose_auth_profile(profiles, operation=operation, profile_name=inputs.auth_profile)
    prepared_safe = prepare_operation_call(operation, auth_profiles=profiles, inputs=inputs)
    if dry_run:
        return {"dry_run": True, "prepared_call": prepared_safe}

    method = str(operation.get("method") or "GET").upper()
    if profile and method not in profile.allow_methods:
        raise OperationCallError(
            f"method {method} is not allowed by auth profile {profile.name!r}; "
            f"allowed methods: {', '.join(profile.allow_methods)}"
        )
    if profile is None and method not in {"GET", "HEAD"}:
        raise OperationCallError(
            "mutating unauthenticated calls require an auth profile with allow_methods"
        )
    if not confirm:
        raise OperationCallError("real HTTP execution requires confirm=true")

    prepared = prepare_operation_call(
        operation,
        auth_profiles=profiles,
        inputs=inputs,
        include_secrets=True,
    )
    if prepared.get("missing_required"):
        raise OperationCallError(
            "cannot execute with missing required inputs: "
            + ", ".join(prepared["missing_required"])
        )

    request_kwargs: dict[str, Any] = {
        "method": method,
        "url": prepared["url"],
        "headers": prepared["headers"],
        "timeout": timeout,
    }
    if prepared.get("body_mode") == "json":
        request_kwargs["json"] = prepared.get("body")
    elif prepared.get("body_mode") == "form":
        request_kwargs["data"] = prepared.get("body")

    verify = profile.verify_ssl if profile else True
    with httpx.Client(verify=verify, follow_redirects=False) as client:
        response = client.request(**request_kwargs)

    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            response_body: Any = response.json()
        except ValueError:
            response_body = response.text
    else:
        response_body = response.text
        if isinstance(response_body, str) and len(response_body) > 12000:
            response_body = response_body[:12000] + "..."

    return {
        "dry_run": False,
        "request": prepared_safe,
        "response": {
            "status_code": response.status_code,
            "reason_phrase": response.reason_phrase,
            "headers": _safe_response_headers(dict(response.headers)),
            "body": response_body,
        },
    }


def _operation_url(
    operation: dict[str, Any], *, profile: AuthProfile | None, base_url: str | None = None
) -> str:
    path = str(operation.get("path") or "")
    server_url = base_url or (profile.base_url if profile and profile.base_url else None)
    if server_url is None:
        for server in operation.get("servers") or []:
            if isinstance(server, dict) and isinstance(server.get("url"), str):
                server_url = _resolve_server_variables(server["url"], server.get("variables"))
                break
    if not server_url:
        raise OperationCallError(
            "operation has no server URL; pass base_url or an auth profile with base_url"
        )
    if server_url.startswith("//"):
        server_url = "https:" + server_url
    if not urlparse(server_url).scheme:
        server_url = "https://" + server_url.lstrip("/")
    return urljoin(server_url.rstrip("/") + "/", path.lstrip("/"))


def _resolve_server_variables(url: str, variables: Any) -> str:
    if not isinstance(variables, dict):
        return url
    out = url
    for name, data in variables.items():
        default = data.get("default") if isinstance(data, dict) else None
        if default is not None:
            out = out.replace("{" + str(name) + "}", str(default))
    return out


def _fill_path_params(url: str, path_params: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in path_params or path_params[name] is None:
            return match.group(0)
        return quote(str(path_params[name]), safe="")

    return re.sub(r"\{([^}/?#]+)\}", replace, url)


def _missing_required_inputs(
    operation: dict[str, Any],
    inputs: CallInputs,
    *,
    effective_query: dict[str, str] | None = None,
    effective_headers: dict[str, str] | None = None,
) -> list[str]:
    missing: list[str] = []
    path_params = inputs.path_params or {}
    query_params = effective_query if effective_query is not None else (inputs.query_params or {})
    headers = effective_headers if effective_headers is not None else (inputs.headers or {})
    body = inputs.json_body if inputs.json_body is not None else inputs.form_body

    for param in operation.get("parameters") or []:
        if not param.get("required"):
            continue
        name = str(param.get("name") or "")
        location = param.get("in")
        if location == "path" and name not in path_params:
            missing.append(f"path.{name}")
        elif location == "query" and name not in query_params:
            missing.append(f"query.{name}")
        elif location == "header" and name not in headers:
            missing.append(f"header.{name}")

    request_body = operation.get("request_body")
    if isinstance(request_body, dict) and request_body.get("required") and body is None:
        missing.append("body")
    schema = request_body.get("schema") if isinstance(request_body, dict) else None
    if isinstance(schema, dict) and isinstance(body, dict):
        for name in schema.get("required") or []:
            if name not in body:
                missing.append(f"body.{name}")
    return list(dict.fromkeys(missing))


def _validate_host(host: str, *, profile: AuthProfile | None) -> None:
    global_allowlist = [
        x.strip().lower()
        for x in os.environ.get("MNEME_HTTP_ALLOW_HOSTS", "").split(",")
        if x.strip()
    ]
    if global_allowlist and not any(_host_matches(pattern, host) for pattern in global_allowlist):
        raise OperationCallError(f"host {host!r} is not in MNEME_HTTP_ALLOW_HOSTS")
    if profile and not profile_allows_host(profile, host):
        raise OperationCallError(
            f"host {host!r} is not allowed by auth profile {profile.name!r}; "
            "add it to allowed_hosts or set allow_any_host=true"
        )


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    safe_query = redact_query({str(k): str(v) for k, v in query.items()})
    return urlunparse(parsed._replace(query=urlencode(safe_query, doseq=True)))


def _safe_response_headers(headers: dict[str, str]) -> dict[str, str]:
    allowed = {
        "content-type",
        "content-length",
        "etag",
        "last-modified",
        "x-request-id",
        "retry-after",
        "rate-limit-remaining",
        "ratelimit-remaining",
    }
    return {key: value for key, value in headers.items() if key.lower() in allowed}


def _host_matches(pattern: str, host: str) -> bool:
    pattern = pattern.lower().strip()
    host = host.lower().strip()
    if pattern == host:
        return True
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host.endswith(suffix) and host != pattern[2:]
    return False
