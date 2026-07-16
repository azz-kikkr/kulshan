"""End-to-end snapshot tests for ``kulshan report``.

Validates the report generation pipeline against the new cost-only
baseline product behavior. Mocks AWS boundaries so tests run offline.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from kulshan.cli import main
from kulshan.constants import ExitCode
from kulshan.orchestrator import TOOL_LABELS, TOOL_ORDER

_OK_EXITS = {int(ExitCode.SUCCESS), int(ExitCode.FINDING_FAIL)}

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "checks"
ACCOUNT_ID = "000000000000"
REGIONS = ["us-east-1", "us-west-2", "eu-west-1"]


def _load_all_fixtures() -> dict:
    results: dict = {}
    for tool_key in TOOL_ORDER:
        with (FIXTURES_DIR / f"{tool_key}.json").open(encoding="utf-8") as f:
            results[tool_key] = json.load(f)
    return results


@pytest.fixture
def fixture_results() -> dict:
    return _load_all_fixtures()


@pytest.fixture
def mocked_environment(fixture_results):
    """Patch AWS boundaries so report runs offline."""
    from kulshan.workspace.context import WorkspaceContext
    from kulshan.workspace.config import WorkspaceConfig

    # Create a fake unbound workspace context that skips onboarding
    fake_config = WorkspaceConfig(name="default", binding_mode="unbound")
    fake_ws = WorkspaceContext(
        name="default",
        path=Path.home() / ".kulshan-test-fake",
        config=fake_config,
        history_db_path=Path.home() / ".kulshan-test-fake" / "history.db",
        security_history_db_path=Path.home() / ".kulshan-test-fake" / "sec.db",
    )

    fake_exec = MagicMock()
    fake_exec.session = MagicMock()
    fake_exec.session_account_id = ACCOUNT_ID
    fake_exec.is_unbound = True

    with (
        patch("kulshan.session.create_session", return_value=MagicMock()),
        patch("kulshan.session.get_account_id", return_value=ACCOUNT_ID),
        patch("kulshan.session.get_enabled_regions", return_value=list(REGIONS)),
        patch("kulshan.orchestrator.run_all_scans", return_value=fixture_results),
        patch("kulshan.workspace.resolution.resolve_workspace", return_value=fake_ws),
        patch("kulshan.workspace.resolution.resolve_workspace_with_profile", return_value=fake_ws),
        patch("kulshan.workspace.execution.resolve_aws_execution", return_value=fake_exec),
    ):
        yield


@pytest.fixture
def cost_only_environment():
    """Only cost pack returns data; others are absent."""
    from kulshan.workspace.context import WorkspaceContext
    from kulshan.workspace.config import WorkspaceConfig

    cost_fixture = json.loads(
        (FIXTURES_DIR / "cost.json").read_text(encoding="utf-8")
    )
    results = {"cost": cost_fixture}

    fake_config = WorkspaceConfig(name="default", binding_mode="unbound")
    fake_ws = WorkspaceContext(
        name="default",
        path=Path.home() / ".kulshan-test-fake",
        config=fake_config,
        history_db_path=Path.home() / ".kulshan-test-fake" / "history.db",
        security_history_db_path=Path.home() / ".kulshan-test-fake" / "sec.db",
    )

    fake_exec = MagicMock()
    fake_exec.session = MagicMock()
    fake_exec.session_account_id = ACCOUNT_ID
    fake_exec.is_unbound = True

    with (
        patch("kulshan.session.create_session", return_value=MagicMock()),
        patch("kulshan.session.get_account_id", return_value=ACCOUNT_ID),
        patch("kulshan.session.get_enabled_regions", return_value=["us-east-1"]),
        patch("kulshan.orchestrator.run_all_scans", return_value=results),
        patch("kulshan.workspace.resolution.resolve_workspace", return_value=fake_ws),
        patch("kulshan.workspace.resolution.resolve_workspace_with_profile", return_value=fake_ws),
        patch("kulshan.workspace.execution.resolve_aws_execution", return_value=fake_exec),
    ):
        yield


# ═══════════════════════════════════════════════════════════════════════
# HTML REPORT GENERATION
# ═══════════════════════════════════════════════════════════════════════


class TestHTMLReportGeneration:
    """HTML report file is written and has correct structure."""

    def test_html_file_written(self, mocked_environment, tmp_path):
        out = tmp_path / "report.html"
        runner = CliRunner()
        result = runner.invoke(main, ["report", "--yes", "--format", "html", "--output", str(out)])
        assert result.exit_code in _OK_EXITS, f"exit={result.exit_code}, output={result.output!r}"
        assert out.exists()
        assert out.stat().st_size > 1000

    def test_html_contains_executive_summary(self, mocked_environment, tmp_path):
        out = tmp_path / "report.html"
        CliRunner().invoke(main, ["report", "--yes", "--format", "html", "--output", str(out)])
        html = out.read_text(encoding="utf-8")
        assert "Executive Summary" in html

    def test_html_contains_detailed_breakdown(self, mocked_environment, tmp_path):
        out = tmp_path / "report.html"
        CliRunner().invoke(main, ["report", "--yes", "--format", "html", "--output", str(out)])
        html = out.read_text(encoding="utf-8")
        assert "Detailed Breakdown" in html

    def test_html_contains_account_id(self, mocked_environment, tmp_path):
        out = tmp_path / "report.html"
        CliRunner().invoke(main, ["report", "--yes", "--format", "html", "--output", str(out), "--show-pii"])
        html = out.read_text(encoding="utf-8")
        assert ACCOUNT_ID in html

    def test_html_contains_what_to_do_next(self, mocked_environment, tmp_path):
        """Renamed from 'Top Actions' to 'What To Do Next'."""
        out = tmp_path / "report.html"
        CliRunner().invoke(main, ["report", "--yes", "--format", "html", "--output", str(out)])
        html = out.read_text(encoding="utf-8")
        assert "What To Do Next" in html

    def test_html_does_not_show_old_sections(self, mocked_environment, tmp_path):
        """Old removed sections should not appear as rendered headings."""
        out = tmp_path / "report.html"
        CliRunner().invoke(main, ["report", "--yes", "--format", "html", "--output", str(out)])
        html = out.read_text(encoding="utf-8")
        # These were rendered headings in the old report - now removed
        assert '>Overall Operations Score<' not in html
        assert '<h2 class="section-title">Tool Scores</h2>' not in html
        assert '<div class="tool-grid">' not in html


# ═══════════════════════════════════════════════════════════════════════
# COST-ONLY MODE
# ═══════════════════════════════════════════════════════════════════════


class TestCostOnlyReport:
    """Cost-only report shows only cost data, no N/A packs."""

    def test_html_does_not_show_unrun_packs(self, cost_only_environment, tmp_path):
        out = tmp_path / "report.html"
        CliRunner().invoke(main, ["report", "--yes", "--format", "html", "--output", str(out)])
        html = out.read_text(encoding="utf-8")
        # Should NOT have security/sweep/DR etc. in detailed breakdown
        assert "Security Scanner" not in html
        assert "Waste Detector" not in html
        assert "DR Readiness" not in html
        # Should have cost
        assert "Cost Analyzer" in html

    def test_terminal_shows_only_cost(self, cost_only_environment):
        runner = CliRunner()
        result = runner.invoke(main, ["report", "--yes", "--format", "terminal"])
        assert result.exit_code in _OK_EXITS
        assert "Cost Analyzer" in result.output
        # Should NOT show N/A rows for unrun packs
        assert "N/A" not in result.output

    def test_terminal_exits_cleanly(self, cost_only_environment):
        runner = CliRunner()
        result = runner.invoke(main, ["report", "--yes", "--format", "terminal"])
        assert result.exit_code in _OK_EXITS


# ═══════════════════════════════════════════════════════════════════════
# JSON REPORT
# ═══════════════════════════════════════════════════════════════════════


class TestJSONReport:
    """JSON report has valid top-level shape."""

    def test_json_file_written(self, mocked_environment, tmp_path):
        out = tmp_path / "report.json"
        runner = CliRunner()
        result = runner.invoke(main, ["report", "--yes", "--format", "json", "--output", str(out)])
        assert result.exit_code in _OK_EXITS
        assert out.exists()

    def test_json_top_level_shape(self, mocked_environment, tmp_path):
        out = tmp_path / "report.json"
        CliRunner().invoke(main, ["report", "--yes", "--format", "json", "--output", str(out)])
        data = json.loads(out.read_text(encoding="utf-8"))
        for key in ("kulshan_version", "account_id", "regions", "overall_score", "overall_grade", "tools"):
            assert key in data, f"Missing top-level key: {key}"

    def test_json_contains_findings_array(self, mocked_environment, tmp_path):
        out = tmp_path / "report.json"
        CliRunner().invoke(main, ["report", "--yes", "--format", "json", "--output", str(out)])
        data = json.loads(out.read_text(encoding="utf-8"))
        assert "findings" in data
        assert isinstance(data["findings"], list)

    def test_json_contains_top_actions(self, mocked_environment, tmp_path):
        out = tmp_path / "report.json"
        CliRunner().invoke(main, ["report", "--yes", "--format", "json", "--output", str(out)])
        data = json.loads(out.read_text(encoding="utf-8"))
        assert "top_actions" in data
        assert isinstance(data["top_actions"], list)


# ═══════════════════════════════════════════════════════════════════════
# TERMINAL REPORT
# ═══════════════════════════════════════════════════════════════════════


class TestTerminalReport:
    """Terminal report renders without crashing."""

    def test_terminal_exits_cleanly(self, mocked_environment):
        runner = CliRunner()
        result = runner.invoke(main, ["report", "--yes", "--format", "terminal"])
        assert result.exit_code in _OK_EXITS

    def test_terminal_shows_score(self, mocked_environment):
        runner = CliRunner()
        result = runner.invoke(main, ["report", "--yes", "--format", "terminal"])
        # Overall score line should be present
        assert "/100" in result.output


# ═══════════════════════════════════════════════════════════════════════
# NO FINDINGS SCENARIO
# ═══════════════════════════════════════════════════════════════════════


class TestNoFindings:
    """When no findings exist, report still renders cleanly."""

    @pytest.fixture
    def empty_environment(self):
        from kulshan.workspace.context import WorkspaceContext
        from kulshan.workspace.config import WorkspaceConfig

        empty_results = {
            "cost": {
                "tool": "cost",
                "scores": {
                    "overall_score": 80, "grade": "B-",
                    "total_findings": 0, "severity_counts": {},
                    "breakdown": {}, "total_spend": 5000.0,
                },
                "findings": [],
                "errors": [],
                "metadata": {},
            }
        }

        fake_config = WorkspaceConfig(name="default", binding_mode="unbound")
        fake_ws = WorkspaceContext(
            name="default",
            path=Path.home() / ".kulshan-test-fake",
            config=fake_config,
            history_db_path=Path.home() / ".kulshan-test-fake" / "history.db",
            security_history_db_path=Path.home() / ".kulshan-test-fake" / "sec.db",
        )

        fake_exec = MagicMock()
        fake_exec.session = MagicMock()
        fake_exec.session_account_id = ACCOUNT_ID
        fake_exec.is_unbound = True

        with (
            patch("kulshan.session.create_session", return_value=MagicMock()),
            patch("kulshan.session.get_account_id", return_value=ACCOUNT_ID),
            patch("kulshan.session.get_enabled_regions", return_value=["us-east-1"]),
            patch("kulshan.orchestrator.run_all_scans", return_value=empty_results),
            patch("kulshan.workspace.resolution.resolve_workspace", return_value=fake_ws),
            patch("kulshan.workspace.resolution.resolve_workspace_with_profile", return_value=fake_ws),
            patch("kulshan.workspace.execution.resolve_aws_execution", return_value=fake_exec),
        ):
            yield

    def test_html_renders_without_actions(self, empty_environment, tmp_path):
        out = tmp_path / "report.html"
        runner = CliRunner()
        result = runner.invoke(main, ["report", "--yes", "--format", "html", "--output", str(out)])
        assert result.exit_code == 0
        html = out.read_text(encoding="utf-8")
        # No actions table when no findings
        assert '<table class="actions-table">' not in html
        # But executive summary should still render
        assert "Executive Summary" in html

    def test_terminal_renders_without_actions(self, empty_environment):
        runner = CliRunner()
        result = runner.invoke(main, ["report", "--yes", "--format", "terminal"])
        assert result.exit_code == 0
