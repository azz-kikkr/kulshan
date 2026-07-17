"""
Kulshan data models.

All dataclasses use ``slots=True`` for efficiency.  Decimal values are
serialised as strings; timestamps as ISO-8601 strings.
"""

from __future__ import annotations

import hashlib
import json
import platform
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    """Finding severity level."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    def rank(self) -> int:
        """Return a numeric rank (lower is more severe)."""
        _ranks = {
            Severity.CRITICAL: 0,
            Severity.HIGH: 1,
            Severity.MEDIUM: 2,
            Severity.LOW: 3,
            Severity.INFO: 4,
        }
        return _ranks[self]


class Category(str, Enum):
    """Scan category."""

    COST = "cost"
    SECURITY = "security"
    SWEEP = "sweep"
    DR = "dr"
    AGE = "age"
    DRIFT = "drift"
    TAG = "tag"
    PULSE = "pulse"
    LIMIT = "limit"
    TOPO = "topo"


class Tier(str, Enum):
    """License tier."""

    FREE = "free"
    PRO = "pro"
    TEAM = "team"
    ENTERPRISE = "enterprise"

    def rank(self) -> int:
        """Return a numeric rank (higher is more capable)."""
        _ranks = {
            Tier.FREE: 0,
            Tier.PRO: 1,
            Tier.TEAM: 2,
            Tier.ENTERPRISE: 3,
        }
        return _ranks[self]

    def includes(self, other: Tier) -> bool:
        """Return True if *self* includes all capabilities of *other*."""
        return self.rank() >= other.rank()


class Confidence(str, Enum):
    """Confidence level for a finding."""

    DEFINITE = "definite"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _ts_to_str(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def _str_to_ts(val: Optional[str]) -> Optional[datetime]:
    if val is None:
        return None
    return datetime.fromisoformat(val)


def _decimal_to_str(d: Optional[Decimal]) -> Optional[str]:
    if d is None:
        return None
    return str(d)


def _str_to_decimal(val: Optional[str]) -> Optional[Decimal]:
    if val is None:
        return None
    return Decimal(val)


# ---------------------------------------------------------------------------
# Fingerprint / ID utilities (relocated from findings.py)
# ---------------------------------------------------------------------------

# Severity → score-impact table (informational; does not feed pack score yet).
SEVERITY_SCORE_IMPACT: Dict[str, int] = {
    "critical": -15,
    "high": -10,
    "medium": -5,
    "low": -2,
    "info": 0,
}

VALID_SEVERITY = ("critical", "high", "medium", "low", "info")
VALID_EFFORT = ("trivial", "low", "medium", "high")
VALID_RISK = ("safe", "low", "medium", "high")


def _iso_week(d: Any) -> str:
    """Return an ISO week string like ``2026-W17`` for a date / datetime / ISO string.

    Used by :func:`compute_fingerprint` so an anomaly recurring across consecutive
    days inside the same ISO week produces the same fingerprint (Phase 6 Q2:
    week-granularity for issue identity).
    """
    if d is None or d == "":
        return ""
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d.replace("Z", "+00:00"))
        except ValueError:
            return d
    if isinstance(d, datetime):
        d = d.date()
    if isinstance(d, date):
        iso_year, iso_week, _ = d.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if hasattr(d, "to_pydatetime"):
        try:
            return _iso_week(d.to_pydatetime())
        except Exception:
            return str(d)
    return str(d)


def compute_fingerprint(
    *,
    pack: str,
    kind: str,
    account: Optional[str],
    service: Optional[str],
    usage_type: Optional[str],
    period: Any,
) -> str:
    """Stable hash for a finding. Identical findings across scans share fingerprints.

    Period is normalized to ISO-week granularity so the same anomaly across
    consecutive days produces one issue identity rather than five.
    """
    period_iso = _iso_week(period)
    raw = f"{pack}|{kind}|{account or ''}|{service or ''}|{usage_type or ''}|{period_iso}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def make_finding_id(*, pack: str, kind: str, fingerprint: str) -> str:
    """Build a deterministic finding id like ``cost-anomaly_statistical-a1b2c3d4e5f6...``."""
    return f"{pack}-{kind}-{fingerprint}"


def simple_fingerprint(pack: str, kind: str, resource_id: str) -> str:
    """Simple stable fingerprint for packs that don't need the full compute_fingerprint().

    Used by sweep, dr, age, drift, tag, pulse, topo packs. Produces a 16-char
    hex digest that's stable across runs for the same resource.
    """
    raw = f"{pack}|{kind}|{resource_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _json_safe(obj: Any) -> Any:
    """Best-effort coerce nested structures to JSON-serializable types."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if hasattr(obj, "isoformat"):
        try:
            return obj.isoformat()
        except Exception:
            return str(obj)
    if hasattr(obj, "item"):
        try:
            return obj.item()
        except Exception:
            return str(obj)
    return str(obj)


