"""Isolated process entry point for blocking MCP operations."""

from __future__ import annotations

import json
import sys

from kulshan.mcp_server.tools import _execute_operation


def main() -> None:
    """Read one request from stdin and write one response to stdout."""
    try:
        request = json.loads(sys.stdin.read())
        payload = _execute_operation(request["operation"], request.get("arguments", {}))
        response = {"status": "ok", "payload": payload}
    except BaseException as exc:
        response = {"status": "error", "payload": str(exc)}
    sys.stdout.write(json.dumps(response))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
