"""Integration-level tests for the Kulshan CLI entry point."""
from __future__ import annotations

import click
from click.testing import CliRunner

from kulshan.cli import main


class TestMainGroup:
    """Verify the main Click group structure."""

    def test_main_is_click_group(self):
        assert isinstance(main, click.Group)

    def test_main_is_callable(self):
        assert callable(main)

    def test_shell_command_is_registered(self):
        assert "shell" in main.commands

    def test_setup_completion_command_is_registered(self):
        assert "setup-completion" in main.commands

    def test_report_command_is_registered(self):
        assert "report" in main.commands


class TestCLIRunner:
    """Verify CLI invocations via CliRunner."""

    def test_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert result.exception is None

    def test_help_output_contains_kulshan(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "Kulshan" in result.output

    def test_version_shows_0_1_0(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_setup_completion_bash(self):
        runner = CliRunner()
        result = runner.invoke(main, ["setup-completion", "--shell", "bash"])
        assert result.exit_code == 0
        assert "_KULSHAN_COMPLETE" in result.output
        assert "bash_source" in result.output

    def test_setup_completion_zsh(self):
        runner = CliRunner()
        result = runner.invoke(main, ["setup-completion", "--shell", "zsh"])
        assert result.exit_code == 0
        assert "_KULSHAN_COMPLETE" in result.output
        assert "zsh_source" in result.output

    def test_setup_completion_fish(self):
        runner = CliRunner()
        result = runner.invoke(main, ["setup-completion", "--shell", "fish"])
        assert result.exit_code == 0
        assert "_KULSHAN_COMPLETE" in result.output
        assert "fish_source" in result.output

    def test_setup_completion_powershell(self):
        runner = CliRunner()
        result = runner.invoke(main, ["setup-completion", "--shell", "powershell"])
        assert result.exit_code == 0
        assert "_KULSHAN_COMPLETE" in result.output
        assert "powershell_source" in result.output
        assert "Invoke-Expression" in result.output

    def test_shell_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["shell", "--help"])
        assert result.exit_code == 0
        assert "REPL" in result.output or "shell" in result.output.lower()

    def test_report_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["report", "--help"])
        assert result.exit_code == 0
        assert "--quick" in result.output
