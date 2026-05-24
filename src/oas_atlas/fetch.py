from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import httpx

DEFAULT_USER_AGENT = "oas-atlas/0.1 (+https://github.com/example/oas-atlas)"


@dataclass(slots=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    headers: Mapping[str, str]
    text: str
    content_type: str | None = None


class FetchError(RuntimeError):
    pass


class Fetcher:
    """Small HTTP client with crawl-friendly defaults.

    This is deliberately conservative: short timeout, bounded response size, no cookies,
    and a clear User-Agent. The MVP should crawl intentionally public API description
    surfaces, not perform broad reconnaissance.
    """

    def __init__(
        self,
        *,
        timeout: float = 12.0,
        max_bytes: int = 5_000_000,
        user_agent: str = DEFAULT_USER_AGENT,
        follow_redirects: bool = True,
    ) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self.follow_redirects = follow_redirects

    def get_text(self, url: str, *, accept: str | None = None) -> FetchResult:
        headers = {"User-Agent": self.user_agent}
        if accept:
            headers["Accept"] = accept
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=self.follow_redirects,
                headers=headers,
            ) as client:
                response = client.get(url)
        except httpx.HTTPError as exc:
            raise FetchError(f"failed to fetch {url}: {exc}") from exc

        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > self.max_bytes:
            raise FetchError(f"response too large for {url}: {content_length} bytes")
        if len(response.content) > self.max_bytes:
            raise FetchError(f"response too large for {url}: {len(response.content)} bytes")
        if response.status_code >= 400:
            raise FetchError(f"HTTP {response.status_code} for {url}")

        encoding = response.encoding or "utf-8"
        try:
            text = response.content.decode(encoding, errors="replace")
        except LookupError:
            text = response.text
        return FetchResult(
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            headers=response.headers,
            text=text,
            content_type=response.headers.get("content-type"),
        )
