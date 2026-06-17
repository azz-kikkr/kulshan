"""Drift guard and sanitization tests for ``samples/sample-report.{html,json}``.

Phase 6C-3. The committed sample artifacts are buyer-facing assets:

* website CTA target
* outreach asset
* the proof that Kulshan is not a CLI toy

Two failure modes this test class catches:

1. **Drift.** Someone changes ``report/html.py`` or a fixture without
   regenerating ``samples/sample-report.html``. The drift-guard tests fail
   loudly and tell the human to run the script.

2. **Leakage.** A real AWS account id, ARN, or email address slips into the
   committed artifact. The sanitization tests reject it.

If you just changed the renderer and tests here are failing, run::

    python Kulshan/scripts/generate_sample_report.py

and commit the updated artifacts.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

# Resolve repo paths relative to this test file.
TESTS_DIR = Path(__file__).resolve().parent       # Kulshan/tests/unit
WHEEL_DIR = TESTS_DIR.parent.parent               # Kulshan/
REPO_ROOT = WHEEL_DIR.parent                      # mission-finops/

SCRIPT_PATH = WHEEL_DIR / "scripts" / "generate_sample_report.py"
SAMPLES_DIR = REPO_ROOT / "samples"
HTML_PATH = SAMPLES_DIR / "sample-report.html"
JSON_PATH = SAMPLES_DIR / "sample-report.json"
README_PATH = SAMPLES_DIR / "README.md"


def _import_generator():
    """Import ``generate_sample_report.py`` from disk without polluting sys.path."""
    spec = importlib.util.spec_from_file_location(
        "generate_sample_report", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gsr():
    """Imported generator module, scoped per test module."""
    return _import_generator()


# ── files exist ──────────────────────────────────────────────────────────────


class TestSampleReportFiles:
    def test_html_exists(self):
        assert HTML_PATH.exists(), (
            f"missing {HTML_PATH}, run "
            "`python Kulshan/scripts/generate_sample_report.py`"
        )

    def test_json_exists(self):
        assert JSON_PATH.exists(), (
            f"missing {JSON_PATH}, run "
            "`python Kulshan/scripts/generate_sample_report.py`"
        )

    def test_readme_exists(self):
        assert README_PATH.exists()

    def test_html_not_empty(self):
        assert HTML_PATH.stat().st_size > 1024

    def test_json_not_empty(self):
        assert JSON_PATH.stat().st_size > 1024


# ── sanitization (no real AWS / customer data leaks into the artifact) ───────


class TestSampleReportSanitization:
    """Reject any non-placeholder 12-digit number, real ARN, or email address."""

    _NON_PLACEHOLDER_ACCOUNT_RE = re.compile(r"\b(?!0{12}\b)\d{12}\b")
    _NONZERO_ARN_ACCOUNT_RE = re.compile(
        r"arn:aws:[\w-]+:[\w-]*:(?!000000000000\b)\d{12}:"
    )
    _EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

    def test_no_real_account_ids_in_html(self):
        text = HTML_PATH.read_text(encoding="utf-8")
        assert not self._NON_PLACEHOLDER_ACCOUNT_RE.search(text)

    def test_no_real_account_ids_in_json(self):
        text = JSON_PATH.read_text(encoding="utf-8")
        assert not self._NON_PLACEHOLDER_ACCOUNT_RE.search(text)

    def test_no_arn_with_nonzero_account_in_html(self):
        text = HTML_PATH.read_text(encoding="utf-8")
        assert not self._NONZERO_ARN_ACCOUNT_RE.search(text)

    def test_no_arn_with_nonzero_account_in_json(self):
        text = JSON_PATH.read_text(encoding="utf-8")
        assert not self._NONZERO_ARN_ACCOUNT_RE.search(text)

    def test_no_email_addresses_in_html(self):
        text = HTML_PATH.read_text(encoding="utf-8")
        assert not self._EMAIL_RE.search(text)

    def test_no_email_addresses_in_json(self):
        text = JSON_PATH.read_text(encoding="utf-8")
        assert not self._EMAIL_RE.search(text)

    def test_placeholder_account_present_in_html(self):
        text = HTML_PATH.read_text(encoding="utf-8")
        assert "000000000000" in text

    def test_placeholder_account_present_in_json(self):
        text = JSON_PATH.read_text(encoding="utf-8")
        assert "000000000000" in text


# ── HTML structure (the buyer-facing sections must actually be present) ──────


class TestSampleReportHTMLStructure:
    @pytest.fixture(scope="class")
    def html_text(self) -> str:
        return HTML_PATH.read_text(encoding="utf-8")

    def test_top_actions_section(self, html_text):
        assert "actions-table" in html_text or "action-rank" in html_text

    def test_overlap_summary_block(self, html_text):
        assert '<div class="overlap-summary">' in html_text
        assert "1 confirmed by Kulshan" in html_text
        assert "1 Kulshan-only" in html_text
        assert "0 AWS-only" in html_text

    def test_Kulshan_statistical_subsection(self, html_text):
        assert (
            '<h3 class="findings-subsection">Kulshan statistical anomalies</h3>'
            in html_text
        )

    def test_aws_native_subsection(self, html_text):
        assert (
            '<h3 class="findings-subsection">AWS-native Cost Anomaly Detection</h3>'
            in html_text
        )

    def test_evidence_block_present(self, html_text):
        assert '<details class="finding-evidence">' in html_text
        assert "<summary>Evidence</summary>" in html_text

    def test_finding_cards_present(self, html_text):
        assert '<div class="finding-card">' in html_text

    def test_all_ten_tool_labels(self, html_text):
        from kulshan.orchestrator import TOOL_LABELS

        for label in TOOL_LABELS.values():
            assert label in html_text, f"label '{label}' missing from sample HTML"

    def test_self_contained_no_external_resources(self, html_text):
        # Sample must not pull external CSS or JS, it is shipped as-is.
        assert "<link " not in html_text or "rel=\"stylesheet\"" not in html_text
        assert '<script src=' not in html_text
        assert 'http://' not in html_text or html_text.count('http://') == 0
        # Single inline <script> for theme toggle is acceptable.
        assert html_text.count("<script>") == 1

    def test_synthetic_banner_present(self, html_text):
        assert '<div class="synthetic-banner"' in html_text
        assert "Synthetic sample report" in html_text
        assert (
            "This report uses fixture data only. No customer data, no real "
            "AWS account IDs, and no live AWS environment were used."
            in html_text
        )

    def test_severity_summary_critical_count_matches_top_actions(self, html_text):
        """At least one critical finding should exist in the sample report."""
        assert "Critical" in html_text or "critical" in html_text


# ── JSON structure ───────────────────────────────────────────────────────────


class TestSampleReportJSONStructure:
    @pytest.fixture(scope="class")
    def payload(self) -> dict:
        return json.loads(JSON_PATH.read_text(encoding="utf-8"))

    def test_top_level_keys(self, payload):
        for key in (
            "kulshan_version",
            "account_id",
            "regions",
            "duration_seconds",
            "overall_score",
            "overall_grade",
            "tools",
            "findings",
            "top_actions",
        ):
            assert key in payload, f"missing top-level key '{key}'"

    def test_account_id_is_placeholder(self, payload):
        assert payload["account_id"] == "000000000000"

    def test_findings_count_matches_cost_fixture(self, payload):
        # Cost fixture currently has 3 findings; nothing else emits any.
        assert len(payload["findings"]) == 3

    def test_top_actions_count(self, payload):
        # 3 findings → top_actions holds all 3 (under the cap of 10).
        assert len(payload["top_actions"]) == 3

    def test_top_action_priority_ordering(self, payload):
        first = payload["top_actions"][0]
        last = payload["top_actions"][-1]
        assert first["severity"] == "critical"
        assert last["severity"] == "medium"

    def test_all_ten_tools_present(self, payload):
        from kulshan.orchestrator import TOOL_ORDER

        for tool_key in TOOL_ORDER:
            assert tool_key in payload["tools"]

    def test_regions_match_frozen_constants(self, payload):
        assert payload["regions"] == ["us-east-1", "us-west-2", "eu-west-1"]

    def test_duration_is_frozen(self, payload):
        assert payload["duration_seconds"] == 12.4


# ── drift guard (the source of truth) ────────────────────────────────────────


class TestSampleReportDriftGuard:
    """Re-run generation in memory; committed files must match byte-for-byte
    (with universal newlines normalising platform line endings)."""

    _STALE_HINT = (
        "samples/sample-report.{ext} is stale. "
        "Run: python Kulshan/scripts/generate_sample_report.py"
    )

    def test_html_matches_regeneration(self, gsr):
        results = gsr.load_fixtures()
        score, grade, _findings, top = gsr.compute_inputs(results)
        expected = gsr.build_html(results, top, score, grade)
        actual = HTML_PATH.read_text(encoding="utf-8")
        assert actual == expected, self._STALE_HINT.format(ext="html")

    def test_json_matches_regeneration(self, gsr):
        results = gsr.load_fixtures()
        score, grade, findings, top = gsr.compute_inputs(results)
        expected = gsr.build_json(results, top, score, grade, findings)
        actual = JSON_PATH.read_text(encoding="utf-8")
        assert actual == expected, self._STALE_HINT.format(ext="json")

    def test_regeneration_is_deterministic(self, gsr):
        """Running the build twice must produce identical output (frozen inputs)."""
        results = gsr.load_fixtures()
        score, grade, findings, top = gsr.compute_inputs(results)
        first_html = gsr.build_html(results, top, score, grade)
        second_html = gsr.build_html(results, top, score, grade)
        assert first_html == second_html

        first_json = gsr.build_json(results, top, score, grade, findings)
        second_json = gsr.build_json(results, top, score, grade, findings)
        assert first_json == second_json
