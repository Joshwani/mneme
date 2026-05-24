from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_AUTH_CONFIG_PATH = "~/.config/mneme/auth.json"


class AuthConfigError(ValueError):
    """Raised when a local auth profile is missing or malformed."""


@dataclass(slots=True)
class AuthProfile:
    """Local auth profile used by the HTTP executor.

    Secrets should usually be referenced through environment variables. safe_dict()
    intentionally omits secret values so results can be returned to an LLM.
    """

    name: str
    provider_domain: str | None = None
    base_url: str | None = None
    auth: dict[str, Any] = field(default_factory=dict)
    default_headers: dict[str, str] = field(default_factory=dict)
    default_query: dict[str, str] = field(default_factory=dict)
    allowed_hosts: list[str] = field(default_factory=list)
    allow_methods: list[str] = field(default_factory=lambda: ["GET", "HEAD"])
    require_confirmation: bool = True
    verify_ssl: bool = True
    allow_any_host: bool = False

    @classmethod
    def from_mapping(cls, name: str, data: dict[str, Any]) -> "AuthProfile":
        auth = data.get("auth") if isinstance(data.get("auth"), dict) else {}
        if not auth and isinstance(data.get("type"), str):
            auth = {key: value for key, value in data.items() if key not in PROFILE_KEYS}
            auth["type"] = data["type"]

        allowed_hosts = list(_string_list(data.get("allowed_hosts")))
        provider_domain = _optional_str(data.get("provider_domain"))
        if provider_domain:
            allowed_hosts.append(provider_domain)
        base_url = _optional_str(data.get("base_url"))
        if base_url:
            host = _host_from_url(base_url)
            if host:
                allowed_hosts.append(host)

        allow_methods = [m.upper() for m in _string_list(data.get("allow_methods"))]
        if not allow_methods:
            allow_methods = ["GET", "HEAD"]

        return cls(
            name=name,
            provider_domain=provider_domain,
            base_url=base_url,
            auth=dict(auth),
            default_headers={
                str(k): str(v) for k, v in (data.get("default_headers") or {}).items()
            },
            default_query={str(k): str(v) for k, v in (data.get("default_query") or {}).items()},
            allowed_hosts=list(dict.fromkeys(h.lower() for h in allowed_hosts if h)),
            allow_methods=allow_methods,
            require_confirmation=bool(data.get("require_confirmation", True)),
            verify_ssl=bool(data.get("verify_ssl", True)),
            allow_any_host=bool(data.get("allow_any_host", False)),
        )

    def safe_dict(self) -> dict[str, Any]:
        auth_type = self.auth.get("type")
        safe_auth = {"type": auth_type} if auth_type else {}
        for key in ("in", "name", "scheme"):
            if key in self.auth:
                safe_auth[key] = self.auth[key]
        for key in ("env", "token_env", "username_env", "password_env", "api_key_env", "value_env"):
            if key in self.auth:
                safe_auth[key] = self.auth[key]
        if "headers" in self.auth and isinstance(self.auth["headers"], dict):
            safe_auth["headers"] = {
                str(k): _redacted_secret_ref(v) for k, v in self.auth["headers"].items()
            }
        return {
            "name": self.name,
            "provider_domain": self.provider_domain,
            "base_url": self.base_url,
            "auth": safe_auth,
            "default_headers": sorted(self.default_headers.keys()),
            "default_query": sorted(self.default_query.keys()),
            "allowed_hosts": self.allowed_hosts,
            "allow_methods": self.allow_methods,
            "require_confirmation": self.require_confirmation,
            "verify_ssl": self.verify_ssl,
            "allow_any_host": self.allow_any_host,
        }


PROFILE_KEYS = {
    "provider_domain",
    "base_url",
    "auth",
    "default_headers",
    "default_query",
    "allowed_hosts",
    "allow_methods",
    "require_confirmation",
    "verify_ssl",
    "allow_any_host",
}


def default_auth_config_path() -> Path:
    return Path(os.environ.get("MNEME_AUTH_CONFIG", DEFAULT_AUTH_CONFIG_PATH)).expanduser()


