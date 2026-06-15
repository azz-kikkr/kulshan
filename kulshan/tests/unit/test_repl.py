"""Tests for the Kulshan.repl module."""
from __future__ import annotations

from pathlib import Path

import click

from kulshan.repl import (
    ClickCompleter,
    detect_unicode_support,
    get_history_path,
    inject_context_args,
    make_prompt_text,
    truncate_history,
)


class TestDetectUnicodeSupport:
    """Verify unicode detection returns a boolean."""

    def test_returns_bool(self):
        result = detect_unicode_support()
        assert isinstance(result, bool)


class TestGetHistoryPath:
    """Verify history path is a Path object."""

    def test_returns_path_object(self):
        result = get_history_path()
        assert isinstance(result, Path)

    def test_path_ends_with_repl_history(self):
        result = get_history_path()
        assert result.name == "repl_history"


class TestMakePromptText:
    """Verify prompt text content based on unicode support."""

    def test_unicode_true_contains_mountain(self):
        prompt = make_prompt_text(supports_unicode=True)
        # HTML object has a value attribute or can be converted to string
        prompt_str = str(prompt)
        assert "\U0001f984" in prompt_str

    def test_unicode_false_does_not_contain_mountain(self):
        prompt = make_prompt_text(supports_unicode=False)
        prompt_str = str(prompt)
        assert "\U0001f984" not in prompt_str

    def test_prompt_contains_Kulshan(self):
        prompt = make_prompt_text(supports_unicode=True)
        prompt_str = str(prompt)
        assert "kulshan" in prompt_str.lower()


class TestInjectContextArgs:
    """Verify profile/role-arn injection into argument lists."""

    def test_prepends_profile_when_not_present(self):
        result = inject_context_args(["scan"], profile="dev", role_arn=None)
        assert result == ["--profile", "dev", "scan"]

    def test_skips_profile_when_already_in_args(self):
        result = inject_context_args(
            ["--profile", "prod", "scan"], profile="dev", role_arn=None
        )
        assert result == ["--profile", "prod", "scan"]

    def test_handles_both_profile_and_role_arn(self):
        result = inject_context_args(
            ["scan"], profile="dev", role_arn="arn:aws:iam::123:role/MyRole"
        )
        assert "--profile" in result
        assert "dev" in result
        assert "--role-arn" in result
        assert "arn:aws:iam::123:role/MyRole" in result
        # Original args should be at the end
        assert result[-1] == "scan"

    def test_none_profile_does_not_prepend(self):
        result = inject_context_args(["scan"], profile=None, role_arn=None)
        assert result == ["scan"]

    def test_none_role_arn_does_not_prepend(self):
        result = inject_context_args(["scan"], profile=None, role_arn=None)
        assert "--role-arn" not in result

    def test_empty_args_with_profile(self):
        result = inject_context_args([], profile="staging", role_arn=None)
        assert result == ["--profile", "staging"]

    def test_skips_role_arn_when_already_in_args(self):
        result = inject_context_args(
            ["--role-arn", "arn:existing", "scan"],
            profile=None,
            role_arn="arn:new",
        )
        assert result == ["--role-arn", "arn:existing", "scan"]


class TestTruncateHistory:
    """Verify history file truncation."""

    def test_nonexistent_file_does_not_crash(self, tmp_path):
        fake_path = tmp_path / "does_not_exist"
        truncate_history(fake_path, max_entries=10)
        # Should simply return without error

    def test_keeps_last_n_entries(self, tmp_path):
        history_file = tmp_path / "history"
        lines = [f"line {i}\n" for i in range(20)]
        history_file.write_text("".join(lines))

        truncate_history(history_file, max_entries=5)

        result = history_file.read_text().splitlines()
        assert len(result) == 5
        assert result[-1] == "line 19"
        assert result[0] == "line 15"

    def test_no_op_when_under_limit(self, tmp_path):
        history_file = tmp_path / "history"
        lines = [f"entry {i}\n" for i in range(3)]
        history_file.write_text("".join(lines))

        truncate_history(history_file, max_entries=10)

        result = history_file.read_text().splitlines()
        assert len(result) == 3


class TestClickCompleter:
    """Verify ClickCompleter instantiation."""

    def test_can_instantiate_with_click_group(self, simple_click_group):
        completer = ClickCompleter(simple_click_group)
        assert completer.cli_group is simple_click_group

    def test_stores_group_reference(self, nested_click_group):
        completer = ClickCompleter(nested_click_group)
        assert completer.cli_group is nested_click_group
