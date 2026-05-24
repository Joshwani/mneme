from __future__ import annotations

from pathlib import Path
from typing import Any

from oas_atlas.fetch import Fetcher
from oas_atlas.index.db import AtlasDB
from oas_atlas.normalize.load import load_openapi_text
from oas_atlas.normalize.operations import make_spec_id, normalize_operations
from oas_atlas.util import sha256_hex, utc_now_iso

APIS_GURU_LIST_URL = "https://api.apis.guru/v2/list.json"


class IngestError(RuntimeError):
    pass


def ingest_text(
    db: AtlasDB,
    text: str,
    *,
    source_url: str | None,
    discovered_via: str | None = None,
) -> dict[str, Any]:
    fetched_at = utc_now_iso()
    content_hash = sha256_hex(text)
    try:
        spec = load_openapi_text(text)
        spec_meta, operations = normalize_operations(
            spec,
            source_url=source_url,
            content_hash=content_hash,
            fetched_at=fetched_at,
        )
    except Exception as exc:
        spec_id = make_spec_id(source_url, content_hash)
        if source_url:
            db.record_failed_spec(
                spec_id=spec_id,
                source_url=source_url,
                fetched_at=fetched_at,
                content_hash=content_hash,
                error=str(exc),
                discovered_via=discovered_via,
            )
        raise IngestError(str(exc)) from exc
    count = db.upsert_spec(
        spec_meta=spec_meta,
        raw_json=spec,
        operations=operations,
        discovered_via=discovered_via,
    )
    return {"spec_id": spec_meta["spec_id"], "operations": count, "title": spec_meta.get("title")}


def ingest_url(
    db: AtlasDB,
    url: str,
    *,
    fetcher: Fetcher | None = None,
    discovered_via: str | None = None,
) -> dict[str, Any]:
    fetcher = fetcher or Fetcher()
    fetched = fetcher.get_text(
        url,
        accept="application/vnd.oai.openapi+json, application/json, application/yaml, text/yaml, */*",
    )
    return ingest_text(
        db,
        fetched.text,
        source_url=fetched.final_url or url,
        discovered_via=discovered_via,
    )


def ingest_file(
    db: AtlasDB,
    path: str | Path,
    *,
    discovered_via: str | None = "local_file",
) -> dict[str, Any]:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    source_url = file_path.resolve().as_uri()
    return ingest_text(db, text, source_url=source_url, discovered_via=discovered_via)


def ingest_apis_guru(
    db: AtlasDB,
    *,
    fetcher: Fetcher | None = None,
    limit: int | None = None,
    continue_on_error: bool = True,
) -> dict[str, Any]:
    fetcher = fetcher or Fetcher(max_bytes=25_000_000)
    list_result = fetcher.get_text(APIS_GURU_LIST_URL, accept="application/json")
    import json

    data = json.loads(list_result.text)
    spec_urls = apis_guru_spec_urls(data)
    if limit is not None:
        spec_urls = spec_urls[:limit]
    ok = 0
    errors: list[dict[str, str]] = []
    operations = 0
    for url in spec_urls:
        try:
            result = ingest_url(db, url, fetcher=fetcher, discovered_via="apis_guru")
            ok += 1
            operations += int(result.get("operations", 0))
        except Exception as exc:
            errors.append({"url": url, "error": str(exc)})
            if not continue_on_error:
                raise
    return {
        "specs_attempted": len(spec_urls),
        "specs_ok": ok,
        "operations": operations,
        "errors": errors,
    }


def apis_guru_spec_urls(data: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for _api_name, api in data.items():
        if not isinstance(api, dict):
            continue
        versions = api.get("versions")
        preferred = api.get("preferred")
        candidates: list[dict[str, Any]] = []
        if isinstance(versions, dict):
            if preferred and isinstance(versions.get(preferred), dict):
                candidates.append(versions[preferred])
            candidates.extend(v for v in versions.values() if isinstance(v, dict))
        elif isinstance(api.get("swaggerUrl"), str) or isinstance(api.get("openapiYamlUrl"), str):
            candidates.append(api)
        for candidate in candidates:
            url = candidate.get("openapiYamlUrl") or candidate.get("swaggerUrl")
            if isinstance(url, str) and url not in urls:
                urls.append(url)
                break
    return urls
