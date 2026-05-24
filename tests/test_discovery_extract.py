from __future__ import annotations

from mneme.crawl.discover import _extract_spec_urls_from_text


def test_extract_swagger_ui_url():
    html = """
    <html><script>
    SwaggerUIBundle({ url: '/openapi.json' })
    </script></html>
    """
    candidates = _extract_spec_urls_from_text(html, "https://example.com/docs", "test")
    urls = {candidate.url for candidate in candidates}
    assert "https://example.com/openapi.json" in urls


def test_extract_redoc_spec_url():
    html = '<redoc spec-url="/swagger.yaml"></redoc>'
    candidates = _extract_spec_urls_from_text(html, "https://example.com/docs", "test")
    urls = {candidate.url for candidate in candidates}
    assert "https://example.com/swagger.yaml" in urls
