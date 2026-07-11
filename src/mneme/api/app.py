from __future__ import annotations

import importlib.metadata
import os
import platform
import secrets
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from mneme.api.models import (
    AuthProfileCreate,
    AuthProfileMetadata,
    DiscoverRequest,
    IngestFileRequest,
    IngestUrlRequest,
)
from mneme.auth import (
    AuthConfigError,
    default_auth_config_path,
    delete_auth_profile,
    list_auth_profiles,
    load_auth_profiles,
    upsert_auth_profile_metadata,
)
from mneme.call_template import build_call_template
from mneme.crawl.discover import discover_domain
from mneme.fetch import Fetcher
from mneme.http_client import CallInputs, execute_operation_call, prepare_operation_call
from mneme.index.db import MnemeDB, default_db_path
from mneme.index.ingest import ingest_file, ingest_url
from mneme.index.search import SearchFilters, search_operations


class SearchInclude(BaseModel):
    schemas: str = Field(default="minimal", description="Reserved for future: none|minimal|full")
    examples: bool = True
    auth: bool = True


class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=50)
    filters: dict[str, Any] = Field(default_factory=dict)
    include: SearchInclude = Field(default_factory=SearchInclude)
    token_budget: int | None = Field(default=None, ge=500, le=128000)


class OperationCallRequest(BaseModel):
    auth_profile: str | None = None
    auth_config: str | None = None
    path_params: dict[str, Any] = Field(default_factory=dict)
    query_params: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, Any] = Field(default_factory=dict)
    json_body: Any = None
    form_body: dict[str, Any] | None = None
    base_url: str | None = None
    confirm: bool = False
    dry_run: bool = True
    timeout: float = Field(default=30.0, ge=1.0, le=120.0)


