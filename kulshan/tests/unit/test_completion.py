"""Tests for the Kulshan.completion module."""
from __future__ import annotations

from unittest.mock import patch

import click

from kulshan.completion import (
    AWSProfileType,
    generate_completion_script,
    get_aws_profiles,
    make_setup_completion_command,
)


class TestGetAWSProfiles:
    """Verify AWS profile discovery from config files."""

    def test_returns_empty_list_when_no_aws_config(self, tmp_path):
        """When ~/.aws does not exist, return an empty list."""
        fake_home = tmp_path / "nohome"
        fake_home.mkdir()
        with patch("kulshan.completion.Path.home", return_value=fake_home):
            profiles = get_aws_profiles()
        assert profiles == []

    def test_returns_profiles_from_mock_config(self, mock_aws_config, tmp_path):
        """Profiles written by the mock_aws_config fixture are discovered."""
        with patch("kulshan.completion.Path.home", return_value=tmp_path):
            profiles = get_aws_profiles()

        assert "default" in profiles
        assert "dev" in profiles
        assert "prod" in profiles

    def test_returns_sorted_unique_profiles(self, mock_aws_config, tmp_path):
        with patch("kulshan.completion.Path.home", return_value=tmp_path):
            profiles = get_aws_profiles()

        assert profiles == sorted(set(profiles))

    def test_handles_config_only(self, tmp_path):
        """Works when only ~/.aws/config exists (no credentials file)."""
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir()
        config = aws_dir / "config"
        config.write_text("[default]\nregion=us-east-1\n\n[profile staging]\nregion=eu-west-1\n")

        with patch("kulshan.completion.Path.home", return_value=tmp_path):
            profiles = get_aws_profiles()

        assert "default" in profiles
        assert "staging" in profiles


class TestGenerateCompletionScript:
    """Verify completion script generation for each shell."""

    SHELLS = ["bash", "zsh", "fish", "powershell"]

    def test_bash_script_is_valid_string(self):
        script = generate_completion_script("Kulshan", "bash")
        assert isinstance(script, str)
        assert len(script) > 0

    def test_zsh_script_is_valid_string(self):
        script = generate_completion_script("Kulshan", "zsh")
        assert isinstance(script, str)
        assert len(script) > 0

    def test_fish_script_is_valid_string(self):
        script = generate_completion_script("Kulshan", "fish")
        assert isinstance(script, str)
        assert len(script) > 0

    def test_each_script_contains_complete_env_var(self):
        for shell in self.SHELLS:
            script = generate_completion_script("Kulshan", shell)
            assert "_KULSHAN_COMPLETE" in script, f"{shell} script missing env var"

    def test_each_script_contains_persistence_comment(self):
        for shell in self.SHELLS:
            script = generate_completion_script("Kulshan", shell)
            comment_lines = [line for line in script.splitlines() if line.startswith("#")]
            assert len(comment_lines) >= 1, f"{shell} script has no comment lines"

    def test_bash_script_uses_eval(self):
        script = generate_completion_script("Kulshan", "bash")
        assert "eval" in script

    def test_fish_script_uses_source(self):
        script = generate_completion_script("Kulshan", "fish")
        assert "source" in script

    def test_powershell_script_is_valid_string(self):
        script = generate_completion_script("Kulshan", "powershell")
        assert isinstance(script, str)
        assert len(script) > 0

    def test_powershell_script_contains_invoke_expression(self):
        script = generate_completion_script("Kulshan", "powershell")
        assert "Invoke-Expression" in script

    def test_powershell_script_uses_powershell_source(self):
        script = generate_completion_script("Kulshan", "powershell")
        assert "powershell_source" in script

    def test_powershell_script_mentions_profile(self):
        script = generate_completion_script("Kulshan", "powershell")
        assert "$PROFILE" in script


class TestAWSProfileType:
    """Verify the custom Click parameter type."""

    def test_convert_passes_through_value(self):
        param_type = AWSProfileType()
        result = param_type.convert("my-profile", param=None, ctx=None)
        assert result == "my-profile"

    def test_convert_passes_through_arbitrary_string(self):
        param_type = AWSProfileType()
        result = param_type.convert("some-random-value", param=None, ctx=None)
        assert result == "some-random-value"

    def test_name_attribute(self):
        param_type = AWSProfileType()
        assert param_type.name == "aws_profile"


class TestMakeSetupCompletionCommand:
    """Verify the setup-completion command factory."""

    def test_creates_click_command(self):
        cmd = make_setup_completion_command("Kulshan")
        assert isinstance(cmd, click.Command)

    def test_command_name_is_setup_completion(self):
        cmd = make_setup_completion_command("Kulshan")
        assert cmd.name == "setup-completion"

    def test_command_has_shell_option(self):
        cmd = make_setup_completion_command("Kulshan")
        param_names = [p.name for p in cmd.params]
        assert "shell_name" in param_names
