"""Tests for the canonical coverage model."""
from __future__ import annotations

from kulshan.coverage import (
    CoverageError,
    CoverageReport,
    ExecutionRecord,
    build_coverage_from_results,
)


class TestCoverageReport:
    def test_to_dict_structure(self):
        report = CoverageReport(
            packs_attempted=3,
            packs_completed=2,
            packs_partial=1,
            regions_scanned=2,
            report_status="partial",
            scan_duration_seconds=14.5,
        )
        d = report.to_dict()
        assert d["summary"]["packs_attempted"] == 3
        assert d["summary"]["packs_completed"] == 2
        assert d["summary"]["report_status"] == "partial"
        assert d["summary"]["scan_duration_seconds"] == 14.5

    def test_terminal_summary_complete(self):
        report = CoverageReport(
            packs_attempted=10, packs_completed=10, regions_scanned=3,
        )
        summary = report.terminal_summary()
        assert "10/10 packs complete" in summary
        assert "3 regions" in summary

    def test_terminal_summary_partial(self):
        report = CoverageReport(
            packs_attempted=10, packs_completed=7,
            packs_partial=2, packs_failed=1, regions_scanned=3,
            denied_actions=["security: Access denied: get_account_summary"],
        )
        summary = report.terminal_summary()
        assert "7/10" in summary
        assert "2 partial" in summary
        assert "1 failed" in summary
        assert "1 permissions denied" in summary

    def test_execution_record_to_dict(self):
        rec = ExecutionRecord(
            pack="security", region="us-east-1", status="complete",
            findings_count=5,
        )
        d = rec.to_dict()
        assert d["pack"] == "security"
        assert d["region"] == "us-east-1"
        assert d["status"] == "complete"
        assert d["findings_count"] == 5

    def test_execution_record_with_errors(self):
        rec = ExecutionRecord(
            pack="security", region="us-east-1", status="partial",
            errors=[CoverageError(
                service="iam", action="get_account_summary",
                code="AccessDenied", message="Not authorized",
            )],
        )
        d = rec.to_dict()
        assert len(d["errors"]) == 1
        assert d["errors"][0]["code"] == "AccessDenied"


class TestBuildCoverageFromResults:
    def test_all_complete(self):
        results = {
            "cost": {"findings": [{"title": "x"}], "scores": {}, "errors": []},
            "security": {"findings": [], "scores": {}, "errors": []},
        }
        report = build_coverage_from_results(
            results=results, regions=["us-east-1"], duration_seconds=10.0,
        )
        assert report.packs_attempted == 2
        assert report.packs_completed == 2
        assert report.report_status == "complete"

    def test_skipped_pack(self):
        results = {
            "cost": {"findings": [], "scores": {}, "errors": [], "skipped": True},
            "security": {"findings": [{"title": "x"}], "scores": {}, "errors": []},
        }
        report = build_coverage_from_results(
            results=results, regions=["us-east-1"], duration_seconds=5.0,
        )
        assert report.packs_skipped == 1
        assert report.packs_completed == 1
        assert report.report_status == "partial"

    def test_denied_permission(self):
        results = {
            "security": {
                "findings": [],
                "scores": {},
                "errors": ["Access denied: get_account_summary"],
            },
        }
        report = build_coverage_from_results(
            results=results, regions=["us-east-1"], duration_seconds=3.0,
        )
        assert report.packs_failed == 1
        assert report.report_status == "failed"
        assert len(report.denied_actions) == 1

    def test_partial_with_some_findings(self):
        results = {
            "security": {
                "findings": [{"title": "open port"}],
                "scores": {},
                "errors": ["Access denied: get_account_summary"],
            },
        }
        report = build_coverage_from_results(
            results=results, regions=["us-east-1"], duration_seconds=3.0,
        )
        assert report.packs_partial == 1
        assert report.report_status == "partial"

    def test_data_sources_detection(self):
        results = {
            "cost": {
                "findings": [],
                "scores": {},
                "errors": [],
                "metadata": {"cur_investigation": {"month": "2026-06"}},
            },
        }
        report = build_coverage_from_results(
            results=results, regions=["us-east-1"], duration_seconds=5.0,
        )
        assert report.data_sources.get("cur_parquet") == "used"
        assert report.data_sources.get("cost_explorer") == "used"

    def test_multiple_regions(self):
        results = {
            "topo": {"findings": [], "scores": {}, "errors": []},
        }
        report = build_coverage_from_results(
            results=results, regions=["us-east-1", "eu-west-1", "ap-southeast-1"],
            duration_seconds=6.0,
        )
        assert report.regions_scanned == 3
        # One execution record per region
        assert len(report.executions) == 3

    def test_json_serializable(self):
        """Coverage report must be JSON-serializable."""
        import json
        results = {
            "cost": {"findings": [{"title": "x"}], "scores": {}, "errors": []},
            "security": {"findings": [], "scores": {}, "errors": ["Access denied: foo"]},
        }
        report = build_coverage_from_results(
            results=results, regions=["us-east-1"], duration_seconds=10.0,
        )
        json_str = json.dumps(report.to_dict())
        parsed = json.loads(json_str)
        assert "summary" in parsed
        assert "executions" in parsed
        assert "denied_actions" in parsed
