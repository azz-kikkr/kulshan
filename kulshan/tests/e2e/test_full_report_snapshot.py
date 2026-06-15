"""End-to-end snapshot test for ``Kulshan Report``.

Migration safety net. Mocks the AWS-touching boundaries (session creation,
account lookup, region lookup) and the orchestrator's ``run_all_scans``
function with deterministic fixture data, then invokes ``Kulshan Report``
through Click's CliRunner to exercise the full report path
(orchestrator -> compose_overall -> renderer -> file write).

Assertions are structural, not byte-for-byte:
- exit code is 0
- HTML output contains every tool label and every overall score
- JSON output has the expected shape and per-tool scores match fixtures

Run this before AND after any packaging refactor. If the same fixture inputs
produce the same structural assertions on both sides, the move is verified
non-breaking.
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

# After Phase 6C-3 the cost fixture carries a critical finding, so the CLI
# correctly exits with FINDING_FAIL instead of SUCCESS. Tests should accept
# either non-error code; the fact the report file was written is what matters.
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
    """Patch the AWS and orchestrator boundaries so `Kulshan Report` runs offline."""
    with (
        patch("kulshan.session.create_session", return_value=MagicMock()),
        patch("kulshan.session.get_account_id", return_value=ACCOUNT_ID),
        patch("kulshan.session.get_enabled_regions", return_value=list(REGIONS)),
        patch("kulshan.orchestrator.run_all_scans", return_value=fixture_results),
    ):
        yield


class TestReportFixturesPresent:
    """Sanity checks on the fixture data itself."""

    def test_one_fixture_per_tool(self, fixture_results):
        assert set(fixture_results.keys()) == set(TOOL_ORDER)

    def test_every_fixture_has_score_and_grade(self, fixture_results):
        for tool_key, result in fixture_results.items():
            scores = result.get("scores", {})
            assert "overall_score" in scores, f"{tool_key} missing overall_score"
            assert "grade" in scores, f"{tool_key} missing grade"
            score = scores["overall_score"]
            assert isinstance(score, int) and 0 <= score <= 100

    def test_no_real_account_ids_in_fixtures(self, fixture_results):
        # Defensive: nothing in fixtures should look like a real 12-digit AWS account id
        # (other than the all-zeros placeholder if any tool puts one there).
        import re

        forbidden = re.compile(r"\b(?!0{12}\b)\d{12}\b")
        for tool_key, result in fixture_results.items():
            blob = json.dumps(result)
            assert not forbidden.search(blob), (
                f"{tool_key} fixture contains a 12-digit number that looks like a "
                f"real AWS account id; replace with 000000000000."
            )


class TestReportHTML:
    def test_exits_cleanly_and_writes_file(self, mocked_environment, tmp_path):
        out = tmp_path / "report.html"
        runner = CliRunner()
        result = runner.invoke(main, ["report", "--format", "html", "--output", str(out)])
        assert result.exit_code in _OK_EXITS, (
            f"unexpected exit; output: {result.output!r}; exception: {result.exception!r}"
        )
        assert out.exists(), "HTML report file was not written"

    def test_contains_all_tool_labels(self, mocked_environment, tmp_path):
        out = tmp_path / "report.html"
        runner = CliRunner()
        runner.invoke(main, ["report", "--format", "html", "--output", str(out)])
        html = out.read_text(encoding="utf-8")
        for tool_key, label in TOOL_LABELS.items():
            assert label in html, f"label '{label}' for tool '{tool_key}' missing from HTML"

    def test_contains_all_overall_scores(self, mocked_environment, tmp_path, fixture_results):
        out = tmp_path / "report.html"
        runner = CliRunner()
        runner.invoke(main, ["report", "--format", "html", "--output", str(out)])
        html = out.read_text(encoding="utf-8")
        for tool_key, fixture in fixture_results.items():
            score = fixture["scores"]["overall_score"]
            assert str(score) in html, f"score {score} for '{tool_key}' missing from HTML"

    def test_contains_all_grades(self, mocked_environment, tmp_path, fixture_results):
        out = tmp_path / "report.html"
        runner = CliRunner()
        runner.invoke(main, ["report", "--format", "html", "--output", str(out)])
        html = out.read_text(encoding="utf-8")
        for tool_key, fixture in fixture_results.items():
            grade = fixture["scores"]["grade"]
            assert grade in html, f"grade '{grade}' for '{tool_key}' missing from HTML"

    def test_contains_account_id(self, mocked_environment, tmp_path):
        out = tmp_path / "report.html"
        runner = CliRunner()
        runner.invoke(main, ["report", "--format", "html", "--output", str(out)])
        html = out.read_text(encoding="utf-8")
        assert ACCOUNT_ID in html


class TestReportJSON:
    def test_exits_cleanly_and_has_top_level_shape(self, mocked_environment, tmp_path):
        out = tmp_path / "report.json"
        runner = CliRunner()
        result = runner.invoke(main, ["report", "--format", "json", "--output", str(out)])
        assert result.exit_code in _OK_EXITS
        data = json.loads(out.read_text(encoding="utf-8"))
        for key in ("kulshan_version", "account_id", "regions", "overall_score", "overall_grade", "tools"):
            assert key in data, f"top-level key '{key}' missing from JSON"
        assert data["account_id"] == ACCOUNT_ID

    def test_all_tools_present_with_matching_scores(self, mocked_environment, tmp_path, fixture_results):
        out = tmp_path / "report.json"
        runner = CliRunner()
        runner.invoke(main, ["report", "--format", "json", "--output", str(out)])
        data = json.loads(out.read_text(encoding="utf-8"))
        for tool_key in TOOL_ORDER:
            assert tool_key in data["tools"], f"{tool_key} missing from JSON output.tools"
            tool_data = data["tools"][tool_key]
            expected_score = fixture_results[tool_key]["scores"]["overall_score"]
            assert tool_data["scores"]["overall_score"] == expected_score

    def test_overall_score_is_in_range(self, mocked_environment, tmp_path):
        out = tmp_path / "report.json"
        runner = CliRunner()
        runner.invoke(main, ["report", "--format", "json", "--output", str(out)])
        data = json.loads(out.read_text(encoding="utf-8"))
        assert isinstance(data["overall_score"], int)
        assert 0 <= data["overall_score"] <= 100
        valid_grades = {"A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D", "F"}
        assert data["overall_grade"] in valid_grades


# ── Phase 6C-1: top-level findings + top_actions in JSON ──────────────────────


class TestReportTopActionsJSON:
    """Phase 6C-1: ``Kulshan Report --format json`` exposes top-level
    ``findings`` and ``top_actions`` arrays."""

    def _run_json(self, tmp_path):
        out = tmp_path / "report.json"
        runner = CliRunner()
        result = runner.invoke(main, ["report", "--format", "json", "--output", str(out)])
        assert result.exit_code in _OK_EXITS
        return json.loads(out.read_text(encoding="utf-8"))

    def test_top_level_findings_present(self, mocked_environment, tmp_path):
        data = self._run_json(tmp_path)
        assert "findings" in data
        assert isinstance(data["findings"], list)
        # Cost fixture has 3 findings; nothing else emits today.
        assert len(data["findings"]) == 3

    def test_top_level_top_actions_present(self, mocked_environment, tmp_path):
        data = self._run_json(tmp_path)
        assert "top_actions" in data
        assert isinstance(data["top_actions"], list)
        # 3 findings → top_actions should hold all 3 (less than the cap of 10).
        assert len(data["top_actions"]) == 3

    def test_top_actions_capped_at_10(self, mocked_environment, tmp_path):
        data = self._run_json(tmp_path)
        assert len(data["top_actions"]) <= 10

    def test_top_actions_ordering_is_deterministic_and_priority_first(
        self, mocked_environment, tmp_path
    ):
        # The fixture has: critical/$18k, high/$18k overlap, medium/$1k.
        # Highest priority wins → first item should be the critical one.
        data = self._run_json(tmp_path)
        first = data["top_actions"][0]
        assert first["severity"] == "critical"
        # And the medium one should be last among the three.
        last = data["top_actions"][-1]
        assert last["severity"] == "medium"

    def test_existing_tools_findings_path_still_works(
        self, mocked_environment, tmp_path
    ):
        """Backwards compatibility: ``data.tools.cost.findings`` must still exist
        alongside the new top-level ``data.findings``."""
        data = self._run_json(tmp_path)
        assert "cost" in data["tools"]
        assert "findings" in data["tools"]["cost"]
        assert len(data["tools"]["cost"]["findings"]) == 3

    def test_cost_metadata_preserved(self, mocked_environment, tmp_path):
        data = self._run_json(tmp_path)
        cad = data["tools"]["cost"].get("metadata", {}).get("cost_anomaly_detection")
        assert cad is not None
        assert cad["status"] == "ok"
        assert "overlap" in cad

    def test_existing_keys_not_removed(self, mocked_environment, tmp_path):
        """Locks the legacy top-level keys so 6C-1 cannot accidentally drop them."""
        data = self._run_json(tmp_path)
        for key in (
            "kulshan_version",
            "account_id",
            "regions",
            "duration_seconds",
            "overall_score",
            "overall_grade",
            "tools",
        ):
            assert key in data, f"legacy top-level key '{key}' was removed"


# ── Phase 6C-1: terminal renderer surfaces Top Actions and AWS status ─────────


class TestReportTerminalTopActions:
    """Phase 6C-1: ``Kulshan Report --format terminal`` shows a Top Actions
    panel + AWS Cost Anomaly Detection status line when findings exist."""

    def _run_terminal(self):
        runner = CliRunner()
        return runner.invoke(main, ["report", "--format", "terminal"])

    def test_terminal_exits_cleanly(self, mocked_environment):
        result = self._run_terminal()
        assert result.exit_code in _OK_EXITS, result.output

    def test_terminal_contains_top_actions_header(self, mocked_environment):
        result = self._run_terminal()
        assert "Top Actions" in result.output

    def test_terminal_contains_top_action_titles(self, mocked_environment):
        """The Top Actions panel renders titles from the fixture findings."""
        result = self._run_terminal()
        # All three fixture findings have distinctive title fragments.
        assert "EC2 - Other spike" in result.output
        assert "AWS-detected anomaly" in result.output
        assert "Amazon RDS spike" in result.output

    def test_terminal_contains_aws_anomaly_detection_line(self, mocked_environment):
        result = self._run_terminal()
        assert "AWS Cost Anomaly Detection" in result.output

    def test_terminal_aws_status_includes_overlap_summary(self, mocked_environment):
        result = self._run_terminal()
        # The fixture has both_count=1, Kulshan_only_count=1, aws_only_count=0.
        # Rich may wrap long lines in CliRunner's narrow terminal; assert the
        # individual phrases appear somewhere in the output (whitespace-tolerant).
        # Strip whitespace/newlines for a wrap-tolerant comparison.
        flat = " ".join(result.output.split())
        assert "1 confirmed by Kulshan" in flat
        assert "1 Kulshan-only" in flat

    def test_terminal_keeps_existing_score_table(self, mocked_environment):
        """Score table must still render so old behavior is preserved."""
        result = self._run_terminal()
        assert "Cost Analyzer" in result.output  # one of the TOOL_LABELS values
        assert "/100" in result.output  # overall score display


class TestReportTerminalNoFindings:
    """When no findings exist (older fixtures without the new fields), the
    terminal report must still render and must NOT show Top Actions noise."""

    @pytest.fixture
    def empty_findings_environment(self):
        """All packs return scores only, no findings, no metadata."""
        empty_results = {
            tool_key: {
                "tool": tool_key,
                "scores": {
                    "overall_score": 80,
                    "grade": "B-",
                    "total_findings": 0,
                    "severity_counts": {},
                    "breakdown": {},
                },
                "errors": [],
            }
            for tool_key in TOOL_ORDER
        }
        with (
            patch("kulshan.session.create_session", return_value=MagicMock()),
            patch("kulshan.session.get_account_id", return_value=ACCOUNT_ID),
            patch("kulshan.session.get_enabled_regions", return_value=list(REGIONS)),
            patch("kulshan.orchestrator.run_all_scans", return_value=empty_results),
        ):
            yield

    def test_terminal_renders_without_top_actions_panel(self, empty_findings_environment):
        runner = CliRunner()
        result = runner.invoke(main, ["report", "--format", "terminal"])
        assert result.exit_code == 0
        # No findings → no Top Actions header should appear.
        assert "Top Actions" not in result.output
        # No metadata → no AWS Cost Anomaly Detection line.
        assert "AWS Cost Anomaly Detection" not in result.output
        # But the existing score table must still render.
        assert "Overall Score" in result.output

    def test_json_renders_empty_top_arrays(self, empty_findings_environment, tmp_path):
        out = tmp_path / "report.json"
        runner = CliRunner()
        runner.invoke(main, ["report", "--format", "json", "--output", str(out)])
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["findings"] == []
        assert data["top_actions"] == []


# ── Phase 6C-2: HTML renders Top Actions, cost findings, overlap summary ──────


class TestReportHTMLTopActions:
    """Phase 6C-2: ``Kulshan Report --format html`` surfaces ranked top
    actions, cost finding cards grouped by kind, and the AWS Cost Anomaly
    Detection overlap summary inside the cost detail section."""

    def _render_html(self, tmp_path) -> str:
        out = tmp_path / "report.html"
        runner = CliRunner()
        result = runner.invoke(
            main, ["report", "--format", "html", "--output", str(out)]
        )
        assert result.exit_code in _OK_EXITS, (
            f"unexpected exit; output: {result.output!r}; "
            f"exception: {result.exception!r}"
        )
        return out.read_text(encoding="utf-8")

    # --- Top Actions table ---

    def test_html_contains_top_actions_section(self, mocked_environment, tmp_path):
        html_text = self._render_html(tmp_path)
        # Use the rendered element, not the substring (CSS has the same words).
        assert '<h2 class="section-title">Top Actions</h2>' in html_text
        assert '<table class="top-actions-table">' in html_text

    def test_html_top_actions_includes_each_finding_title(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        # Every fixture finding has a distinctive title fragment.
        assert "EC2 - Other spike" in html_text
        assert "AWS-detected anomaly" in html_text
        assert "Amazon RDS spike" in html_text

    def test_html_top_actions_severity_pills_present(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        # Severity values appear capitalised inside compact pills.
        assert ">Critical</span>" in html_text
        assert ">High</span>" in html_text
        assert ">Medium</span>" in html_text

    def test_html_top_actions_shows_estimated_monthly_impact(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        # critical+high finding: 18712.50 → "$18,712.50"
        # medium finding: 1356.00 → "$1,356.00"
        assert "$18,712.50" in html_text
        assert "$1,356.00" in html_text

    def test_html_top_actions_source_labels_match_terminal(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        # Two statistical findings → "Kulshan"; one aws_native → "AWS".
        assert '<td class="ta-source">Kulshan</td>' in html_text
        assert '<td class="ta-source">AWS</td>' in html_text

    def test_html_top_actions_critical_ranked_first(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        # Composite priority puts the critical finding ahead of high+medium.
        crit_idx = html_text.find("EC2 - Other spike")
        med_idx = html_text.find("Amazon RDS spike")
        assert crit_idx != -1 and med_idx != -1
        assert crit_idx < med_idx

    # --- Cost findings cards ---

    def test_html_cost_section_titles_appear_in_cards(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        # Each finding title appears at least twice: once in the Top Actions
        # table and once inside its finding card.
        assert html_text.count("EC2 - Other spike") >= 2
        assert html_text.count("Amazon RDS spike") >= 2
        assert html_text.count("AWS-detected anomaly") >= 2

    def test_html_cost_section_renders_finding_cards(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        assert 'class="finding-card"' in html_text
        assert 'class="finding-cards"' in html_text

    def test_html_cost_section_groups_Kulshan_and_aws_native(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        assert "Kulshan statistical anomalies" in html_text
        assert "AWS-native Cost Anomaly Detection" in html_text

    def test_html_cost_section_contains_recommended_action(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        assert "Investigate in account 000000000000" in html_text
        assert "Verify in the AWS Cost Anomaly Detection console" in html_text
        assert "<strong>Recommended:</strong>" in html_text

    def test_html_cost_section_contains_why_it_matters(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        assert "Cost is 98% above baseline" in html_text
        assert "AWS Cost Anomaly Detection ML model" in html_text

    def test_html_cost_section_shows_metadata_fields(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        # Region / service / usage_type from cost fixture findings.
        assert "us-east-1" in html_text
        assert "EC2 - Other" in html_text
        assert "USE1-NatGateway-Bytes" in html_text
        assert "eu-west-1" in html_text
        assert "Amazon Relational Database Service" in html_text

    # --- Evidence ---

    def test_html_evidence_uses_native_details_element(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        assert '<details class="finding-evidence">' in html_text
        assert "<summary>Evidence</summary>" in html_text

    def test_html_evidence_uses_table_not_pre_block(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        # Evidence renders as a key/value table; no JSON dump.
        assert 'class="evidence-table"' in html_text
        # A specific evidence value from fixture should be visible.
        assert "Z:4.1σ, IQR, MAD:5.2" in html_text

    # --- AWS overlap summary ---

    def test_html_overlap_summary_status_and_counts(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        # The block is rendered as <div class="overlap-summary">.
        assert '<div class="overlap-summary">' in html_text
        assert "AWS Cost Anomaly Detection: <strong>ok</strong>" in html_text
        assert "1 confirmed by Kulshan" in html_text
        assert "1 Kulshan-only" in html_text
        assert "0 AWS-only" in html_text

    def test_html_overlap_summary_shows_lookback_days(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        assert "90-day lookback" in html_text

    # --- Guardrail: no new JS ---

    def test_html_no_new_javascript_introduced(
        self, mocked_environment, tmp_path
    ):
        html_text = self._render_html(tmp_path)
        # Only the existing theme-toggle script tag; no new JS for Phase 6C-2.
        assert html_text.count("<script>") == 1
        assert html_text.count("</script>") == 1

    def test_html_default_does_not_show_synthetic_banner(
        self, mocked_environment, tmp_path
    ):
        """CLI-generated reports default to synthetic_sample=False.

        The synthetic banner is for sample artifacts only; production reports
        must never carry it, otherwise buyers will think their own report is
        synthetic.
        """
        html_text = self._render_html(tmp_path)
        assert '<div class="synthetic-banner"' not in html_text
        assert "Synthetic sample report" not in html_text

    # --- Backwards compatibility ---

    def test_html_legacy_assertions_still_hold(
        self, mocked_environment, tmp_path, fixture_results
    ):
        html_text = self._render_html(tmp_path)
        # Tool labels + grades + scores remain present.
        for tool_key, label in TOOL_LABELS.items():
            assert label in html_text
        for tool_key, fixture in fixture_results.items():
            score = fixture["scores"]["overall_score"]
            grade = fixture["scores"]["grade"]
            assert str(score) in html_text
            assert grade in html_text


class TestReportHTMLNoFindings:
    """Phase 6C-2: empty fixtures (no findings, no metadata) still render
    cleanly with no orphan Top Actions header and no broken cost section."""

    @pytest.fixture
    def empty_findings_environment(self):
        empty_results = {
            tool_key: {
                "tool": tool_key,
                "scores": {
                    "overall_score": 80,
                    "grade": "B-",
                    "total_findings": 0,
                    "severity_counts": {},
                    "breakdown": {},
                },
                "errors": [],
            }
            for tool_key in TOOL_ORDER
        }
        with (
            patch("kulshan.session.create_session", return_value=MagicMock()),
            patch("kulshan.session.get_account_id", return_value=ACCOUNT_ID),
            patch("kulshan.session.get_enabled_regions", return_value=list(REGIONS)),
            patch("kulshan.orchestrator.run_all_scans", return_value=empty_results),
        ):
            yield

    def test_html_renders_when_no_findings(
        self, empty_findings_environment, tmp_path
    ):
        out = tmp_path / "report.html"
        runner = CliRunner()
        result = runner.invoke(
            main, ["report", "--format", "html", "--output", str(out)]
        )
        assert result.exit_code == 0
        assert out.exists()

    def test_html_does_not_emit_top_actions_when_empty(
        self, empty_findings_environment, tmp_path
    ):
        out = tmp_path / "report.html"
        runner = CliRunner()
        runner.invoke(
            main, ["report", "--format", "html", "--output", str(out)]
        )
        html_text = out.read_text(encoding="utf-8")
        # Tighten to rendered elements; CSS comments contain the same words.
        assert '<h2 class="section-title">Top Actions</h2>' not in html_text
        assert '<table class="top-actions-table">' not in html_text

    def test_html_cost_section_renders_without_overlap_or_cards(
        self, empty_findings_environment, tmp_path
    ):
        out = tmp_path / "report.html"
        runner = CliRunner()
        runner.invoke(
            main, ["report", "--format", "html", "--output", str(out)]
        )
        html_text = out.read_text(encoding="utf-8")
        # Tighten to rendered elements; CSS comments contain the same words.
        assert '<div class="overlap-summary">' not in html_text
        assert (
            '<h3 class="findings-subsection">Kulshan statistical anomalies</h3>'
            not in html_text
        )
        assert (
            '<h3 class="findings-subsection">AWS-native Cost Anomaly Detection</h3>'
            not in html_text
        )
        assert '<div class="finding-card">' not in html_text

    def test_html_existing_tool_labels_still_present(
        self, empty_findings_environment, tmp_path
    ):
        out = tmp_path / "report.html"
        runner = CliRunner()
        runner.invoke(
            main, ["report", "--format", "html", "--output", str(out)]
        )
        html_text = out.read_text(encoding="utf-8")
        for tool_key, label in TOOL_LABELS.items():
            assert label in html_text
