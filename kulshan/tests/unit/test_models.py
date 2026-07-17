"""
Unit tests for Kulshan.models
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kulshan.models import (
    Category,
    CategoryScore,
    CIThresholds,
    CombinedScanResult,
    Confidence,
    Finding,
    LicenseInfo,
    RemediationAction,
    ScanHistoryRecord,
    ScanResult,
    Severity,
    SuiteConfig,
    Tier,
)


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------

class TestSeverity:
    def test_values(self):
        assert Severity.CRITICAL.value == "critical"
        assert Severity.HIGH.value == "high"
        assert Severity.MEDIUM.value == "medium"
        assert Severity.LOW.value == "low"
        assert Severity.INFO.value == "info"

    def test_rank_ordering(self):
        assert Severity.CRITICAL.rank() < Severity.HIGH.rank()
        assert Severity.HIGH.rank() < Severity.MEDIUM.rank()
        assert Severity.MEDIUM.rank() < Severity.LOW.rank()
        assert Severity.LOW.rank() < Severity.INFO.rank()

    def test_rank_values(self):
        assert Severity.CRITICAL.rank() == 0
        assert Severity.INFO.rank() == 4


class TestCategory:
    def test_values(self):
        assert Category.COST.value == "cost"
        assert Category.SECURITY.value == "security"
        assert Category.SWEEP.value == "sweep"


class TestTier:
    def test_values(self):
        assert Tier.FREE.value == "free"
        assert Tier.PRO.value == "pro"
        assert Tier.TEAM.value == "team"
        assert Tier.ENTERPRISE.value == "enterprise"

    def test_rank_ordering(self):
        assert Tier.FREE.rank() < Tier.PRO.rank()
        assert Tier.PRO.rank() < Tier.TEAM.rank()
        assert Tier.TEAM.rank() < Tier.ENTERPRISE.rank()

    def test_includes(self):
        assert Tier.ENTERPRISE.includes(Tier.FREE)
        assert Tier.ENTERPRISE.includes(Tier.TEAM)
        assert Tier.PRO.includes(Tier.FREE)
        assert Tier.PRO.includes(Tier.PRO)
        assert not Tier.FREE.includes(Tier.PRO)
        assert not Tier.TEAM.includes(Tier.ENTERPRISE)


class TestConfidence:
    def test_values(self):
        assert Confidence.DEFINITE.value == "definite"
        assert Confidence.HIGH.value == "high"
        assert Confidence.MEDIUM.value == "medium"
        assert Confidence.LOW.value == "low"


# ---------------------------------------------------------------------------
# Finding tests
# ---------------------------------------------------------------------------

def _make_finding(**overrides) -> Finding:
    defaults = dict(
        id="f-001",
        pack="cost",
        kind="anomaly_statistical",
        fingerprint="abc1234567890def",
        title="Idle EC2 instance",
        severity=Severity.MEDIUM,
        score_impact=-5,
        estimated_monthly_impact=42.50,
        confidence=0.85,
        effort="low",
        risk="safe",
        resource_arn="arn:aws:ec2:us-east-1:123456789012:instance/i-abc123",
        resource_type="AWS::EC2::Instance",
        region="us-east-1",
        account_id="123456789012",
        service="Amazon Elastic Compute Cloud",
        description="Instance has <5% CPU utilisation for 14 days",
        evidence={},
        recommended_action="Stop or terminate the instance",
        compliance_frameworks=["CIS-1.4"],
        detected_at=datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        schema_version="2.0",
    )
    defaults.update(overrides)
    return Finding(**defaults)


class TestFinding:
    def test_construction(self):
        f = _make_finding()
        assert f.id == "f-001"
        assert f.severity == Severity.MEDIUM
        assert f.estimated_monthly_impact == 42.50

    def test_to_dict_from_dict_roundtrip(self):
        original = _make_finding()
        d = original.to_dict()
        restored = Finding.from_dict(d)
        assert restored.id == original.id
        assert restored.severity == original.severity
        assert restored.estimated_monthly_impact == original.estimated_monthly_impact
        assert restored.compliance_frameworks == original.compliance_frameworks
        assert restored.detected_at == original.detected_at

    def test_float_impact_serialised(self):
        f = _make_finding()
        d = f.to_dict()
        assert isinstance(d["estimated_monthly_impact"], float)
        assert d["estimated_monthly_impact"] == 42.50

    def test_none_optional_fields(self):
        f = _make_finding(
            resource_arn=None,
            region=None,
            account_id=None,
        )
        d = f.to_dict()
        assert d["resource_arn"] is None
        assert d["region"] is None
        assert d["account_id"] is None
        restored = Finding.from_dict(d)
        assert restored.resource_arn is None
        assert restored.region is None
        assert restored.account_id is None

    def test_json_serialisable(self):
        """The to_dict output must be JSON-serialisable."""
        f = _make_finding()
        text = json.dumps(f.to_dict())
        assert isinstance(text, str)


# ---------------------------------------------------------------------------
# CategoryScore tests
# ---------------------------------------------------------------------------

class TestCategoryScore:
    @pytest.mark.parametrize(
        "score, expected_grade",
        [
            (100, "A+"),
            (97, "A+"),
            (96, "A"),
            (90, "A"),
            (89, "B"),
            (80, "B"),
            (79, "C"),
            (70, "C"),
            (69, "D"),
            (60, "D"),
            (59, "F"),
            (0, "F"),
        ],
    )
    def test_score_to_grade(self, score: int, expected_grade: str):
        assert CategoryScore.score_to_grade(score) == expected_grade

    def test_to_dict_from_dict(self):
        cs = CategoryScore(
            category=Category.COST,
            score=85,
            grade="B",
            finding_counts_by_severity={"critical": 1, "high": 3},
            total_monthly_impact_usd=Decimal("1234.56"),
            metrics={"avg_utilisation": 0.65},
        )
        d = cs.to_dict()
        restored = CategoryScore.from_dict(d)
        assert restored.category == cs.category
        assert restored.score == cs.score
        assert restored.grade == cs.grade
        assert restored.total_monthly_impact_usd == cs.total_monthly_impact_usd


# ---------------------------------------------------------------------------
# CombinedScanResult tests
# ---------------------------------------------------------------------------

def _make_scan_result(category: Category = Category.COST) -> ScanResult:
    now = datetime.now(timezone.utc)
    return ScanResult(
        scan_id="scan-001",
        category=category,
        started_at=now,
        completed_at=now,
        duration_seconds=12.5,
        account_id="123456789012",
        regions=["us-east-1"],
        score=CategoryScore(
            category=category,
            score=82,
            grade="B",
        ),
        findings=[_make_finding()],
        tool_version="0.1.0",
    )


def _make_combined() -> CombinedScanResult:
    now = datetime.now(timezone.utc)
    return CombinedScanResult(
        combined_scan_id="combined-001",
        started_at=now,
        completed_at=now,
        duration_seconds=45.0,
        account_ids=["123456789012"],
        regions=["us-east-1"],
        category_results={
            "cost": _make_scan_result(Category.COST),
            "security": _make_scan_result(Category.SECURITY),
        },
        overall_score=80,
        overall_grade="B",
        ranked_remediations=[
            RemediationAction(
                finding_id="f-001",
                tool="cost",
                title="Stop idle instance",
                monthly_impact_usd=Decimal("42.50"),
                priority_score=85.0,
            )
        ],
        tier_at_scan=Tier.PRO,
        suite_version="0.1.0",
    )


class TestCombinedScanResult:
    def test_to_json_from_json_roundtrip(self):
        original = _make_combined()
        json_str = original.to_json()
        restored = CombinedScanResult.from_json(json_str)

        assert restored.combined_scan_id == original.combined_scan_id
        assert restored.overall_score == original.overall_score
        assert restored.overall_grade == original.overall_grade
        assert restored.tier_at_scan == original.tier_at_scan
        assert len(restored.category_results) == len(original.category_results)
        assert len(restored.ranked_remediations) == len(original.ranked_remediations)

    def test_to_dict_from_dict_roundtrip(self):
        original = _make_combined()
        d = original.to_dict()
        restored = CombinedScanResult.from_dict(d)
        assert restored.combined_scan_id == original.combined_scan_id
        assert restored.duration_seconds == original.duration_seconds
        assert restored.account_ids == original.account_ids

    def test_json_is_valid_json(self):
        c = _make_combined()
        parsed = json.loads(c.to_json())
        assert isinstance(parsed, dict)
        assert "combined_scan_id" in parsed


# ---------------------------------------------------------------------------
# LicenseInfo tests
# ---------------------------------------------------------------------------

class TestLicenseInfo:
    def test_free_factory(self):
        li = LicenseInfo.free()
        assert li.tier == Tier.FREE
        assert li.accounts_allowed == 1
        assert li.valid is True
        assert li.in_grace_period is False

    def test_frozen(self):
        li = LicenseInfo.free()
        with pytest.raises(AttributeError):
            li.tier = Tier.PRO  # type: ignore[misc]

    def test_to_dict_from_dict(self):
        li = LicenseInfo.free()
        d = li.to_dict()
        restored = LicenseInfo.from_dict(d)
        assert restored.tier == li.tier
        assert restored.accounts_allowed == li.accounts_allowed
        assert restored.valid == li.valid


# ---------------------------------------------------------------------------
 # SuiteConfig tests
# ---------------------------------------------------------------------------

class TestSuiteConfig:
    def test_defaults(self):
        cfg = SuiteConfig()
        assert cfg.aws_regions == ["us-east-1"]

    def test_to_dict_sections(self):
        cfg = SuiteConfig()
        d = cfg.to_dict()
        assert "aws" in d
        assert "ci" in d
        assert "ui" in d
