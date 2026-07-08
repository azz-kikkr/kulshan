"""MCP stdio server for Kulshan tools."""

from __future__ import annotations


def run_server() -> None:
    """Run the Kulshan MCP server over stdio. Requires the mcp extra."""
    from kulshan.mcp_server.server import run_server as _run

    _run()


__all__ = ["run_server"]
