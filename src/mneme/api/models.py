from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HOST_RE = re.compile(
    r"^(?:\*\.)?(?:localhost|[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)"
    r"(?::[0-9]{1,5})?$"
)
_METHOD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")


class IngestUrlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl

    @field_validator("url")
    @classmethod
    def reject_embedded_credentials(cls, value: HttpUrl) -> HttpUrl:
        if value.username is not None or value.password is not None:
            raise ValueError("URL credentials are not allowed")
        return value


class IngestFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)


class DiscoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=1, max_length=2048)

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        value = value.strip()
        parsed = urlsplit(value if "://" in value else f"https://{value}")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("must be a domain or HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("URL credentials are not allowed")
        return value


class AuthHeaderEnvironmentRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    env: str = Field(min_length=1, max_length=256)

    @field_validator("env")
    @classmethod
    def validate_env(cls, value: str) -> str:
        if not _ENV_NAME_RE.fullmatch(value):
            raise ValueError("must be a valid environment variable name")
        return value


class AuthMetadata(BaseModel):
    """Auth mechanism metadata. Literal credentials are intentionally unsupported."""

    model_config = ConfigDict(extra="forbid")

    type: str | None = Field(default=None, max_length=64)
    in_: str | None = Field(default=None, alias="in", max_length=32)
    name: str | None = Field(default=None, max_length=256)
    scheme: str | None = Field(default=None, max_length=64)
    env: str | None = Field(default=None, max_length=256)
    token_env: str | None = Field(default=None, max_length=256)
    username_env: str | None = Field(default=None, max_length=256)
    password_env: str | None = Field(default=None, max_length=256)
    api_key_env: str | None = Field(default=None, max_length=256)
    value_env: str | None = Field(default=None, max_length=256)
    headers: dict[str, AuthHeaderEnvironmentRef] | None = None

    @field_validator(
        "env",
        "token_env",
        "username_env",
        "password_env",
        "api_key_env",
        "value_env",
    )
    @classmethod
    def validate_env(cls, value: str | None) -> str | None:
        if value is not None and not _ENV_NAME_RE.fullmatch(value):
            raise ValueError("must be a valid environment variable name")
        return value


class AuthProfileMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_domain: str | None = Field(default=None, max_length=253)
    base_url: HttpUrl | None = None
    auth: AuthMetadata | None = None
    allowed_hosts: list[str] | None = None
    allow_methods: list[str] | None = None
    require_confirmation: bool | None = None
    verify_ssl: bool | None = None
    allow_any_host: bool | None = None

    @field_validator("provider_domain")
    @classmethod
    def validate_provider_domain(cls, value: str | None) -> str | None:
        if value is not None and not _HOST_RE.fullmatch(value):
            raise ValueError("must be a hostname, optionally with a port")
        return value.lower() if value else value

    @field_validator("allowed_hosts")
    @classmethod
    def validate_allowed_hosts(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if len(value) > 100:
            raise ValueError("must contain at most 100 hosts")
        normalized: list[str] = []
        for host in value:
            if not _HOST_RE.fullmatch(host):
                raise ValueError(f"invalid allowed host: {host!r}")
            normalized.append(host.lower())
        return list(dict.fromkeys(normalized))

    @field_validator("allow_methods")
    @classmethod
    def validate_allow_methods(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("must contain at least one method")
        normalized = [method.upper() for method in value]
        if any(not _METHOD_RE.fullmatch(method) for method in normalized):
            raise ValueError("contains an invalid HTTP method")
        return list(dict.fromkeys(normalized))

    def to_storage_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True, by_alias=True, mode="json")


class AuthProfileCreate(AuthProfileMetadata):
    name: str = Field(min_length=1, max_length=128)