# ---------------------------------------------------------------------------
# Schema Version Parsers
# ---------------------------------------------------------------------------

# Confidence enum → float mapping (used by v1.0 parser / adapter)
CONFIDENCE_ENUM_MAP: Dict[str, float] = {
    "definite": 1.0,
    "high": 0.85,
    "medium": 0.6,
    "low": 0.35,
}


def _parse_v1(d: Dict[str, Any]) -> Dict[str, Any]:
    """Map v1.0 fields (old models.py shape) to v2.0 canonical shape."""
    out: Dict[str, Any] = {}

    # Identity
    out["id"] = d.get("id", d.get("id", ""))
    out["pack"] = d.get("pack", d.get("tool", ""))
    out["kind"] = d.get("kind", d.get("check_id", ""))
    out["fingerprint"] = d.get("fingerprint", "")

    # Severity & impact
    out["title"] = d.get("title", "")
    sev_raw = d.get("severity", "info")
    if isinstance(sev_raw, Severity):
        out["severity"] = sev_raw.value
    elif hasattr(sev_raw, "value"):
        out["severity"] = sev_raw.value.lower()
    else:
        out["severity"] = str(sev_raw).lower()

    # Confidence: map enum string → float
    conf_raw = d.get("confidence", 0.5)
    if isinstance(conf_raw, (int, float)):
        out["confidence"] = float(conf_raw)
    elif isinstance(conf_raw, str):
        out["confidence"] = CONFIDENCE_ENUM_MAP.get(conf_raw.lower(), 0.5)
    else:
        out["confidence"] = 0.5

    # Impact
    impact_raw = d.get("estimated_monthly_impact", d.get("monthly_impact_usd"))
    if impact_raw is not None:
        try:
            out["estimated_monthly_impact"] = float(str(impact_raw))
        except (ValueError, TypeError):
            out["estimated_monthly_impact"] = 0.0
    else:
        out["estimated_monthly_impact"] = 0.0

    # score_impact derived from severity
    sev_val = out["severity"]
    out["score_impact"] = SEVERITY_SCORE_IMPACT.get(sev_val, 0)

    # Effort: map effort_minutes → categorical or use effort string
    if "effort" in d and d["effort"] in VALID_EFFORT:
        out["effort"] = d["effort"]
    elif "effort_minutes" in d and d["effort_minutes"] is not None:
        mins = int(d["effort_minutes"])
        if mins <= 15:
            out["effort"] = "trivial"
        elif mins <= 60:
            out["effort"] = "low"
        elif mins <= 240:
            out["effort"] = "medium"
        else:
            out["effort"] = "high"
    else:
        out["effort"] = "medium"

    # Risk
    out["risk"] = d.get("risk", "safe")
    if out["risk"] not in VALID_RISK:
        out["risk"] = "safe"

    # Location
    out["account_id"] = d.get("account_id", d.get("account"))
    out["region"] = d.get("region")
    out["resource_arn"] = d.get("resource_arn")
    out["resource_type"] = d.get("resource_type")
    out["service"] = d.get("service")

    # Explanation
    out["description"] = d.get("description", d.get("why_it_matters", ""))
    out["evidence"] = d.get("evidence", {})
    out["recommended_action"] = d.get("recommended_action", d.get("remediation_text", ""))

    # Metadata
    out["compliance_frameworks"] = list(d.get("compliance_frameworks", []))
    detected_raw = d.get("detected_at")
    out["detected_at"] = detected_raw  # pass through, will be parsed in from_dict
    out["schema_version"] = "2.0"

    return out


