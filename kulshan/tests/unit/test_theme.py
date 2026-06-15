"""Tests for the Kulshan.theme module."""
from __future__ import annotations

from io import StringIO

from rich.console import Console

from kulshan.theme import THEME_REGISTRY, ToolTheme, get_theme, render_banner


class TestThemeRegistry:
    """Verify the THEME_REGISTRY contents."""

    EXPECTED_NAMES = {
        "kulshan",
        "cost",
        "sweep",
        "tag",
        "age",
        "dr",
        "pulse",
        "limit",
        "drift",
        "topo",
        "security",
    }

    def test_registry_has_all_11_themes(self):
        assert set(THEME_REGISTRY.keys()) == self.EXPECTED_NAMES

    def test_registry_length(self):
        assert len(THEME_REGISTRY) == 11

    def test_every_theme_accent_is_purple(self):
        for name, theme in THEME_REGISTRY.items():
            assert theme.accent == "purple", f"{name} accent is {theme.accent!r}, expected 'purple'"

    def test_every_theme_is_tool_theme_instance(self):
        for name, theme in THEME_REGISTRY.items():
            assert isinstance(theme, ToolTheme), f"{name} is not a ToolTheme"


class TestGetTheme:
    """Verify get_theme lookup and fallback behaviour."""

    def test_returns_correct_theme_for_known_name(self):
        theme = get_theme("cost")
        assert theme is THEME_REGISTRY["cost"]
        assert theme.primary == "cyan"

    def test_returns_theme_for_each_known_name(self):
        for name in THEME_REGISTRY:
            assert get_theme(name) is THEME_REGISTRY[name]

    def test_falls_back_to_kulshan_for_unknown_name(self):
        theme = get_theme("nonexistent-tool")
        assert theme is THEME_REGISTRY["kulshan"]

    def test_falls_back_to_kulshan_for_empty_string(self):
        theme = get_theme("")
        assert theme is THEME_REGISTRY["kulshan"]


class TestRenderBanner:
    """Verify render_banner produces output without crashing."""

    def test_render_banner_does_not_crash(self):
        buf = StringIO()
        console = Console(file=buf, force_terminal=True)
        # Monkey-patch Console() inside render_banner by calling it directly
        # Instead, we just call render_banner and let it print to real console;
        # the key assertion is that it does not raise.
        render_banner(
            tool_name="TestTool",
            tagline="A test tagline",
            version="0.0.1",
            theme=get_theme("kulshan"),
        )

    def test_render_banner_output_contains_tool_name(self, capsys):
        render_banner(
            tool_name="CostAnalyzer",
            tagline="Analyze costs",
            version="2.0.0",
            theme=get_theme("cost"),
        )
        captured = capsys.readouterr().out
        assert "CostAnalyzer" in captured

    def test_render_banner_output_contains_mountain_emoji(self, capsys):
        render_banner(
            tool_name="SweepTool",
            tagline="Sweep unused resources",
            version="0.1.0",
            theme=get_theme("sweep"),
        )
        captured = capsys.readouterr().out
        assert "\U0001f984" in captured

    def test_render_banner_output_contains_version(self, capsys):
        render_banner(
            tool_name="MyTool",
            tagline="Does things",
            version="3.5.7",
            theme=get_theme("kulshan"),
        )
        captured = capsys.readouterr().out
        assert "3.5.7" in captured
