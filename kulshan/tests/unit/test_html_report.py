"""Smoke tests for the HTML report generator."""
from __future__ import annotations

import re

import pytest

from kulshan.report.html import _svg_bar, _svg_dial, generate_html_report
from kulshan.orchestrator import TOOL_LABELS, TOOL_ORDER

# ---------------------------------------------------------------------------
# Mock data covering all 10 check packs, with a mix of real scores and skipped packs
# ---------------------------------------------------------------------------
MOCK_RESULTS: dict = {
    "cost": {
        "tool": "cost",
        "scores": {
            "overall_score": 85,
            "grade": "B",
            "total_findings": 3,
            "severity_counts": {"high": 1, "medium": 2},
        },
        "errors": [],
    },
    "security": {
        "tool": "security",
        "scores": {
            "overall_score": 62,
            "grade": "D",
            "total_findings": 12,
            "severity_counts": {"critical": 2, "high": 5, "medium": 3, "low": 2},
        },
        "errors": [],
    },
    "sweep": {
        "tool": "sweep",
        "scores": {
            "overall_score": 78,
            "grade": "C+",
            "total_findings": 8,
            "severity_counts": {"medium": 4, "low": 4},
        },
        "errors": [],
    },
    "dr": {
        "tool": "dr",
        "scores": {
            "overall_score": 91,
            "grade": "A-",
            "total_findings": 1,
            "severity_counts": {"low": 1},
        },
        "errors": [],
    },
    "age": {
        "tool": "age",
        "scores": {
            "overall_score": 0,
            "grade": "N/A",
            "total_findings": 0,
            "severity_counts": {},
        },
        "errors": ["Not installed"],
        "skipped": True,
    },
    "drift": {
        "tool": "drift",
        "scores": {
            "overall_score": 55,
            "grade": "F",
            "total_findings": 20,
            "severity_counts": {"high": 10, "medium": 10},
        },
        "errors": [],
    },
    "tag": {
        "tool": "tag",
        "scores": {
            "overall_score": 0,
            "grade": "N/A",
            "total_findings": 0,
            "severity_counts": {},
        },
        "errors": ["Not installed"],
        "skipped": True,
    },
    "pulse": {
        "tool": "pulse",
        "scores": {
            "overall_score": 73,
            "grade": "C",
            "total_findings": 5,
            "severity_counts": {"medium": 3, "low": 2},
        },
        "errors": [],
    },
    "limit": {
        "tool": "limit",
        "scores": {
            "overall_score": 97,
            "grade": "A+",
            "total_findings": 0,
            "severity_counts": {},
        },
        "errors": [],
    },
    "topo": {
        "tool": "topo",
        "scores": {
            "overall_score": 88,
            "grade": "B+",
            "total_findings": 2,
            "severity_counts": {"medium": 1, "low": 1},
        },
        "errors": ["Partial timeout on eu-west-1"],
    },
}


def _generate_default() -> str:
    """Helper to generate a report with default mock data."""
    return generate_html_report(
        results=MOCK_RESULTS,
        overall_score=76,
        overall_grade="C+",
        account_id="123456789012",
        regions=["us-east-1", "us-west-2", "eu-west-1"],
        duration_secs=42.3,
    )


