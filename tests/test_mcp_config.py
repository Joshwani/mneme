from mneme.mcp_config import render


def test_claude_config_includes_catalog_discovery_fallback(tmp_path):
    rendered = render(client="claude", db_path=str(tmp_path / "mneme.db"))

    assert "If your client does not apply MCP server instructions" in rendered
    assert "Use search_callables or search_operations" in rendered
    assert '"mcpServers"' in rendered
