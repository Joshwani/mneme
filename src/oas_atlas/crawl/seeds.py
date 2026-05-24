from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


def read_seed_file(path: str | Path) -> list[str]:
    seeds: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        seeds.append(stripped)
    return seeds


def seed_looks_like_spec_url(seed: str) -> bool:
    parsed = urlparse(seed)
    path = parsed.path.lower()
    if parsed.scheme not in {"http", "https"}:
        return False
    return any(token in path for token in ["openapi", "swagger", "api-docs"]) or path.endswith(
        (".yaml", ".yml", ".json")
    )
