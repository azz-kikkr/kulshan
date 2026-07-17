"""
Property-based tests for data model round-trip serialisation.

Feature: aws-ops-suite, Property 8: ScanResult JSON round-trip
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal

from hypothesis import given, settings, strategies as st

from kulshan.models import (
    Category,
    CategoryScore,
    CombinedScanResult,
    Finding,
    RemediationAction,
    ScanResult,
    Severity,
    Tier,
    SEVERITY_SCORE_IMPACT,
    VALID_EFFORT,
    VALID_RISK,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

severities = st.sampled_from(list(Severity))
categories = st.sampled_from(list(Category))
tiers = st.sampled_from(list(Tier))

# Decimals that survive string round-trip cleanly (finite, reasonable scale)
safe_decimals = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("999999.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Timestamps within a reasonable range, always UTC
timestamps = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
    timezones=st.just(timezone.utc),
)

safe_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z"), max_codepoint=0x7E),
    min_size=1,
    max_size=60,
)

# Short identifier-like strings
identifiers = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), max_codepoint=0x7A),
    min_size=1,
    max_size=30,
)

regions = st.sampled_from(["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"])
account_ids = st.from_regex(r"[0-9]{12}", fullmatch=True)

# Canonical effort and risk values
efforts = st.sampled_from(list(VALID_EFFORT))
risks = st.sampled_from(list(VALID_RISK))


@st.composite
def finding_strategy(draw):
    severity = draw(severities)
    fingerprint = draw(st.from_regex(r"[0-9a-f]{16}", fullmatch=True))
    pack = draw(st.sampled_from(["cost", "security", "sweep"]))
    kind = draw(identifiers)
    return Finding(
        id=draw(identifiers),
        pack=pack,
        kind=kind,
        fingerprint=fingerprint,
        title=draw(safe_text),
        severity=severity,
        score_impact=SEVERITY_SCORE_IMPACT[severity.value],
        estimated_monthly_impact=draw(st.floats(min_value=0.0, max_value=999999.99, allow_nan=False, allow_infinity=False)),
        confidence=draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)),
        effort=draw(efforts),
        risk=draw(risks),
        account_id=draw(st.one_of(st.none(), account_ids)),
        region=draw(st.one_of(st.none(), regions)),
        resource_arn=draw(st.one_of(st.none(), safe_text)),
        resource_type=draw(st.one_of(st.none(), safe_text)),
        service=draw(st.one_of(st.none(), safe_text)),
        description=draw(st.one_of(st.just(""), safe_text)),
        evidence=draw(st.just({})),
        recommended_action=draw(st.one_of(st.just(""), safe_text)),
        compliance_frameworks=draw(st.lists(safe_text, max_size=3)),
        detected_at=draw(st.one_of(st.none(), timestamps)),
        schema_version="2.0",
    )


@st.composite
def category_score_strategy(draw):
    cat = draw(categories)
    score = draw(st.integers(min_value=0, max_value=100))
    return CategoryScore(
        category=cat,
        score=score,
        grade=CategoryScore.score_to_grade(score),
        finding_counts_by_severity=draw(
            st.dictionaries(
                st.sampled_from(["critical", "high", "medium", "low", "info"]),
                st.integers(min_value=0, max_value=100),
                max_size=5,
            )
        ),
        total_monthly_impact_usd=draw(st.one_of(st.none(), safe_decimals)),
        metrics={},
    )


@st.composite
def scan_result_strategy(draw):
    cat = draw(categories)
    start = draw(timestamps)
    dur = draw(st.floats(min_value=0.1, max_value=600.0, allow_nan=False, allow_infinity=False))
    end = start + timedelta(seconds=dur)
    score = draw(category_score_strategy().filter(lambda cs: cs.category == cat))
    return ScanResult(
        scan_id=draw(identifiers),
        category=cat,
        started_at=start,
        completed_at=end,
        duration_seconds=dur,
        account_id=draw(account_ids),
        regions=draw(st.lists(regions, min_size=1, max_size=3, unique=True)),
        score=score,
        findings=draw(st.lists(finding_strategy(), max_size=3)),
        summary_stats={},
        tool_version=draw(st.one_of(st.none(), identifiers)),
        errors=draw(st.lists(safe_text, max_size=2)),
        schema_version="1.0",
    )


@st.composite
def remediation_strategy(draw):
    return RemediationAction(
        finding_id=draw(identifiers),
        tool=draw(st.sampled_from(["cost", "security", "sweep"])),
        title=draw(safe_text),
        rationale=draw(st.one_of(st.none(), safe_text)),
        monthly_impact_usd=draw(st.one_of(st.none(), safe_decimals)),
        effort_minutes=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=10000))),
        priority_score=draw(st.floats(min_value=0, max_value=1000, allow_nan=False, allow_infinity=False)),
        tier_required=draw(tiers),
        code_snippet=draw(st.one_of(st.none(), safe_text)),
    )


@st.composite
def combined_scan_result_strategy(draw):
    start = draw(timestamps)
    dur = draw(st.floats(min_value=0.1, max_value=600.0, allow_nan=False, allow_infinity=False))
    end = start + timedelta(seconds=dur)

    # Build category results, one per chosen category
    chosen = draw(st.lists(categories, min_size=1, max_size=3, unique=True))
    cat_results = {}
    for cat in chosen:
        sr = draw(scan_result_strategy().filter(lambda s: s.category == cat))
        cat_results[cat.value] = sr

    overall = draw(st.integers(min_value=0, max_value=100))

    return CombinedScanResult(
        combined_scan_id=draw(identifiers),
        started_at=start,
        completed_at=end,
        duration_seconds=dur,
        account_ids=draw(st.lists(account_ids, min_size=1, max_size=3, unique=True)),
        regions=draw(st.lists(regions, min_size=1, max_size=3, unique=True)),
        category_results=cat_results,
        overall_score=overall,
        overall_grade=CategoryScore.score_to_grade(overall),
        ranked_remediations=draw(st.lists(remediation_strategy(), max_size=3)),
        tier_at_scan=draw(tiers),
        suite_version="0.1.0",
    )


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------

class TestFindingRoundTrip:
    """**Validates: Requirements 4.3, 4.4**"""

    @settings(max_examples=100)
    @given(finding=finding_strategy())
    def test_finding_roundtrip(self, finding: Finding):
        """Finding.from_dict(f.to_dict()) preserves all fields."""
        d = finding.to_dict()
        restored = Finding.from_dict(d)

        assert restored.id == finding.id
        assert restored.pack == finding.pack
        assert restored.kind == finding.kind
        assert restored.fingerprint == finding.fingerprint
        assert restored.title == finding.title
        assert restored.severity == finding.severity
        assert restored.score_impact == finding.score_impact
        assert restored.estimated_monthly_impact == finding.estimated_monthly_impact
        assert restored.confidence == finding.confidence
        assert restored.effort == finding.effort
        assert restored.risk == finding.risk
        assert restored.account_id == finding.account_id
        assert restored.region == finding.region
        assert restored.resource_arn == finding.resource_arn
        assert restored.resource_type == finding.resource_type
        assert restored.service == finding.service
        assert restored.description == finding.description
        assert restored.evidence == finding.evidence
        assert restored.recommended_action == finding.recommended_action
        assert restored.compliance_frameworks == finding.compliance_frameworks
        assert restored.detected_at == finding.detected_at
        assert restored.schema_version == finding.schema_version


class TestCombinedScanResultRoundTrip:
    """
    Feature: aws-ops-suite, Property 8: ScanResult JSON round-trip

    **Validates: Requirements 4.3, 4.4**

    For any valid CombinedScanResult object, serialising to JSON via
    to_json() then deserialising via from_json() shall produce an
    equivalent CombinedScanResult object.
    """

    @settings(max_examples=100)
    @given(csr=combined_scan_result_strategy())
    def test_json_roundtrip(self, csr: CombinedScanResult):
        json_str = csr.to_json()
        restored = CombinedScanResult.from_json(json_str)

        assert restored.combined_scan_id == csr.combined_scan_id
        assert restored.duration_seconds == csr.duration_seconds
        assert restored.account_ids == csr.account_ids
        assert restored.regions == csr.regions
        assert restored.overall_score == csr.overall_score
        assert restored.overall_grade == csr.overall_grade
        assert restored.tier_at_scan == csr.tier_at_scan
        assert restored.suite_version == csr.suite_version

        # Category results
        assert set(restored.category_results.keys()) == set(csr.category_results.keys())
        for key in csr.category_results:
            orig_sr = csr.category_results[key]
            rest_sr = restored.category_results[key]
            assert rest_sr.scan_id == orig_sr.scan_id
            assert rest_sr.category == orig_sr.category
            assert rest_sr.score.score == orig_sr.score.score
            assert rest_sr.score.grade == orig_sr.score.grade
            assert len(rest_sr.findings) == len(orig_sr.findings)

        # Remediations
        assert len(restored.ranked_remediations) == len(csr.ranked_remediations)