def _parse_v2(d: Dict[str, Any]) -> Dict[str, Any]:
    """Identity parser for current schema version."""
    return d


VERSION_PARSERS: Dict[str, Any] = {
    "1.0": _parse_v1,
    "2.0": _parse_v2,
}


# ---------------------------------------------------------------------------
# Finding (Canonical — frozen)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    """Canonical audit finding emitted by all packs.

    Immutable. Validated at construction time. Serializes via ``to_dict()``
    and reconstructs via ``from_dict(d)`` with schema version dispatch.
    """

    # Identity
    id: str
    pack: str
    kind: str
    fingerprint: str

    # Severity & impact
    title: str
    severity: Severity
    score_impact: int
    estimated_monthly_impact: float
    confidence: float
    effort: str
    risk: str

    # Location (all optional)
    account_id: Optional[str] = None
    region: Optional[str] = None
    resource_arn: Optional[str] = None
    resource_type: Optional[str] = None
    service: Optional[str] = None

    # Explanation
    description: str = ""
    evidence: dict = field(default_factory=dict)
    recommended_action: str = ""

    # Metadata
    compliance_frameworks: list = field(default_factory=list)
    detected_at: Optional[datetime] = None
    schema_version: str = "2.0"

    def __post_init__(self) -> None:
        # Severity must be a Severity enum member
        if not isinstance(self.severity, Severity):
            raise ValueError(
                f"severity must be a Severity enum member, got {self.severity!r}"
            )
        # Confidence in [0.0, 1.0]
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {self.confidence}"
            )
        # Effort must be in valid set
        if self.effort not in VALID_EFFORT:
            raise ValueError(
                f"effort must be one of {VALID_EFFORT}, got {self.effort!r}"
            )
        # Risk must be in valid set
        if self.risk not in VALID_RISK:
            raise ValueError(
                f"risk must be one of {VALID_RISK}, got {self.risk!r}"
            )
        # score_impact must match the severity mapping
        expected_impact = SEVERITY_SCORE_IMPACT[self.severity.value]
        if self.score_impact != expected_impact:
            raise ValueError(
                f"score_impact must be {expected_impact} for severity "
                f"{self.severity.value!r}, got {self.score_impact}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict representation."""
        return {
            "id": self.id,
            "pack": self.pack,
            "kind": self.kind,
            "fingerprint": self.fingerprint,
            "title": self.title,
            "severity": self.severity.value,
            "score_impact": self.score_impact,
            "estimated_monthly_impact": self.estimated_monthly_impact,
            "confidence": self.confidence,
            "effort": self.effort,
            "risk": self.risk,
            "account_id": self.account_id,
            "region": self.region,
            "resource_arn": self.resource_arn,
            "resource_type": self.resource_type,
            "service": self.service,
            "description": self.description,
            "evidence": _json_safe(self.evidence),
            "recommended_action": self.recommended_action,
            "compliance_frameworks": list(self.compliance_frameworks),
            "detected_at": _ts_to_str(self.detected_at),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Finding:
        """Reconstruct a Finding from a dict with schema version dispatch.

        - If ``schema_version`` is absent, treats as "1.0"
        - Raises ``ValueError`` for unknown schema versions
        - Raises ``KeyError`` for missing required fields
        """
        schema_version = d.get("schema_version", "1.0")

        if schema_version not in VERSION_PARSERS:
            raise ValueError(
                f"Unsupported schema version: {schema_version!r}. "
                f"Supported versions: {sorted(VERSION_PARSERS.keys())}"
            )

        # Apply version-specific parser
        parsed = VERSION_PARSERS[schema_version](d)

        # Extract required fields (raises KeyError if missing)
        required_keys = (
            "id", "pack", "kind", "fingerprint", "title", "severity",
            "score_impact", "estimated_monthly_impact", "confidence",
            "effort", "risk",
        )
        for key in required_keys:
            if key not in parsed:
                raise KeyError(
                    f"Required field {key!r} missing from finding dict"
                )

        # Parse severity enum
        sev_raw = parsed["severity"]
        if isinstance(sev_raw, Severity):
            severity = sev_raw
        else:
            severity = Severity(str(sev_raw).lower())

        # Parse detected_at
        detected_raw = parsed.get("detected_at")
        if detected_raw is None:
            detected_at = None
        elif isinstance(detected_raw, datetime):
            detected_at = detected_raw
        elif isinstance(detected_raw, str):
            detected_at = datetime.fromisoformat(detected_raw)
        else:
            detected_at = None

        return cls(
            id=parsed["id"],
            pack=parsed["pack"],
            kind=parsed["kind"],
            fingerprint=parsed["fingerprint"],
            title=parsed["title"],
            severity=severity,
            score_impact=int(parsed["score_impact"]),
            estimated_monthly_impact=float(parsed["estimated_monthly_impact"]),
            confidence=float(parsed["confidence"]),
            effort=parsed["effort"],
            risk=parsed["risk"],
            account_id=parsed.get("account_id"),
            region=parsed.get("region"),
            resource_arn=parsed.get("resource_arn"),
            resource_type=parsed.get("resource_type"),
            service=parsed.get("service"),
            description=parsed.get("description", ""),
            evidence=parsed.get("evidence") if isinstance(parsed.get("evidence"), dict) else {},
            recommended_action=parsed.get("recommended_action", ""),
            compliance_frameworks=list(parsed.get("compliance_frameworks", [])),
            detected_at=detected_at,
            schema_version=parsed.get("schema_version", "2.0"),
        )


# ---------------------------------------------------------------------------
# CategoryScore
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CategoryScore:
    """Per-category score within a scan result."""

    category: Category
    score: int  # 0-100
    grade: str
    finding_counts_by_severity: Dict[str, int] = field(default_factory=dict)
    total_monthly_impact_usd: Optional[Decimal] = None
    metrics: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def score_to_grade(score: int) -> str:
        """Map a 0-100 score to a letter grade."""
        if score >= 97:
            return "A+"
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "score": self.score,
            "grade": self.grade,
            "finding_counts_by_severity": dict(self.finding_counts_by_severity),
            "total_monthly_impact_usd": _decimal_to_str(self.total_monthly_impact_usd),
            "metrics": dict(self.metrics),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> CategoryScore:
        return cls(
            category=Category(d["category"]),
            score=d["score"],
            grade=d["grade"],
            finding_counts_by_severity=dict(d.get("finding_counts_by_severity", {})),
            total_monthly_impact_usd=_str_to_decimal(d.get("total_monthly_impact_usd")),
            metrics=dict(d.get("metrics", {})),
        )


# ---------------------------------------------------------------------------
# ScanResult
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ScanResult:
    """Normalised output from a single sub-tool scan."""

    scan_id: str
    category: Category
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    account_id: str
    regions: List[str]
    score: CategoryScore
    findings: List[Any] = field(default_factory=list)
    summary_stats: Dict[str, Any] = field(default_factory=dict)
    tool_version: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    schema_version: str = "1.0"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "category": self.category.value,
            "started_at": _ts_to_str(self.started_at),
            "completed_at": _ts_to_str(self.completed_at),
            "duration_seconds": self.duration_seconds,
            "account_id": self.account_id,
            "regions": list(self.regions),
            "score": self.score.to_dict(),
            "findings": [f.to_dict() for f in self.findings],
            "summary_stats": dict(self.summary_stats),
            "tool_version": self.tool_version,
            "errors": list(self.errors),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ScanResult:
        return cls(
            scan_id=d["scan_id"],
            category=Category(d["category"]),
            started_at=_str_to_ts(d["started_at"]),  # type: ignore[arg-type]
            completed_at=_str_to_ts(d["completed_at"]),  # type: ignore[arg-type]
            duration_seconds=d["duration_seconds"],
            account_id=d["account_id"],
            regions=list(d["regions"]),
            score=CategoryScore.from_dict(d["score"]),
            findings=[Finding.from_dict(f) for f in d.get("findings", []) if isinstance(f, dict)],
            summary_stats=dict(d.get("summary_stats", {})),
            tool_version=d.get("tool_version"),
            errors=list(d.get("errors", [])),
            schema_version=d.get("schema_version", "1.0"),
        )





# ---------------------------------------------------------------------------
# RemediationAction
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RemediationAction:
    """Unified ranked remediation item."""

    finding_id: str
    tool: str
    title: str
    rationale: Optional[str] = None
    monthly_impact_usd: Optional[Decimal] = None
    effort_minutes: Optional[int] = None
    priority_score: float = 0.0
    tier_required: Tier = Tier.FREE
    code_snippet: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "tool": self.tool,
            "title": self.title,
            "rationale": self.rationale,
            "monthly_impact_usd": _decimal_to_str(self.monthly_impact_usd),
            "effort_minutes": self.effort_minutes,
            "priority_score": self.priority_score,
            "tier_required": self.tier_required.value,
            "code_snippet": self.code_snippet,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> RemediationAction:
        return cls(
            finding_id=d["finding_id"],
            tool=d["tool"],
            title=d["title"],
            rationale=d.get("rationale"),
            monthly_impact_usd=_str_to_decimal(d.get("monthly_impact_usd")),
            effort_minutes=d.get("effort_minutes"),
            priority_score=d.get("priority_score", 0.0),
            tier_required=Tier(d.get("tier_required", "free")),
            code_snippet=d.get("code_snippet"),
        )


# ---------------------------------------------------------------------------
# CombinedScanResult
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CombinedScanResult:
    """Merged output of all sub-tool scans."""

    combined_scan_id: str
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    account_ids: List[str]
    regions: List[str]
    category_results: Dict[str, ScanResult] = field(default_factory=dict)
    overall_score: int = 0
    overall_grade: str = "F"
    ranked_remediations: List[RemediationAction] = field(default_factory=list)
    tier_at_scan: Tier = Tier.FREE
    suite_version: str = "0.1.0"

    # -- JSON helpers -------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "combined_scan_id": self.combined_scan_id,
            "started_at": _ts_to_str(self.started_at),
            "completed_at": _ts_to_str(self.completed_at),
            "duration_seconds": self.duration_seconds,
            "account_ids": list(self.account_ids),
            "regions": list(self.regions),
            "category_results": {
                k: v.to_dict() for k, v in self.category_results.items()
            },
            "overall_score": self.overall_score,
            "overall_grade": self.overall_grade,
            "ranked_remediations": [r.to_dict() for r in self.ranked_remediations],
            "tier_at_scan": self.tier_at_scan.value,
            "suite_version": self.suite_version,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> CombinedScanResult:
        return cls(
            combined_scan_id=d["combined_scan_id"],
            started_at=_str_to_ts(d["started_at"]),  # type: ignore[arg-type]
            completed_at=_str_to_ts(d["completed_at"]),  # type: ignore[arg-type]
            duration_seconds=d["duration_seconds"],
            account_ids=list(d["account_ids"]),
            regions=list(d["regions"]),
            category_results={
                k: ScanResult.from_dict(v)
                for k, v in d.get("category_results", {}).items()
            },
            overall_score=d.get("overall_score", 0),
            overall_grade=d.get("overall_grade", "F"),
            ranked_remediations=[
                RemediationAction.from_dict(r)
                for r in d.get("ranked_remediations", [])
            ],
            tier_at_scan=Tier(d.get("tier_at_scan", "free")),
            suite_version=d.get("suite_version", "0.1.0"),
        )

    def to_json(self) -> str:
        """Serialise to a JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> CombinedScanResult:
        """Deserialise from a JSON string."""
        return cls.from_dict(json.loads(json_str))


# ---------------------------------------------------------------------------
# LicenseInfo (frozen)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class LicenseInfo:
    """Decoded JWT payload representing the current license state."""

    tier: Tier
    accounts_allowed: int
    issued_at: datetime
    expires_at: datetime
    grace_expires_at: datetime
    customer_email_hash: str = ""
    license_key_last4: str = ""
    in_grace_period: bool = False
    valid: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier.value,
            "accounts_allowed": self.accounts_allowed,
            "issued_at": _ts_to_str(self.issued_at),
            "expires_at": _ts_to_str(self.expires_at),
            "grace_expires_at": _ts_to_str(self.grace_expires_at),
            "customer_email_hash": self.customer_email_hash,
            "license_key_last4": self.license_key_last4,
            "in_grace_period": self.in_grace_period,
            "valid": self.valid,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> LicenseInfo:
        return cls(
            tier=Tier(d["tier"]),
            accounts_allowed=d["accounts_allowed"],
            issued_at=_str_to_ts(d["issued_at"]),  # type: ignore[arg-type]
            expires_at=_str_to_ts(d["expires_at"]),  # type: ignore[arg-type]
            grace_expires_at=_str_to_ts(d["grace_expires_at"]),  # type: ignore[arg-type]
            customer_email_hash=d.get("customer_email_hash", ""),
            license_key_last4=d.get("license_key_last4", ""),
            in_grace_period=d.get("in_grace_period", False),
            valid=d.get("valid", True),
        )

    @classmethod
    def free(cls) -> LicenseInfo:
        """Return a default free-tier license."""
        now = datetime.now(timezone.utc)
        return cls(
            tier=Tier.FREE,
            accounts_allowed=1,
            issued_at=now,
            expires_at=now,
            grace_expires_at=now,
            customer_email_hash="",
            license_key_last4="",
            in_grace_period=False,
            valid=True,
        )


# ---------------------------------------------------------------------------
# ScanHistoryRecord
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ScanHistoryRecord:
    """SQLite row for scan history."""

    combined_scan_id: str
    timestamp: datetime
    account_ids_json: str
    regions_json: str
    tier_at_scan: Tier
    suite_version: str
    duration_seconds: float
    overall_score: int
    overall_grade: str
    cost_score: Optional[int] = None
    security_score: Optional[int] = None
    sweep_score: Optional[int] = None
    total_findings: int = 0
    critical_findings: int = 0
    high_findings: int = 0
    total_monthly_waste_usd: Optional[Decimal] = None
    total_monthly_cost_usd: Optional[Decimal] = None
    full_result_json: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "combined_scan_id": self.combined_scan_id,
            "timestamp": _ts_to_str(self.timestamp),
            "account_ids_json": self.account_ids_json,
            "regions_json": self.regions_json,
            "tier_at_scan": self.tier_at_scan.value,
            "suite_version": self.suite_version,
            "duration_seconds": self.duration_seconds,
            "overall_score": self.overall_score,
            "overall_grade": self.overall_grade,
            "cost_score": self.cost_score,
            "security_score": self.security_score,
            "sweep_score": self.sweep_score,
            "total_findings": self.total_findings,
            "critical_findings": self.critical_findings,
            "high_findings": self.high_findings,
            "total_monthly_waste_usd": _decimal_to_str(self.total_monthly_waste_usd),
            "total_monthly_cost_usd": _decimal_to_str(self.total_monthly_cost_usd),
            "full_result_json": self.full_result_json,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ScanHistoryRecord:
        return cls(
            combined_scan_id=d["combined_scan_id"],
            timestamp=_str_to_ts(d["timestamp"]),  # type: ignore[arg-type]
            account_ids_json=d["account_ids_json"],
            regions_json=d["regions_json"],
            tier_at_scan=Tier(d["tier_at_scan"]),
            suite_version=d["suite_version"],
            duration_seconds=d["duration_seconds"],
            overall_score=d["overall_score"],
            overall_grade=d["overall_grade"],
            cost_score=d.get("cost_score"),
            security_score=d.get("security_score"),
            sweep_score=d.get("sweep_score"),
            total_findings=d.get("total_findings", 0),
            critical_findings=d.get("critical_findings", 0),
            high_findings=d.get("high_findings", 0),
            total_monthly_waste_usd=_str_to_decimal(d.get("total_monthly_waste_usd")),
            total_monthly_cost_usd=_str_to_decimal(d.get("total_monthly_cost_usd")),
            full_result_json=d.get("full_result_json"),
        )


# ---------------------------------------------------------------------------
# TelemetryEvent (frozen)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    """Opt-in telemetry ping (immutable)."""

    event_type: str
    command: str
    suite_version: str
    python_version: str
    os_family: str
    os_release: str
    success: bool
    duration_ms: Optional[float] = None
    error_class: Optional[str] = None
    tier: Tier = Tier.FREE
    install_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "command": self.command,
            "suite_version": self.suite_version,
            "python_version": self.python_version,
            "os_family": self.os_family,
            "os_release": self.os_release,
            "success": self.success,
            "duration_ms": self.duration_ms,
            "error_class": self.error_class,
            "tier": self.tier.value,
            "install_id": self.install_id,
            "timestamp": _ts_to_str(self.timestamp),
        }

    def to_json_body(self) -> str:
        """Return a JSON string ready for POST to the telemetry endpoint."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> TelemetryEvent:
        return cls(
            event_type=d["event_type"],
            command=d["command"],
            suite_version=d["suite_version"],
            python_version=d["python_version"],
            os_family=d["os_family"],
            os_release=d["os_release"],
            success=d["success"],
            duration_ms=d.get("duration_ms"),
            error_class=d.get("error_class"),
            tier=Tier(d.get("tier", "free")),
            install_id=d.get("install_id", ""),
            timestamp=_str_to_ts(d["timestamp"]),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# CIThresholds
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CIThresholds:
    """CI/CD pass/fail configuration."""

    fail_on_security_severity: Optional[Severity] = None
    fail_on_cost_increase_pct: Optional[float] = None
    fail_on_monthly_waste_usd: Optional[Decimal] = None
    fail_on_overall_score_below: Optional[int] = None
    fail_on_critical_findings_count: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fail_on_security_severity": (
                self.fail_on_security_severity.value
                if self.fail_on_security_severity
                else None
            ),
            "fail_on_cost_increase_pct": self.fail_on_cost_increase_pct,
            "fail_on_monthly_waste_usd": _decimal_to_str(self.fail_on_monthly_waste_usd),
            "fail_on_overall_score_below": self.fail_on_overall_score_below,
            "fail_on_critical_findings_count": self.fail_on_critical_findings_count,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> CIThresholds:
        sev = d.get("fail_on_security_severity")
        return cls(
            fail_on_security_severity=Severity(sev) if sev else None,
            fail_on_cost_increase_pct=d.get("fail_on_cost_increase_pct"),
            fail_on_monthly_waste_usd=_str_to_decimal(d.get("fail_on_monthly_waste_usd")),
            fail_on_overall_score_below=d.get("fail_on_overall_score_below"),
            fail_on_critical_findings_count=d.get("fail_on_critical_findings_count"),
        )


# ---------------------------------------------------------------------------
# SuiteConfig
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SuiteConfig:
    """Full parsed TOML configuration."""

    # [aws]
    aws_profile: Optional[str] = None
    aws_role_arn: Optional[str] = None
    aws_regions: List[str] = field(default_factory=lambda: ["us-east-1"])
    quick_scan_regions: List[str] = field(
        default_factory=lambda: ["us-east-1", "us-west-2", "eu-west-1"]
    )

    # [output]
    default_format: str = "html"
    report_dir: str = "."

    # [license]
    license_key: Optional[str] = None
    license_email: Optional[str] = None

    # [history]
    history_retention_days: int = 365
    history_db_path: Optional[str] = None

    # [telemetry]
    telemetry_enabled: bool = False
    crash_reporting: bool = False

    # [ci]
    ci_thresholds: CIThresholds = field(default_factory=CIThresholds)

    # [ui]
    colour: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "aws": {
                "profile": self.aws_profile,
                "role_arn": self.aws_role_arn,
                "regions": list(self.aws_regions),
                "quick_scan_regions": list(self.quick_scan_regions),
            },
            "output": {
                "default_format": self.default_format,
                "report_dir": self.report_dir,
            },
            "license": {
                "key": self.license_key,
                "email": self.license_email,
            },
            "history": {
                "retention_days": self.history_retention_days,
                "db_path": self.history_db_path,
            },
            "telemetry": {
                "enabled": self.telemetry_enabled,
                "crash_reporting": self.crash_reporting,
            },
            "ci": self.ci_thresholds.to_dict(),
            "ui": {
                "colour": self.colour,
            },
        }
