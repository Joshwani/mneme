from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from oas_atlas.auth import load_auth_profiles
from oas_atlas.call_template import build_call_template
from oas_atlas.http_client import CallInputs, execute_operation_call, prepare_operation_call
from oas_atlas.index.db import AtlasDB, default_db_path
from oas_atlas.index.search import SearchFilters, search_operations


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


def create_app(db_path: str | None = None) -> FastAPI:
    db_path = db_path or default_db_path()
    app = FastAPI(
        title="OAS Atlas",
        version="0.2.0",
        description="Agent-optimized search over OpenAPI-described operations.",
    )
    db = AtlasDB(db_path)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "stats": db.stats()}

    @app.get("/stats")
    def stats() -> dict[str, Any]:
        return db.stats()

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


app = create_app()
