from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def utc_now_iso_us() -> str:
    """ISO 8601 UTC timestamp with microsecond resolution.

    Used by features (notes, workspace) where many writes can happen within
    a single second and stable recency ordering matters.
    """

    return datetime.now(timezone.utc).isoformat()


def sha256_hex(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def stable_id(prefix: str, value: bytes | str, length: int = 20) -> str:
    return f"{prefix}_{sha256_hex(value)[:length]}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def pretty_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False)


def provider_domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.netloc:
        return parsed.netloc.lower()
    return None


def normalize_domain_or_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("empty domain/url")
    parsed = urlparse(value)
    if parsed.scheme:
        return value.rstrip("/")
    return f"https://{value.strip('/')}"


def absolute_url(base_url: str, maybe_url: str) -> str:
    return urljoin(base_url, maybe_url)


def json_dumps_compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def json_loads_maybe(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def tokenize_for_fts(query: str, max_terms: int = 24) -> list[str]:
    # Keep alphanumeric-ish terms. FTS5 MATCH strings are easy to break with punctuation,
    # so we never pass user text directly to MATCH.
    seen: set[str] = set()
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z0-9_]{2,}", query.lower()):
        if token not in seen:
            seen.add(token)
            terms.append(token)
        if len(terms) >= max_terms:
            break
    return terms


def to_fts_query(query: str) -> str:
    terms = tokenize_for_fts(query)
    # OR is intentionally recall-heavy. The trailing ``*`` enables FTS5 prefix
    # matching so "greet" finds "greet", "greeting", "greetings". Agents can
    # rerank or request larger top-k.
    return " OR ".join(f'"{term}"*' for term in terms)
