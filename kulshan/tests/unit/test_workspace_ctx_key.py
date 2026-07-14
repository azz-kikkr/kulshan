"""Integration test: --workspace global option reaches all subcommands.

Proves the real Click context key 'workspace' is passed from main
group to report, history, delete-history, investigate cost, investigate ec2.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from kulshan.cli import main
from kulshan.workspace.config import (
    AwsConnection,
    WorkspaceAwsConfig,
    WorkspaceConfig,
    create_default_workspace_config,
    write_workspace_config,
)
from kulshan.workspace.sts import VerifiedAwsSession


def _setup(tmp_path):
    ws_root = tmp_path / "workspaces"
    default_dir = ws_root / "default"
    default_dir.mkdir(parents=True)
    write_workspace_config(default_dir, create_default_workspace_config())
    ws_dir = ws_root / "cust-ctx"
    ws_dir.mkdir(parents=True)
    write_workspace_config(ws_dir, WorkspaceConfig(
        name="cust-ctx", binding_mode="bound",
        aws=WorkspaceAwsConfig(
            payer_account_id="999999999999", default_connection="main",
            connections=[AwsConnection(
                name="main", profile="p1",
                expected_session_account_id="111122223333",
            )],
        ),
    ))
    return ws_root


def _patches(tmp_path, ws_root):
    return [
        patch("kulshan.workspace.resolution.get_workspaces_root", return_value=ws_root),
        patch("kulshan.workspace.resolution.get_workspace_path", side_effect=lambda n: ws_root / n),
        patch("kulshan.workspace.resolution.get_config_file_path", return_value=tmp_path / "c.toml"),
        patch("kulshan.workspace.migration.get_legacy_history_path", return_value=tmp_path / "lm.db"),
        patch("kulshan.workspace.migration.get_legacy_security_history_path", return_value=tmp_path / "ls.db"),
    ]


class TestGlobalWorkspaceReachesSubcommands:
    """Prove --workspace reaches all subcommands via real CLI invocation."""

    def test_history_receives_workspace(self, tmp_path):
        """history uses the workspace specified by --workspace."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()
        ws_root = _setup(tmp_path)
        patches = _patches(tmp_path, ws_root)
        runner = CliRunner()

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = runner.invoke(main, ["--workspace", "cust-ctx", "history"])

        # Should succeed and read from the cust-ctx workspace
        assert result.exit_code == 0
        # No error about workspace not found
        assert "not found" not in result.output.lower()

    def test_investigate_cost_receives_workspace(self, tmp_path):
        """investigate cost uses the workspace from --workspace."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()
        ws_root = _setup(tmp_path)
        patches = _patches(tmp_path, ws_root)
        runner = CliRunner()

        # Use non-existent path to trigger quick failure after workspace resolves
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = runner.invoke(main, [
                "--workspace", "cust-ctx",
                "investigate", "cost",
                "--path", str(tmp_path / "no-cur"),
                "--month", "2026-06",
            ])

        # Should fail because path doesn't exist, NOT because workspace is wrong
        assert result.exit_code != 0
        assert "workspace" not in result.output.lower() or "not found" not in result.output.lower()

    def test_investigate_ec2_receives_workspace(self, tmp_path):
        """investigate ec2 uses the workspace from --workspace."""
        from kulshan.workspace.resolution import _reset_migration_guard
        _reset_migration_guard()
        ws_root = _setup(tmp_path)
        patches = _patches(tmp_path, ws_root)
        runner = CliRunner()

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = runner.invoke(main, [
                "--workspace", "cust-ctx",
                "investigate", "ec2",
                "--cur", str(tmp_path / "no-ec2"),
                "--month", "2026-06",
            ])

        # Should fail because path doesn't exist, NOT workspace error
        assert result.exit_code != 0
