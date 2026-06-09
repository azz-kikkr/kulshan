"""Tests for partial-scan metadata and score withholding."""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

from rich.console import Console

from kulshan.orchestrator import compute_overall, run_all_scans, summarize_completeness


def _result(*, score: int = 80, errors: list[str] | None = None) -> dict:
    return {
        "scores": {
            "overall_score": score,
            "grade": "B",
            "total_findings": 0,
            "severity_counts": {},
        },
        "findings": [],
        "errors": errors or [],
    }


def test_complete_scan_retains_aggregate_score():
    results = {"cost": _result(score=80), "security": _result(score=60)}

    metadata = summarize_completeness(results)
    score, grade = compute_overall(results)

    assert metadata["partial"] is False
    assert metadata["completed_checks"] == ["cost", "security"]
    assert score == 70
    assert grade == "C-"


def test_pack_error_marks_scan_partial_and_withholds_score():
    results = {
        "cost": _result(score=90),
        "security": _result(errors=["AccessDenied: missing s3:GetBucketEncryption"]),
    }

    metadata = summarize_completeness(results)
    score, grade = compute_overall(results)

    assert metadata["partial"] is True
    assert metadata["partial_checks"] == ["security"]
    assert metadata["failed_checks"] == ["security"]
    assert metadata["missing_permissions"] == [
        "AccessDenied: missing s3:GetBucketEncryption"
    ]
    assert score == 0
    assert grade == "N/A"


def test_skipped_pack_is_reported_separately():
    results = {
        "cost": _result(),
        "security": {
            "scores": {"overall_score": 0, "grade": "N/A"},
            "errors": ["Not installed"],
            "skipped": True,
        },
    }

    metadata = summarize_completeness(results)

    assert metadata["partial"] is True
    assert metadata["skipped_checks"] == ["security"]
    assert results["security"]["completeness"] == "skipped"


@patch("kulshan.orchestrator._load_check")
def test_invalid_findings_make_pack_partial(mock_load):
    check = MagicMock()
    check.run_scan.return_value = {
        **_result(),
        "findings": [
            {
                "id": "cost-test-invalid",
                "pack": "cost",
                "kind": "test",
                "title": "Invalid severity",
                "severity": "urgent",
                "confidence": 0.9,
                "effort": "low",
                "risk": "safe",
            }
        ],
    }
    mock_load.return_value = check

    results = run_all_scans(
        session=MagicMock(),
        regions=["us-east-1"],
        selected_packs=["cost"],
        console=Console(file=io.StringIO(), quiet=True),
    )

    assert results["cost"]["partial"] is True
    assert results["cost"]["completeness"] == "partial"
    assert results["cost"]["findings"] == []
    assert results["cost"]["errors"] == ["Excluded 1 invalid finding(s)"]
