from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from oas_atlas.index.db import AtlasDB, row_to_operation_dict
from oas_atlas.util import to_fts_query, tokenize_for_fts


@dataclass(slots=True)
class SearchFilters:
    provider_domain: str | None = None
    method: str | None = None
    auth_required: bool | None = None


def search_operations(
    db: AtlasDB,
    query: str,
    *,
    limit: int = 10,
    filters: SearchFilters | None = None,
    token_budget: int | None = None,
) -> dict[str, Any]:
    filters = filters or SearchFilters()
    fts_query = to_fts_query(query)
    if not fts_query:
        return {"query": query, "results": [], "stats": db.stats()}

    where = ["operations_fts MATCH ?"]
    params: list[Any] = [fts_query]
    joins = "JOIN operations o ON o.operation_id = operations_fts.operation_id"
    if filters.provider_domain:
        where.append("o.provider_domain = ?")
        params.append(filters.provider_domain)
    if filters.method:
        where.append("o.method = ?")
        params.append(filters.method.upper())

    sql = f"""
        SELECT o.*, bm25(operations_fts) AS bm25_score
        FROM operations_fts
        {joins}
        WHERE {" AND ".join(where)}
        ORDER BY bm25_score ASC, o.quality_score DESC
        LIMIT ?
    """
    params.append(max(1, min(limit * 4, 100)))
    rows = db.conn.execute(sql, params).fetchall()
    terms = tokenize_for_fts(query)
    results: list[dict[str, Any]] = []
    for row in rows:
        op = row_to_operation_dict(row)
        if filters.auth_required is not None:
            required = bool((op.get("auth") or {}).get("required"))
            if required != filters.auth_required:
                continue
        structural = _structural_bonus(op, terms)
        bm25_score = float(row["bm25_score"])
        score = (
            _score_from_bm25(bm25_score) + structural + (float(op.get("quality_score") or 0) * 0.1)
        )
        result = _to_search_result(op, score=round(score, 4), query_terms=terms)
        results.append(result)
    results.sort(key=lambda item: item["score"], reverse=True)
    results = results[:limit]
    if token_budget:
        results = _apply_token_budget(results, token_budget)
    return {"query": query, "results": results, "stats": db.stats()}


def _score_from_bm25(value: float) -> float:
    # FTS5 bm25 returns lower-is-better and commonly negative values.
    # Convert that into a higher-is-better bounded contribution.
    if value < 0:
        return max(0.0, min(1.0, -value))
    return max(0.0, min(1.0, 1.0 / (1.0 + value)))


def _structural_bonus(op: dict[str, Any], terms: list[str]) -> float:
    text_targets = [
        op.get("operation_id_native") or "",
        op.get("path") or "",
        op.get("summary") or "",
        " ".join(op.get("tags") or []),
    ]
    target = " ".join(text_targets).lower()
    bonus = 0.0
    for term in terms[:12]:
        if term in target:
            bonus += 0.025
    # Mutation verbs usually matter for tool intent.
    if any(t in terms for t in ["create", "send", "submit", "post", "add", "make"]):
        if op.get("method") == "POST":
            bonus += 0.08
    if any(t in terms for t in ["update", "modify", "patch"]):
        if op.get("method") in {"PUT", "PATCH"}:
            bonus += 0.08
    if any(t in terms for t in ["delete", "remove"]):
        if op.get("method") == "DELETE":
            bonus += 0.08
    if any(t in terms for t in ["list", "find", "search", "get", "retrieve"]):
        if op.get("method") == "GET":
            bonus += 0.05
    return min(0.4, bonus)


def _to_search_result(
    op: dict[str, Any], *, score: float, query_terms: list[str]
) -> dict[str, Any]:
    request_body = op.get("request_body") or {}
    required_inputs = []
    for p in op.get("parameters") or []:
        if p.get("required"):
            required_inputs.append(p.get("name"))
    schema = request_body.get("schema") if isinstance(request_body, dict) else None
    if isinstance(schema, dict):
        required_inputs.extend(schema.get("required") or [])
    required_inputs = [str(x) for x in dict.fromkeys(required_inputs).keys() if x]

    why = op.get("summary") or op.get("description") or op.get("agent_text", "").split("\n", 1)[0]
    if why and len(why) > 360:
        why = why[:357] + "..."
    return {
        "operation_id": op["operation_id"],
        "score": score,
        "api_title": op.get("api_title"),
        "api_version": op.get("api_version"),
        "provider_domain": op.get("provider_domain"),
        "method": op.get("method"),
        "path": op.get("path"),
        "summary": op.get("summary"),
        "why_relevant": why,
        "auth_required": bool((op.get("auth") or {}).get("required")),
        "auth": op.get("auth"),
        "required_inputs": required_inputs[:30],
        "source_url": op.get("source_url"),
        "quality_score": op.get("quality_score"),
        "agent_text": op.get("agent_text"),
        "links": {
            "operation": f"/operations/{op['operation_id']}",
            "spec_slice": f"/operations/{op['operation_id']}/spec-slice",
            "call_template": f"/operations/{op['operation_id']}/call-template",
        },
    }


def _apply_token_budget(results: list[dict[str, Any]], token_budget: int) -> list[dict[str, Any]]:
    # Approximate 4 chars/token. Trim agent_text first, then stop adding results.
    char_budget = max(500, token_budget * 4)
    used = 0
    trimmed: list[dict[str, Any]] = []
    for result in results:
        item = dict(result)
        text = str(item.get("agent_text") or "")
        item_chars = len(str(item))
        if item_chars > char_budget // 2 and text:
            max_text = max(200, char_budget // 5)
            item["agent_text"] = text[:max_text] + "..."
            item_chars = len(str(item))
        if used + item_chars > char_budget and trimmed:
            break
        trimmed.append(item)
        used += item_chars
    return trimmed
