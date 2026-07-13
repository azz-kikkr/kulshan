"""Stdio MCP server entry point for Kulshan."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from kulshan.mcp_server.tools import register_tools

DESCRIPTION = "Read-only AWS FinOps audit tools. Local-first, no writes, no telemetry."


def run_server() -> None:
    """Run the Kulshan MCP server over stdio."""
    mcp = FastMCP("kulshan", instructions=DESCRIPTION)
    register_tools(mcp)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
