"""Smoke tests for the Kulshan CLI and orchestrator."""
from __future__ import annotations

from click.testing import CliRunner

from kulshan.cli import main
from kulshan.orchestrator import TOOL_LABELS, TOOL_ORDER, TOOL_WEIGHTS, compute_overall


class TestCLI:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Kulshan" in result.output

    def test_version(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_report_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["report", "--help"])
        assert result.exit_code == 0
        assert "--quick" in result.output
        assert "--format" in result.output
        assert "--no-history" in result.output

    def test_delete_history_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["delete-history", "--help"])
        assert result.exit_code == 0
        assert "Permanently delete" in result.output


class TestOrchestrator:
    def test_tool_order_length(self):
        assert len(TOOL_ORDER) == 10

    def test_labels_for_all_tools(self):
        for key in TOOL_ORDER:
            assert key in TOOL_LABELS

    def test_weights_sum_to_one(self):
        total = sum(TOOL_WEIGHTS.values())
        assert abs(total - 1.0) < 0.01

    def test_compute_overall_empty(self):
        score, grade = compute_overall({})
        assert score == 0

    def test_compute_overall_with_results(self):
        results = {
            "cost": {"scores": {"overall_score": 80}, "errors": []},
            "security": {"scores": {"overall_score": 60}, "errors": []},
        }
        score, grade = compute_overall(results)
        assert 0 <= score <= 100
        assert grade in ("A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D", "F")

    def test_compute_overall_withholds_score_when_pack_skipped(self):
        results = {
            "cost": {"scores": {"overall_score": 90}, "errors": []},
            "security": {"scores": {"overall_score": 0}, "errors": ["x"], "skipped": True},
        }
        score, _ = compute_overall(results)
        assert score == 0
        assert _ == "N/A"


class TestSession:
    def test_import(self):
        from kulshan.session import create_session, get_account_id, get_enabled_regions
        assert callable(create_session)
        assert callable(get_account_id)
        assert callable(get_enabled_regions)


class TestAdapters:
    def test_all_adapters_importable(self):
        """Each adapter module should import without error."""
        import importlib
        for tool_key in TOOL_ORDER:
            try:
                mod = importlib.import_module(f"kulshan.adapters.{tool_key}")
                assert hasattr(mod, "run_scan")
            except ImportError:
                pass  # base tool not installed, that is fine