def create_app(
    db_path: str | None = None,
    *,
    auth_config_path: str | None = None,
    management_token: str | None = None,
) -> FastAPI:
    db_path = db_path or default_db_path()
    resolved_management_token = (
        management_token
        if management_token is not None
        else os.environ.get("MNEME_MANAGEMENT_TOKEN")
    )
    resolved_auth_path = (
        Path(auth_config_path).expanduser()
        if auth_config_path is not None
        else default_auth_config_path()
    )
    app = FastAPI(
        title="Mneme",
        version=_package_version(),
        description="Agent-optimized search over OpenAPI-described operations.",
    )
    db = MnemeDB(db_path)

    def require_management_token(
        provided_token: str | None = Header(
            default=None,
            alias="X-Mneme-Management-Token",
        ),
    ) -> None:
        if not resolved_management_token:
            raise HTTPException(status_code=404, detail="Not Found")
        if provided_token is None or not secrets.compare_digest(
            provided_token,
            resolved_management_token,
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "stats": db.stats()}

    @app.get("/stats")
    def stats() -> dict[str, Any]:
        return db.stats()

    @app.get("/version")
    def version() -> dict[str, Any]:
        return {"version": _package_version(), "api_version": app.version}

    @app.get("/diagnostics", dependencies=[Depends(require_management_token)])
    def diagnostics() -> dict[str, Any]:
        return {
            "version": _package_version(),
            "python": {
                "version": platform.python_version(),
                "implementation": platform.python_implementation(),
                "executable": sys.executable,
            },
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
            },
            "database": {
                "path": str(db.path),
                "exists": db.path.exists(),
                "size_bytes": db.path.stat().st_size if db.path.exists() else None,
                "stats": db.stats(),
            },
            "auth_config": {
                "path": str(resolved_auth_path),
                "exists": resolved_auth_path.exists(),
            },
        }

    @app.get("/specs", dependencies=[Depends(require_management_token)])
    def specs(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        status: str | None = Query(None, pattern="^(ok|error)$"),
        provider_domain: str | None = None,
        q: str | None = Query(None, min_length=1, max_length=500),
    ) -> dict[str, Any]:
        items, total = db.list_specs(
            limit=limit,
            offset=offset,
            status=status,
            provider_domain=provider_domain,
            query=q,
        )
        items = [_sanitize_catalog_item(item) for item in items]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/specs/{spec_id}", dependencies=[Depends(require_management_token)])
    def spec(spec_id: str) -> dict[str, Any]:
        metadata = db.get_spec_metadata(spec_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="spec not found")
        stored = db.get_spec(spec_id)
        raw = stored.get("raw_json") if stored else {}
        return {
            **_sanitize_catalog_item(metadata),
            "documentation": _safe_spec_documentation(raw),
        }

    @app.post(
        "/specs/ingest-url",
        status_code=201,
        dependencies=[Depends(require_management_token)],
    )
    def spec_ingest_url(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        request = _validate_sensitive_payload(IngestUrlRequest, payload)
        try:
            return ingest_url(db, str(request.url), discovered_via="management_api")
        except Exception as exc:
            # Fetch/parser errors can contain a signed URL or document snippet.
            raise HTTPException(status_code=400, detail="spec URL ingestion failed") from exc

    @app.post(
        "/specs/ingest-file",
        status_code=201,
        dependencies=[Depends(require_management_token)],
    )
    def spec_ingest_file(request: IngestFileRequest) -> dict[str, Any]:
        file_path = Path(request.path).expanduser()
        if not file_path.is_file():
            raise HTTPException(status_code=400, detail="spec path is not a readable file")
        try:
            return ingest_file(db, file_path, discovered_via="management_api")
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail="spec file ingestion failed") from exc

    @app.post(
        "/specs/discover",
        dependencies=[Depends(require_management_token)],
    )
    def spec_discover(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        request = _validate_sensitive_payload(DiscoverRequest, payload)
        fetcher = Fetcher()
        try:
            candidates = discover_domain(
                request.domain,
                fetcher=fetcher,
                validate=True,
                max_candidates=10,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail="spec discovery failed") from exc

        results: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                results.append(
                    ingest_url(
                        db,
                        candidate.url,
                        fetcher=fetcher,
                        discovered_via=candidate.discovered_via,
                    )
                )
            except Exception:
                # Candidate URLs and fetch errors may contain credentials or signed query values.
                continue
        return {
            "candidates": len(candidates),
            "ingested": len(results),
            "results": results,
        }

    @app.delete("/specs/{spec_id}", dependencies=[Depends(require_management_token)])
    def spec_delete(spec_id: str) -> dict[str, Any]:
        deleted = db.delete_spec(spec_id)
        if deleted is None:
            raise HTTPException(status_code=404, detail="spec not found")
        return {"spec_id": spec_id, "deleted": True, **deleted}

    @app.get("/operations", dependencies=[Depends(require_management_token)])
    def operations(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        spec_id: str | None = None,
        provider_domain: str | None = None,
        method: str | None = Query(None, min_length=1, max_length=32),
        q: str | None = Query(None, min_length=1, max_length=500),
    ) -> dict[str, Any]:
        items, total = db.list_operations(
            limit=limit,
            offset=offset,
            spec_id=spec_id,
            provider_domain=provider_domain,
            method=method,
            query=q,
        )
        items = [_sanitize_catalog_item(item) for item in items]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/auth/profiles", dependencies=[Depends(require_management_token)])
    def auth_profiles() -> dict[str, Any]:
        try:
            result = list_auth_profiles(resolved_auth_path)
        except AuthConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result["profiles"].sort(key=lambda profile: profile["name"].lower())
        return result

    @app.get(
        "/auth/profiles/{name}",
        dependencies=[Depends(require_management_token)],
    )
    def auth_profile(name: str) -> dict[str, Any]:
        try:
            profile = load_auth_profiles(resolved_auth_path).get(name)
        except AuthConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if profile is None:
            raise HTTPException(status_code=404, detail="auth profile not found")
        return profile.safe_dict()

    @app.post(
        "/auth/profiles",
        status_code=201,
        dependencies=[Depends(require_management_token)],
    )
    def auth_profile_create(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        request = _validate_sensitive_payload(AuthProfileCreate, payload)
        metadata = request.to_storage_dict()
        name = metadata.pop("name")
        try:
            return upsert_auth_profile_metadata(
                name,
                metadata,
                path=resolved_auth_path,
                create_only=True,
            )
        except AuthConfigError as exc:
            status_code = 409 if "already exists" in str(exc) else 400
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @app.put(
        "/auth/profiles/{name}",
        dependencies=[Depends(require_management_token)],
    )
    def auth_profile_update(
        name: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        request = _validate_sensitive_payload(AuthProfileMetadata, payload)
        try:
            return upsert_auth_profile_metadata(
                name,
                request.to_storage_dict(),
                path=resolved_auth_path,
                update_only=True,
            )
        except AuthConfigError as exc:
            status_code = 404 if "not found" in str(exc) else 400
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @app.delete(
        "/auth/profiles/{name}",
        dependencies=[Depends(require_management_token)],
    )
    def auth_profile_delete(name: str) -> dict[str, Any]:
        try:
            deleted = delete_auth_profile(name, path=resolved_auth_path)
        except AuthConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if deleted is None:
            raise HTTPException(status_code=404, detail="auth profile not found")
        return {"deleted": True, "profile": deleted}

    @app.post("/search")
    def search(request: SearchRequest) -> dict[str, Any]:
        filters = SearchFilters(
            provider_domain=request.filters.get("provider_domain"),
            method=request.filters.get("method"),
            auth_required=request.filters.get("auth_required"),
        )
        return search_operations(
            db,
            request.query,
            limit=request.limit,
            filters=filters,
            token_budget=request.token_budget,
        )

    @app.get("/search")
    def search_get(
        q: str = Query(..., min_length=1),
        limit: int = Query(10, ge=1, le=50),
        method: str | None = None,
        provider_domain: str | None = None,
    ) -> dict[str, Any]:
        return search_operations(
            db,
            q,
            limit=limit,
            filters=SearchFilters(provider_domain=provider_domain, method=method),
        )

    @app.get("/operations/{operation_id}")
    def operation(operation_id: str) -> dict[str, Any]:
        op = db.get_operation(operation_id)
        if op is None:
            raise HTTPException(status_code=404, detail="operation not found")
        return op

    @app.get("/operations/{operation_id}/spec-slice")
    def operation_spec_slice(operation_id: str) -> dict[str, Any]:
        op = db.get_operation(operation_id)
        if op is None:
            raise HTTPException(status_code=404, detail="operation not found")
        return op.get("spec_slice") or {}

    @app.get("/operations/{operation_id}/call-template")
    def operation_call_template(operation_id: str) -> dict[str, Any]:
        op = db.get_operation(operation_id)
        if op is None:
            raise HTTPException(status_code=404, detail="operation not found")
        return build_call_template(op)

    @app.post("/operations/{operation_id}/prepare-call")
    def operation_prepare_call(operation_id: str, request: OperationCallRequest) -> dict[str, Any]:
        op = db.get_operation(operation_id)
        if op is None:
            raise HTTPException(status_code=404, detail="operation not found")
        try:
            return prepare_operation_call(
                op,
                auth_profiles=load_auth_profiles(request.auth_config),
                inputs=_call_inputs_from_request(request),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/operations/{operation_id}/execute-call")
    def operation_execute_call(operation_id: str, request: OperationCallRequest) -> dict[str, Any]:
        op = db.get_operation(operation_id)
        if op is None:
            raise HTTPException(status_code=404, detail="operation not found")
        try:
            return execute_operation_call(
                op,
                auth_profiles=load_auth_profiles(request.auth_config),
                inputs=_call_inputs_from_request(request),
                confirm=request.confirm,
                dry_run=request.dry_run,
                timeout=request.timeout,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


def _call_inputs_from_request(request: OperationCallRequest) -> CallInputs:
    return CallInputs(
        path_params=request.path_params,
        query_params=request.query_params,
        headers=request.headers,
        json_body=request.json_body,
        form_body=request.form_body,
        base_url=request.base_url,
        auth_profile=request.auth_profile,
    )


def _package_version() -> str:
    try:
        return importlib.metadata.version("mneme-server")
    except importlib.metadata.PackageNotFoundError:
        from mneme import __version__

        return __version__


def _safe_spec_documentation(raw: Any) -> dict[str, Any]:
    """Return a curated docs projection, excluding paths, components, and extensions."""

    if not isinstance(raw, dict):
        return {}
    info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
    safe_info = {
        key: info[key]
        for key in ("title", "summary", "description", "termsOfService")
        if isinstance(info.get(key), str)
    }
    for key in ("contact", "license"):
        value = info.get(key)
        if isinstance(value, dict):
            safe_info[key] = {
                item_key: item_value
                for item_key, item_value in value.items()
                if item_key in {"name", "url", "email", "identifier"}
                and isinstance(item_value, str)
            }
    servers = []
    for server in raw.get("servers") or []:
        if isinstance(server, dict) and isinstance(server.get("url"), str):
            servers.append(
                {
                    key: server[key]
                    for key in ("url", "description")
                    if isinstance(server.get(key), str)
                }
            )
    tags = []
    for tag in raw.get("tags") or []:
        if isinstance(tag, dict) and isinstance(tag.get("name"), str):
            tags.append(
                {key: tag[key] for key in ("name", "description") if isinstance(tag.get(key), str)}
            )
    result: dict[str, Any] = {"info": safe_info, "servers": servers, "tags": tags}
    external_docs = raw.get("externalDocs")
    if isinstance(external_docs, dict):
        result["external_docs"] = {
            key: external_docs[key]
            for key in ("url", "description")
            if isinstance(external_docs.get(key), str)
        }
    return result


def _sanitize_catalog_item(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    source_url = result.get("source_url")
    if isinstance(source_url, str):
        try:
            parsed = urlsplit(source_url)
            hostname = parsed.hostname or ""
            if ":" in hostname and not hostname.startswith("["):
                hostname = f"[{hostname}]"
            netloc = hostname
            if parsed.port is not None:
                netloc = f"{netloc}:{parsed.port}"
            result["source_url"] = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
        except ValueError:
            result["source_url"] = None
    return result


def _validate_sensitive_payload(
    model: (
        type[IngestUrlRequest]
        | type[DiscoverRequest]
        | type[AuthProfileCreate]
        | type[AuthProfileMetadata]
    ),
    payload: dict[str, Any],
) -> IngestUrlRequest | DiscoverRequest | AuthProfileCreate | AuthProfileMetadata:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        # Pydantic's default errors include the rejected input, which may be a secret.
        safe_errors = [
            {"type": error["type"], "loc": error["loc"], "msg": error["msg"]}
            for error in exc.errors()
        ]
        raise HTTPException(status_code=422, detail=safe_errors) from exc


app = create_app()