def load_auth_profiles(path: str | Path | None = None) -> dict[str, AuthProfile]:
    config_path = Path(path).expanduser() if path else default_auth_config_path()
    if not config_path.exists():
        return {}
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuthConfigError(f"invalid JSON auth config {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise AuthConfigError(f"auth config must be a JSON object: {config_path}")
    raw_profiles = raw.get("profiles", raw)
    if not isinstance(raw_profiles, dict):
        raise AuthConfigError("auth config must contain a 'profiles' object")
    profiles: dict[str, AuthProfile] = {}
    for name, data in raw_profiles.items():
        if not isinstance(data, dict):
            raise AuthConfigError(f"auth profile {name!r} must be an object")
        profiles[str(name)] = AuthProfile.from_mapping(str(name), data)
    return profiles


def list_auth_profiles(path: str | Path | None = None) -> dict[str, Any]:
    profiles = load_auth_profiles(path)
    return {"profiles": [profile.safe_dict() for profile in profiles.values()]}


def choose_auth_profile(
    profiles: dict[str, AuthProfile],
    *,
    operation: dict[str, Any],
    profile_name: str | None = None,
) -> AuthProfile | None:
    if profile_name:
        profile = profiles.get(profile_name)
        if profile is None:
            raise AuthConfigError(f"unknown auth profile: {profile_name}")
        return profile
    provider_domain = (operation.get("provider_domain") or "").lower()
    server_hosts = [
        _host_from_url(server.get("url"))
        for server in operation.get("servers") or []
        if isinstance(server, dict)
    ]
    candidates = [provider_domain, *[h for h in server_hosts if h]]
    for profile in profiles.values():
        for candidate in candidates:
            if candidate and profile_allows_host(profile, candidate):
                return profile
    return None


def apply_auth_profile(
    profile: AuthProfile | None,
    *,
    headers: dict[str, str],
    query: dict[str, str],
) -> list[str]:
    if profile is None:
        return []
    notes: list[str] = []
    for key, value in profile.default_headers.items():
        headers.setdefault(key, value)
    for key, value in profile.default_query.items():
        query.setdefault(key, value)

    auth = profile.auth or {}
    auth_type = str(auth.get("type") or "none").lower()
    if auth_type in {"", "none", "noauth"}:
        notes.append(f"auth profile {profile.name!r} declares no auth")
        return notes
    if auth_type in {"bearer", "oauth2", "oauth"}:
        token = _secret(auth, "token") or _secret(auth, "value") or _secret(auth, "api_key")
        if not token:
            raise AuthConfigError(f"auth profile {profile.name!r} is missing a bearer token/env")
        scheme = str(auth.get("scheme") or "Bearer")
        headers["Authorization"] = f"{scheme} {token}"
        notes.append(f"applied bearer auth from profile {profile.name!r}")
        return notes
    if auth_type in {"api_key", "apikey", "key"}:
        key_name = str(auth.get("name") or auth.get("key") or "X-API-Key")
        key_value = _secret(auth, "value") or _secret(auth, "api_key") or _secret(auth, "token")
        if not key_value:
            raise AuthConfigError(f"auth profile {profile.name!r} is missing an API key/env")
        location = str(auth.get("in") or "header").lower()
        if location == "query":
            query[key_name] = key_value
        elif location == "header":
            headers[key_name] = key_value
        else:
            raise AuthConfigError(
                f"unsupported api_key location {location!r} in profile {profile.name!r}"
            )
        notes.append(f"applied api key auth from profile {profile.name!r}")
        return notes
    if auth_type == "basic":
        import base64

        username = _secret(auth, "username") or _secret(auth, "user") or ""
        password = _secret(auth, "password") or ""
        if not username and not password:
            raise AuthConfigError(f"auth profile {profile.name!r} is missing basic credentials/env")
        raw = f"{username}:{password}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
        notes.append(f"applied basic auth from profile {profile.name!r}")
        return notes
    if auth_type in {"headers", "custom_headers", "custom"}:
        custom_headers = auth.get("headers")
        if not isinstance(custom_headers, dict):
            raise AuthConfigError(f"custom auth profile {profile.name!r} requires a headers object")
        for header_name, ref in custom_headers.items():
            headers[str(header_name)] = _secret_from_ref(ref)
        notes.append(f"applied custom headers from profile {profile.name!r}")
        return notes
    raise AuthConfigError(f"unsupported auth type {auth_type!r} in profile {profile.name!r}")


def profile_allows_host(profile: AuthProfile, host: str | None) -> bool:
    if not host:
        return False
    if profile.allow_any_host:
        return True
    host = host.lower().split(":", 1)[0]
    if not profile.allowed_hosts:
        return False
    return any(_host_matches(pattern, host) for pattern in profile.allowed_hosts)


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in {"authorization", "x-api-key", "api-key", "apikey"}:
            out[key] = _redact(value)
        else:
            out[key] = value
    return out


def redact_query(query: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in query.items():
        lowered = key.lower()
        if any(marker in lowered for marker in ("token", "key", "secret", "password")):
            out[key] = _redact(value)
        else:
            out[key] = value
    return out


def _secret(auth: dict[str, Any], base: str) -> str | None:
    for key in (f"{base}_env", "env"):
        env_name = auth.get(key)
        if isinstance(env_name, str) and env_name:
            value = os.environ.get(env_name)
            if value:
                return value
    value = auth.get(base)
    if value is None and base == "value":
        value = auth.get("secret")
    if value is None:
        return None
    return str(value)


def _secret_from_ref(ref: Any) -> str:
    if isinstance(ref, dict):
        if isinstance(ref.get("env"), str):
            value = os.environ.get(ref["env"])
            if not value:
                raise AuthConfigError(f"environment variable {ref['env']!r} is not set")
            return value
        if "value" in ref:
            return str(ref["value"])
    if isinstance(ref, str):
        return ref
    raise AuthConfigError("secret reference must be a string or {env|value} object")


def _redacted_secret_ref(ref: Any) -> str:
    if isinstance(ref, dict) and isinstance(ref.get("env"), str):
        return f"env:{ref['env']}"
    return "<redacted>"


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(x) for x in value if x is not None]
    return []


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _host_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.hostname:
        return parsed.hostname.lower()
    return None


def _host_matches(pattern: str, host: str) -> bool:
    pattern = pattern.lower().strip()
    host = host.lower().strip()
    if pattern == host:
        return True
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host.endswith(suffix) and host != pattern[2:]
    return False


def _redact(value: str) -> str:
    if not value:
        return "<redacted>"
    if value.lower().startswith("bearer "):
        return "Bearer <redacted>"
    if value.lower().startswith("basic "):
        return "Basic <redacted>"
    return "<redacted>"
