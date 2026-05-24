from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from mneme.fetch import Fetcher
from mneme.normalize.load import is_openapi_document, load_openapi_text
from mneme.util import absolute_url, normalize_domain_or_url

COMMON_SPEC_PATHS = [
    "/openapi.json",
    "/openapi.yaml",
    "/swagger.json",
    "/swagger.yaml",
    "/api/openapi.json",
    "/api/openapi.yaml",
    "/api/swagger.json",
    "/api/swagger.yaml",
    "/v3/api-docs",
    "/api-docs",
    "/docs/openapi.json",
    "/docs/openapi.yaml",
    "/swagger/v1/swagger.json",
]

COMMON_DOC_PATHS = [
    "/docs",
    "/api",
    "/developers",
    "/developer",
    "/swagger",
    "/swagger-ui",
    "/redoc",
]

SPECISH_RE = re.compile(
    r"(?P<url>(?:https?://[^\s'\"<>\\]+|/[A-Za-z0-9_./~?=&:%+-]+)(?:openapi|swagger|api-docs)[^\s'\"<>\\]*)",
    re.IGNORECASE,
)
SWAGGER_UI_URL_RE = re.compile(r"\burl\s*:\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
SWAGGER_UI_URLS_RE = re.compile(r"\burl\s*:\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
LINK_HEADER_RE = re.compile(r"<([^>]+)>\s*;\s*rel=\"?([^\";,]+)\"?", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SpecCandidate:
    url: str
    discovered_via: str
    source_url: str | None = None
    confidence: float = 0.5


def discover_domain(
    domain_or_url: str,
    *,
    fetcher: Fetcher | None = None,
    validate: bool = True,
    max_candidates: int = 50,
) -> list[SpecCandidate]:
    """Discover candidate OpenAPI descriptions for a domain.

    The function intentionally stays within a single domain and checks only common,
    intentional publication surfaces. It does not do broad internet scanning.
    """
    fetcher = fetcher or Fetcher()
    base_url = normalize_domain_or_url(domain_or_url)
    candidates: list[SpecCandidate] = []

    # Root Link headers and HTML hints.
    root = _safe_fetch(fetcher, base_url)
    if root:
        candidates.extend(_candidates_from_link_header(root.headers.get("link"), root.final_url))
        candidates.extend(
            _extract_spec_urls_from_text(root.text, root.final_url, "root_html", confidence=0.55)
        )

    # RFC 9727 api catalog and APIs.json.
    for path, via, confidence in [
        ("/.well-known/api-catalog", "well_known_api_catalog", 0.95),
        ("/apis.json", "apis_json", 0.90),
    ]:
        url = urljoin(base_url + "/", path.lstrip("/"))
        result = _safe_fetch(fetcher, url)
        if result:
            candidates.extend(
                _extract_catalog_candidates(result.text, result.final_url, via, confidence)
            )
            candidates.extend(
                _extract_spec_urls_from_text(result.text, result.final_url, via, confidence)
            )
            if _looks_like_openapi(result.text):
                candidates.append(
                    SpecCandidate(result.final_url, via, result.final_url, confidence)
                )

    # Common direct paths.
    for path in COMMON_SPEC_PATHS:
        url = urljoin(base_url + "/", path.lstrip("/"))
        candidates.append(SpecCandidate(url, "common_path", base_url, 0.45))

    # Docs pages often embed Swagger UI or Redoc config.
    for path in COMMON_DOC_PATHS:
        url = urljoin(base_url + "/", path.lstrip("/"))
        result = _safe_fetch(fetcher, url)
        if result:
            candidates.extend(
                _extract_spec_urls_from_text(result.text, result.final_url, "docs_page", 0.65)
            )
            candidates.extend(
                _candidates_from_link_header(result.headers.get("link"), result.final_url)
            )

    candidates = _dedupe_candidates(candidates)
    candidates = _same_origin_or_absolute(candidates, base_url)
    candidates = sorted(candidates, key=lambda c: c.confidence, reverse=True)[:max_candidates]

    if validate:
        candidates = validate_candidates(candidates, fetcher=fetcher)
    return candidates


def validate_candidates(
    candidates: Iterable[SpecCandidate],
    *,
    fetcher: Fetcher | None = None,
) -> list[SpecCandidate]:
    fetcher = fetcher or Fetcher()
    valid: list[SpecCandidate] = []
    for candidate in candidates:
        result = _safe_fetch(fetcher, candidate.url)
        if not result:
            continue
        try:
            doc = load_openapi_text(result.text)
        except Exception:
            continue
        if is_openapi_document(doc):
            valid.append(
                SpecCandidate(
                    result.final_url,
                    candidate.discovered_via,
                    candidate.source_url or candidate.url,
                    candidate.confidence,
                )
            )
    return _dedupe_candidates(valid)


def _safe_fetch(fetcher: Fetcher, url: str):
    try:
        return fetcher.get_text(url, accept="application/json, application/yaml, text/html, */*")
    except Exception:
        return None


def _candidates_from_link_header(link_header: str | None, base_url: str) -> list[SpecCandidate]:
    if not link_header:
        return []
    out: list[SpecCandidate] = []
    for href, rel in LINK_HEADER_RE.findall(link_header):
        rel = rel.lower()
        if rel in {"api-catalog", "service-desc"}:
            out.append(
                SpecCandidate(absolute_url(base_url, href), f"link_rel_{rel}", base_url, 0.95)
            )
    return out


def _extract_catalog_candidates(
    text: str,
    base_url: str,
    via: str,
    confidence: float,
) -> list[SpecCandidate]:
    # This intentionally supports APIs.json, Linkset JSON, and loose catalog-like JSON.
    try:
        data = json.loads(text)
    except Exception:
        return []
    out: list[SpecCandidate] = []

    def add(value: str, bump: float = 0.0) -> None:
        if _url_looks_specish(value):
            out.append(
                SpecCandidate(
                    absolute_url(base_url, value), via, base_url, min(1.0, confidence + bump)
                )
            )

    if isinstance(data, dict):
        # APIs.json convention.
        for api in data.get("apis", []) if isinstance(data.get("apis"), list) else []:
            if not isinstance(api, dict):
                continue
            for prop in (
                api.get("properties", []) if isinstance(api.get("properties"), list) else []
            ):
                if not isinstance(prop, dict):
                    continue
                ptype = str(prop.get("type") or prop.get("name") or "").lower()
                url = prop.get("url")
                if isinstance(url, str) and any(x in ptype for x in ["openapi", "swagger", "oas"]):
                    out.append(
                        SpecCandidate(
                            absolute_url(base_url, url), via, base_url, min(1.0, confidence + 0.05)
                        )
                    )

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            href = value.get("href") or value.get("url")
            rel = str(value.get("rel") or value.get("type") or value.get("name") or "").lower()
            if isinstance(href, str) and (
                _url_looks_specish(href)
                or any(x in rel for x in ["openapi", "swagger", "service-desc"])
            ):
                out.append(SpecCandidate(absolute_url(base_url, href), via, base_url, confidence))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            add(value)

    walk(data)
    return _dedupe_candidates(out)


def _extract_spec_urls_from_text(
    text: str,
    base_url: str,
    via: str,
    confidence: float = 0.5,
) -> list[SpecCandidate]:
    out: list[SpecCandidate] = []
    for match in SPECISH_RE.finditer(text):
        url = match.group("url").rstrip(",.)]")
        out.append(SpecCandidate(absolute_url(base_url, url), via, base_url, confidence))

    for match in SWAGGER_UI_URL_RE.finditer(text):
        url = match.group(1)
        if _url_looks_specish(url):
            out.append(SpecCandidate(absolute_url(base_url, url), via, base_url, confidence + 0.05))

    try:
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup.find_all(["a", "link", "script", "redoc"]):
            for attr in ("href", "src", "spec-url", "data-spec-url"):
                value = tag.get(attr)
                if isinstance(value, str) and _url_looks_specish(value):
                    out.append(
                        SpecCandidate(
                            absolute_url(base_url, value), via, base_url, confidence + 0.05
                        )
                    )
    except Exception:
        pass
    return _dedupe_candidates(out)


def _looks_like_openapi(text: str) -> bool:
    stripped = text.lstrip()[:2000].lower()
    return ("openapi" in stripped or "swagger" in stripped) and "paths" in stripped


def _url_looks_specish(url: str) -> bool:
    lowered = url.lower()
    return any(
        token in lowered for token in ["openapi", "swagger", "api-docs"]
    ) and not lowered.endswith((".js", ".css", ".png", ".jpg", ".svg"))


def _dedupe_candidates(candidates: Iterable[SpecCandidate]) -> list[SpecCandidate]:
    best: dict[str, SpecCandidate] = {}
    for candidate in candidates:
        url = candidate.url.strip()
        if not url:
            continue
        current = best.get(url)
        if current is None or candidate.confidence > current.confidence:
            best[url] = candidate
    return list(best.values())


def _same_origin_or_absolute(
    candidates: Iterable[SpecCandidate], base_url: str
) -> list[SpecCandidate]:
    base_host = urlparse(base_url).netloc.lower()
    out: list[SpecCandidate] = []
    for candidate in candidates:
        parsed = urlparse(candidate.url)
        if parsed.scheme not in {"http", "https"}:
            continue
        # Keep same-origin and absolute offsite links. We do not recursively crawl offsite;
        # offsite URLs are common when specs are hosted on CDN/GitHub.
        if parsed.netloc:
            out.append(candidate)
        elif not parsed.netloc and base_host:
            out.append(candidate)
    return out
