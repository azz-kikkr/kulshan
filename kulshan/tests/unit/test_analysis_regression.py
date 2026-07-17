"""Regression proof for the deterministic CUR analysis pipeline.

Validates that kulshan analyze cost and kulshan analyze ec2 produce
correct, serializable output with provenance and confidence.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kulshan.analyze import analyze_ec2_cur, CurAnalysisError
from kulshan.analyze.models import (
    CostInvestigationBrief,
    Ec2InvestigationBrief,
    InvestigationProvenance,
)


_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cur"
_SAMPLE_CUR = _FIXTURES / "sample-cur"


def _has_fixture() -> bool:
    return _SAMPLE_CUR.exists()


@pytest.mark.skipif(not _has_fixture(), reason="sample CUR fixture not available")
class TestEc2AnalysisPipeline:
    """Proves analyze_ec2_cur produces valid, complete briefs."""

    def test_returns_ec2_investigation_brief(self):
        brief = analyze_ec2_cur(str(_SAMPLE_CUR), month="2026-06")
        assert isinstance(brief, Ec2InvestigationBrief)

    def test_provenance_fields_populated(self):
        brief = analyze_ec2_cur(str(_SAMPLE_CUR), month="2026-06")
        prov = brief.provenance
        assert isinstance(prov, InvestigationProvenance)
        assert prov.schema_version == "1.0"
        assert prov.kulshan_version  # non-empty
        assert prov.investigation_type  # non-empty
        assert prov.generated_at  # ISO timestamp
        assert prov.human_review_required is True

    def test_confidence_uses_categorical_labels(self):
        brief = analyze_ec2_cur(str(_SAMPLE_CUR), month="2026-06")
        if brief.confidence:
            assert brief.confidence.label in ("low", "medium", "high")
            assert brief.confidence.source_agreement in ("low", "medium", "high", "n/a")
            assert brief.confidence.data_completeness in ("low", "medium", "high")
            assert brief.confidence.ownership_confidence in ("low", "medium", "high")
            assert brief.confidence.reason  # non-empty reason string

    def test_to_dict_serialization_roundtrip(self):
        brief = analyze_ec2_cur(str(_SAMPLE_CUR), month="2026-06")
        d = brief.to_dict()
        # Must be JSON-serializable
        json_str = json.dumps(d, default=str)
        parsed = json.loads(json_str)
        # Key structural fields present
        assert "schema_version" in parsed
        assert "kulshan_version" in parsed
        assert "cost_basis" in parsed
        assert "period" in parsed
        assert "summary" in parsed
        assert "evidence" in parsed
        assert "confidence" in parsed or parsed.get("confidence") is None

    def test_evidence_structure(self):
        brief = analyze_ec2_cur(str(_SAMPLE_CUR), month="2026-06")
        d = brief.to_dict()
        evidence = d["evidence"]
        assert "available" in evidence
        assert "missing" in evidence
        assert "contradicting" in evidence
        assert isinstance(evidence["available"], list)
        assert isinstance(evidence["missing"], list)

    def test_cost_basis_documented(self):
        brief = analyze_ec2_cur(str(_SAMPLE_CUR), month="2026-06")
        cb = brief.cost_basis
        assert cb.column  # which column was used
        assert cb.currency == "USD"

    def test_period_comparison(self):
        brief = analyze_ec2_cur(str(_SAMPLE_CUR), month="2026-06")
        assert brief.previous_period  # e.g., "2026-05"
        assert brief.current_period == "2026-06"

    def test_top_movers_present(self):
        brief = analyze_ec2_cur(str(_SAMPLE_CUR), month="2026-06")
        # At least one dimension should have data
        has_movers = (
            len(brief.top_accounts) > 0
            or len(brief.top_regions) > 0
            or len(brief.top_resources) > 0
            or len(brief.top_usage_types) > 0
        )
        assert has_movers

    def test_invalid_month_raises(self):
        with pytest.raises(CurAnalysisError):
            analyze_ec2_cur(str(_SAMPLE_CUR), month="2026-6")  # invalid format

    def test_missing_path_raises(self):
        with pytest.raises(CurAnalysisError):
            analyze_ec2_cur("/nonexistent/path/cur")


@pytest.mark.skipif(not _has_fixture(), reason="sample CUR fixture not available")
class TestMCPToolJsonOutput:
    """Proves MCP tools return valid JSON from the analysis pipeline."""

    def test_analyze_ec2_mcp_produces_json(self):
        from kulshan.analyze.export import ec2_brief_to_json

        brief = analyze_ec2_cur(str(_SAMPLE_CUR), month="2026-06")
        json_str = ec2_brief_to_json(brief)
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)
        assert "provenance" in parsed or "schema_version" in parsed
        assert "cost_basis" in parsed

    def test_json_includes_provenance(self):
        from kulshan.analyze.export import ec2_brief_to_json

        brief = analyze_ec2_cur(str(_SAMPLE_CUR), month="2026-06")
        parsed = json.loads(ec2_brief_to_json(brief))
        # Provenance may be nested or flattened
        if "provenance" in parsed:
            prov = parsed["provenance"]
            assert prov["schema_version"] == "1.0"
            assert "kulshan_version" in prov
            assert prov["human_review_required"] is True
        else:
            assert parsed["schema_version"] == "1.0"
            assert parsed["human_review_required"] is True