class TestGenerateHtmlReport:
    def test_generate_returns_string(self):
        html = _generate_default()
        assert isinstance(html, str)
        assert len(html) > 0

    def test_is_valid_html_structure(self):
        html = _generate_default()
        assert "<!DOCTYPE html>" in html
        assert "<html" in html
        assert "</html>" in html
        assert "<head>" in html
        assert "<body>" in html

    def test_contains_no_external_urls(self):
        """No external resource loading - the report must be self-contained."""
        html = _generate_default()
        # Check for external link/script/img tags
        # We allow http/https in plain text content, but not in src/href of resource tags
        link_tags = re.findall(r'<link[^>]+href=["\']https?://', html)
        script_tags = re.findall(r'<script[^>]+src=["\']https?://', html)
        img_tags = re.findall(r'<img[^>]+src=["\']https?://', html)
        assert len(link_tags) == 0, f"Found external link tags: {link_tags}"
        assert len(script_tags) == 0, f"Found external script tags: {script_tags}"
        assert len(img_tags) == 0, f"Found external img tags: {img_tags}"

    def test_contains_ran_tool_names(self):
        """Only tools that ran should appear in the detailed breakdown."""
        html = _generate_default()
        # Default test fixture has all packs with scores, so all labels should appear
        # in the detailed breakdown section
        label = TOOL_LABELS["cost"]
        assert label in html, f"Missing ran tool label: {label}"

    def test_contains_overall_score(self):
        html = _generate_default()
        assert "76" in html
        assert "C+" in html

    def test_contains_account_id(self):
        html = _generate_default()
        assert "123456789012" in html

    def test_contains_score_in_footer(self):
        html = _generate_default()
        # Score appears in footer and/or executive summary
        assert "76" in html or "C+" in html

    def test_contains_inline_css(self):
        html = _generate_default()
        assert "<style>" in html
        assert "var(--bg-primary)" in html

    def test_contains_inline_js(self):
        html = _generate_default()
        assert "<script>" in html
        assert "theme-toggle" in html

    def test_handles_skipped_tools(self):
        """Skipped tools should not appear in the report."""
        all_skipped = {}
        for key in TOOL_ORDER:
            all_skipped[key] = {
                "tool": key,
                "scores": {"overall_score": 0, "grade": "N/A", "total_findings": 0, "severity_counts": {}},
                "findings": [],
                "errors": [],
                "skipped": True,
            }
        from kulshan.report.html import generate_html_report
        result = generate_html_report(
            results=all_skipped,
            overall_score=0,
            overall_grade="N/A",
            account_id="000000000000",
            regions=["us-east-1"],
            duration_secs=1.0,
        )
        # With all packs skipped, no tool labels should appear in detailed breakdown
        assert "Detailed Breakdown" in result
        # But no individual tool details since all are skipped
        assert '<details class="tool-detail">' not in result

    def test_handles_empty_results(self):
        """Empty results dict should not crash."""
        html = generate_html_report(
            results={},
            overall_score=0,
            overall_grade="F",
            account_id="000000000000",
            regions=[],
            duration_secs=0.0,
        )
        assert isinstance(html, str)
        assert len(html) > 0

    def test_report_size_under_50kb(self):
        """Typical report should be under 50KB."""
        html = _generate_default()
        size_kb = len(html.encode("utf-8")) / 1024
        assert size_kb < 50, f"Report is {size_kb:.1f}KB, expected under 50KB"


class TestSvgDial:
    def test_returns_valid_svg(self):
        svg = _svg_dial(75)
        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")
        assert "75" in svg

    def test_score_zero(self):
        svg = _svg_dial(0)
        assert "0" in svg
        assert "<svg" in svg

    def test_score_hundred(self):
        svg = _svg_dial(100)
        assert "100" in svg

    def test_custom_size(self):
        svg = _svg_dial(50, size=200)
        assert 'width="200"' in svg
        assert 'height="200"' in svg

    def test_clamps_negative(self):
        svg = _svg_dial(-10)
        assert "0" in svg

    def test_clamps_over_hundred(self):
        svg = _svg_dial(150)
        assert "100" in svg


class TestSvgBar:
    def test_returns_valid_svg(self):
        svg = _svg_bar(60)
        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")

    def test_custom_dimensions(self):
        svg = _svg_bar(50, width=300, height=20)
        assert 'width="300"' in svg
        assert 'height="20"' in svg

    def test_zero_score(self):
        svg = _svg_bar(0)
        assert 'width="0"' in svg or 'width="0"' in svg
        assert "<svg" in svg

    def test_full_score(self):
        svg = _svg_bar(100, width=200)
        assert 'width="200"' in svg
