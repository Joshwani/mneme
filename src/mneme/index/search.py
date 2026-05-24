from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mneme.index.db import MnemeDB, row_to_library_symbol_dict, row_to_operation_dict
from mneme.util import to_fts_query, tokenize_for_fts


# Callable kinds. Used as the ``kind`` field in unified search results and as
# filters for ``search_callables``.
KIND_HTTP_OPERATION = "http_operation"
KIND_PYLIB_SYMBOL = "pylib_symbol"
KIND_JSLIB_SYMBOL = "jslib_symbol"
ALL_KINDS = (KIND_HTTP_OPERATION, KIND_PYLIB_SYMBOL, KIND_JSLIB_SYMBOL)


@dataclass(slots=True)
class SearchFilters:
    provider_domain: str | None = None
    method: str | None = None
    auth_required: bool | None = None


def search_operations(
    db: MnemeDB,
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
        "kind": KIND_HTTP_OPERATION,
        "callable_id": op["operation_id"],
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


@dataclass(slots=True)
class CallableFilters:
    """Filters for the unified callable search.

    ``kinds`` restricts which callable kinds are returned. ``language`` and
    ``package_name`` only apply to library symbol kinds; ``provider_domain``
    and ``method`` only apply to HTTP operations.
    """

    kinds: tuple[str, ...] | None = None
    provider_domain: str | None = None
    method: str | None = None
    auth_required: bool | None = None
    language: str | None = None
    package_name: str | None = None


def search_callables(
    db: MnemeDB,
    query: str,
    *,
    limit: int = 10,
    filters: CallableFilters | None = None,
    token_budget: int | None = None,
) -> dict[str, Any]:
    """Search across HTTP operations and library symbols, merged by BM25 rank."""

    filters = filters or CallableFilters()
    kinds = filters.kinds or ALL_KINDS
    # The ``language`` and ``package_name`` filters only apply to library symbols,
    # so setting them implies excluding HTTP operations.
    if filters.language or filters.package_name:
        kinds = tuple(k for k in kinds if k != KIND_HTTP_OPERATION)
    fts_query = to_fts_query(query)
    if not fts_query:
        return {"query": query, "results": [], "stats": db.stats()}
    terms = tokenize_for_fts(query)

    pool: list[dict[str, Any]] = []

    if KIND_HTTP_OPERATION in kinds:
        op_filters = SearchFilters(
            provider_domain=filters.provider_domain,
            method=filters.method,
            auth_required=filters.auth_required,
        )
        op_results = _http_search_pool(db, fts_query, op_filters, terms, limit)
        pool.extend(op_results)

    library_kinds = [k for k in kinds if k != KIND_HTTP_OPERATION]
    if library_kinds:
        lib_results = _library_search_pool(
            db,
            fts_query,
            terms,
            limit,
            kinds=tuple(library_kinds),
            language=filters.language,
            package_name=filters.package_name,
        )
        pool.extend(lib_results)

    pool.sort(key=lambda item: item["score"], reverse=True)
    results = pool[:limit]
    if token_budget:
        results = _apply_token_budget(results, token_budget)
    return {"query": query, "results": results, "stats": db.stats()}


def _http_search_pool(
    db: MnemeDB,
    fts_query: str,
    filters: SearchFilters,
    terms: list[str],
    limit: int,
) -> list[dict[str, Any]]:
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
    out: list[dict[str, Any]] = []
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
        out.append(result)
    return out


def _library_search_pool(
    db: MnemeDB,
    fts_query: str,
    terms: list[str],
    limit: int,
    *,
    kinds: tuple[str, ...],
    language: str | None,
    package_name: str | None,
) -> list[dict[str, Any]]:
    where = ["library_symbols_fts MATCH ?"]
    params: list[Any] = [fts_query]
    joins = "JOIN library_symbols s ON s.symbol_id = library_symbols_fts.symbol_id"

    languages: set[str] = set()
    if KIND_PYLIB_SYMBOL in kinds:
        languages.add("python")
    if KIND_JSLIB_SYMBOL in kinds:
        languages.update({"typescript", "javascript"})
    if not languages:
        return []
    if language:
        languages = {language}

    placeholders = ", ".join(["?"] * len(languages))
    where.append(f"s.language IN ({placeholders})")
    params.extend(sorted(languages))

    if package_name:
        where.append("s.package_name = ?")
        params.append(package_name)

    sql = f"""
        SELECT s.*, bm25(library_symbols_fts) AS bm25_score
        FROM library_symbols_fts
        {joins}
        WHERE {" AND ".join(where)}
        ORDER BY bm25_score ASC, s.quality_score DESC
        LIMIT ?
    """
    params.append(max(1, min(limit * 4, 100)))
    rows = db.conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        sym = row_to_library_symbol_dict(row)
        structural = _library_structural_bonus(sym, terms)
        bm25_score = float(row["bm25_score"])
        score = (
            _score_from_bm25(bm25_score) + structural + (float(sym.get("quality_score") or 0) * 0.1)
        )
        out.append(_to_library_result(sym, score=round(score, 4)))
    return out


def _library_structural_bonus(sym: dict[str, Any], terms: list[str]) -> float:
    targets = [
        sym.get("symbol_name") or "",
        sym.get("qualified_name") or "",
        sym.get("module_path") or "",
        sym.get("summary") or "",
        " ".join(sym.get("tags") or []),
    ]
    target = " ".join(targets).lower()
    bonus = 0.0
    for term in terms[:12]:
        if term in target:
            bonus += 0.02
    if sym.get("symbol_name", "").lower() in (terms or []):
        bonus += 0.1
    return min(0.4, bonus)


def _to_library_result(sym: dict[str, Any], *, score: float) -> dict[str, Any]:
    kind = KIND_PYLIB_SYMBOL if sym.get("language") == "python" else KIND_JSLIB_SYMBOL
    why = (
        sym.get("summary") or sym.get("description") or sym.get("agent_text", "").split("\n", 1)[0]
    )
    if why and len(why) > 360:
        why = why[:357] + "..."
    return {
        "kind": kind,
        "callable_id": sym["symbol_id"],
        "symbol_id": sym["symbol_id"],
        "score": score,
        "language": sym.get("language"),
        "package_name": sym.get("package_name"),
        "module_path": sym.get("module_path"),
        "qualified_name": sym.get("qualified_name"),
        "symbol_kind": sym.get("kind"),
        "symbol_name": sym.get("symbol_name"),
        "signature": sym.get("signature"),
        "summary": sym.get("summary"),
        "why_relevant": why,
        "source_url": sym.get("source_url"),
        "quality_score": sym.get("quality_score"),
        "agent_text": sym.get("agent_text"),
        "links": {
            "symbol": f"/callables/{sym['symbol_id']}",
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
