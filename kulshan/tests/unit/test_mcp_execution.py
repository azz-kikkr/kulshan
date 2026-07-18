"""Regression tests for bounded MCP execution and Windows console output."""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from kulshan import console_compat
from kulshan.mcp_server import tools


class FakeProcess:
    def __init__(self, stdout="", stderr="", returncode=0, timeout=False):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.timeout = timeout
        self.terminated = False
        self.killed = False
        self.calls = 0

    def communicate(self, request=None, timeout=None):
        self.calls += 1
        if self.timeout and self.calls == 1:
            raise subprocess.TimeoutExpired("worker", timeout)
        return self.stdout, self.stderr

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def patch_process(monkeypatch, process):
    def fake_popen(command, **kwargs):
        assert command[-2:] == ["-m", "kulshan.mcp_server.worker"]
        assert kwargs["stdin"] is subprocess.PIPE
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
        return process

    monkeypatch.setattr(tools.subprocess, "Popen", fake_popen)


def test_isolated_tool_returns_payload(monkeypatch):
    response = json.dumps({"status": "ok", "payload": '{"status":"ok"}'})
    process = FakeProcess(stdout=response)
    patch_process(monkeypatch, process)

    result = tools._run_isolated("preflight", {}, 5, "refresh credentials")

    assert result == '{"status":"ok"}'


def test_isolated_tool_raises_protocol_error_for_domain_failure(monkeypatch):
    response = json.dumps({"status": "error", "payload": "CUR path missing"})
    process = FakeProcess(stdout=response)
    patch_process(monkeypatch, process)

    with pytest.raises(ToolError, match="CUR path missing"):
        tools._run_isolated("cur_validate", {}, 5, "check the path")


def test_isolated_tool_terminates_on_timeout(monkeypatch):
    process = FakeProcess(timeout=True)
    patch_process(monkeypatch, process)

    with pytest.raises(ToolError, match="timed out after 3 seconds"):
        tools._run_isolated("report", {}, 3, "reduce packs")

    assert process.terminated


def test_isolated_tool_rejects_invalid_worker_json(monkeypatch):
    process = FakeProcess(stdout="not-json")
    patch_process(monkeypatch, process)

    with pytest.raises(ToolError, match="invalid worker response"):
        tools._run_isolated("report", {}, 3, "reduce packs")


def test_parse_packs_uses_tool_error():
    with pytest.raises(ToolError, match="Unknown pack"):
        tools._parse_packs("cost,unknown")


def test_windows_streams_are_reconfigured_to_utf8(monkeypatch):
    stdout = Mock()
    stderr = Mock()
    monkeypatch.setattr(sys, "platform", "win32")

    console_compat.configure_windows_utf8(stdout, stderr)

    stdout.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
    stderr.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")


def test_non_windows_streams_are_unchanged(monkeypatch):
    stdout = Mock()
    stderr = Mock()
    monkeypatch.setattr(sys, "platform", "linux")

    console_compat.configure_windows_utf8(stdout, stderr)

    stdout.reconfigure.assert_not_called()
    stderr.reconfigure.assert_not_called()


def test_all_unavailable_packs_raise_domain_error():
    results = {
        "cost": {"skipped": True, "errors": ["credentials missing"]},
        "security": {"skipped": True, "errors": ["access denied"]},
    }

    with pytest.raises(RuntimeError, match="All requested packs were unavailable"):
        tools._raise_if_all_packs_unavailable(results)


def test_partial_pack_results_remain_usable():
    results = {
        "cost": {"skipped": True, "errors": ["unavailable"]},
        "security": {"skipped": False, "findings": []},
    }

    tools._raise_if_all_packs_unavailable(results)
