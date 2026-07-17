"""Investigation result models.

This module defines the canonical evidence contract for Kulshan investigations.
All investigation briefs include provenance, confidence assessment, and explicit
human_review_required flags to ensure transparency and trust.

Schema version: 1.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from kulshan.__version__ import __version__

# -----------------------------------------------------------------------------
# Schema Constants
# -----------------------------------------------------------------------------

SCHEMA_VERSION = "1.0"

CONFIDENCE_LABELS = ("low", "medium", "high")
CONFIDENCE_COMPONENTS = ("low", "medium", "high", "n/a")
OWNER_BASIS_VALUES = (
    "account_mapping",
    "tag_value",
    "resource_naming_pattern",
    "inferred",
    "unknown",
)


# -----------------------------------------------------------------------------
# Core Building Blocks
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class DeltaRow:
    """A grouped previous/current cost delta."""

    name: str
    previous_cost: float
    current_cost: float
    delta: float

    @property
    def delta_percent(self) -> float | None:
        """Percentage change, or None if previous was zero."""
        if self.previous_cost == 0:
            return None
        return (self.delta / self.previous_cost) * 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "previous_cost": round(self.previous_cost, 2),
            "current_cost": round(self.current_cost, 2),
            "delta": round(self.delta, 2),
            "delta_percent": round(self.delta_percent, 1) if self.delta_percent is not None else None,
        }


@dataclass(frozen=True)
class EvidenceItem:
    """One available, missing, or contradicting evidence signal."""

    evidence_id: str
    label: str
    detail: str
    source: str = "cur_parquet"  # cur_parquet, cost_explorer, tag, cloudtrail, etc.

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "label": self.label,
            "detail": self.detail,
            "source": self.source,
        }


@dataclass(frozen=True)
class CostBasis:
    """Documents which cost column and accounting treatment was used."""

    column: str
    currency: str = "USD"
    includes_credits: bool = False
    includes_refunds: bool = False
    includes_taxes: bool = False
    includes_support: bool = False
    amortized: bool = False
    fallback_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "currency": self.currency,
            "includes_credits": self.includes_credits,
            "includes_refunds": self.includes_refunds,
            "includes_taxes": self.includes_taxes,
            "includes_support": self.includes_support,
            "amortized": self.amortized,
            "fallback_note": self.fallback_note,
        }


@dataclass(frozen=True)
class ConfidenceAssessment:
    """Structured confidence assessment with component scores.

    We explicitly avoid a single numeric confidence score (like 0.75) because
    such scores are meaningless without calibration against real investigations.
    Instead, we provide component assessments that explain why confidence is
    what it is.
    """

    label: str  # "low", "medium", "high"
    source_agreement: str  # "low", "medium", "high", "n/a"
    data_completeness: str  # "low", "medium", "high"
    ownership_confidence: str  # "low", "medium", "high"
    reason: str  # Human-readable explanation

    def __post_init__(self):
        if self.label not in CONFIDENCE_LABELS:
            raise ValueError(f"label must be one of {CONFIDENCE_LABELS}")
        if self.source_agreement not in CONFIDENCE_COMPONENTS:
            raise ValueError(f"source_agreement must be one of {CONFIDENCE_COMPONENTS}")
        if self.data_completeness not in CONFIDENCE_COMPONENTS:
            raise ValueError(f"data_completeness must be one of {CONFIDENCE_COMPONENTS}")
        if self.ownership_confidence not in CONFIDENCE_COMPONENTS:
            raise ValueError(f"ownership_confidence must be one of {CONFIDENCE_COMPONENTS}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "source_agreement": self.source_agreement,
            "data_completeness": self.data_completeness,
            "ownership_confidence": self.ownership_confidence,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class OwnerCandidate:
    """A proposed owner for the cost movement.

    This is explicitly a CANDIDATE, not a declaration. The confirmation_required
    field is always True unless the owner was confirmed through a customer-provided
    mapping file.
    """

    team: str | None
    account_id: str | None
    contact: str | None = None
    basis: str = "unknown"  # account_mapping, tag_value, resource_naming_pattern, inferred
    confirmation_required: bool = True

    def __post_init__(self):
        if self.basis not in OWNER_BASIS_VALUES:
            raise ValueError(f"basis must be one of {OWNER_BASIS_VALUES}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "team": self.team,
            "account_id": self.account_id,
            "contact": self.contact,
            "basis": self.basis,
            "confirmation_required": self.confirmation_required,
        }


@dataclass(frozen=True)
class TagCoverage:
    """Current-period tag coverage for the investigated slice."""

    tagged_cost: float
    untagged_cost: float
    owner_values: list[str]
    team_values: list[str]
    application_values: list[str]
    cost_center_values: list[str]
    environment_values: list[str]

    @property
    def coverage_percent(self) -> float:
        total = self.tagged_cost + self.untagged_cost
        if total == 0:
            return 0.0
        return (self.tagged_cost / total) * 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "tagged_cost": round(self.tagged_cost, 2),
            "untagged_cost": round(self.untagged_cost, 2),
            "coverage_percent": round(self.coverage_percent, 1),
            "owner_values": self.owner_values,
            "team_values": self.team_values,
            "application_values": self.application_values,
            "cost_center_values": self.cost_center_values,
            "environment_values": self.environment_values,
        }


# -----------------------------------------------------------------------------
# Investigation Provenance (Common to all briefs)
# -----------------------------------------------------------------------------


@dataclass
class InvestigationProvenance:
    """Metadata about how and when an investigation was produced.

    This provenance block appears in every investigation output and enables
    AI agents and humans to understand the context and limitations of the
    evidence presented.
    """

    schema_version: str = SCHEMA_VERSION
    kulshan_version: str = field(default_factory=lambda: __version__)
    investigation_type: str = "unknown"
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    data_through: str | None = None  # Latest date in the analyzed data
    human_review_required: bool = True  # Always true for V1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kulshan_version": self.kulshan_version,
            "investigation_type": self.investigation_type,
            "generated_at": self.generated_at,
            "data_through": self.data_through,
            "human_review_required": self.human_review_required,
        }


# -----------------------------------------------------------------------------
# Top-Mover Investigation Brief (Generic, Multi-Service)
# -----------------------------------------------------------------------------


@dataclass
class CostInvestigationBrief:
    """Generic top-mover investigation brief.

    This is the output of `kulshan analyze cost` -- it identifies where
    cost moved without making service-specific explanations. It's the
    "where to look" answer, not the "why it happened" answer.
    """

    # Provenance
    provenance: InvestigationProvenance

    # Cost basis
    cost_basis: CostBasis

    # Period
    previous_period: str
    current_period: str

    # Summary
    previous_cost: float
    current_cost: float
    delta: float
    delta_percent: float | None

    # Top movers
    top_services: list[DeltaRow]
    top_accounts: list[DeltaRow]
    top_regions: list[DeltaRow]
    top_usage_types: list[DeltaRow]

    # Evidence
    evidence_available: list[EvidenceItem]
    evidence_missing: list[EvidenceItem]
    evidence_contradicting: list[EvidenceItem] = field(default_factory=list)

    # Confidence
    confidence: ConfidenceAssessment | None = None

    # Owner (optional — only if pattern detected)
    owner_candidate: OwnerCandidate | None = None

    # Suggested next steps
    suggested_deep_dives: list[str] = field(default_factory=list)
    review_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.provenance.to_dict(),
            "cost_basis": self.cost_basis.to_dict(),
            "period": {
                "previous": self.previous_period,
                "current": self.current_period,
            },
            "summary": {
                "previous_cost_usd": round(self.previous_cost, 2),
                "current_cost_usd": round(self.current_cost, 2),
                "delta_usd": round(self.delta, 2),
                "delta_percent": round(self.delta_percent, 1) if self.delta_percent else None,
            },
            "top_services": [r.to_dict() for r in self.top_services],
            "top_accounts": [r.to_dict() for r in self.top_accounts],
            "top_regions": [r.to_dict() for r in self.top_regions],
            "top_usage_types": [r.to_dict() for r in self.top_usage_types],
            "evidence": {
                "available": [e.to_dict() for e in self.evidence_available],
                "missing": [e.to_dict() for e in self.evidence_missing],
                "contradicting": [e.to_dict() for e in self.evidence_contradicting],
            },
            "confidence": self.confidence.to_dict() if self.confidence else None,
            "owner_candidate": self.owner_candidate.to_dict() if self.owner_candidate else None,
            "suggested_deep_dives": self.suggested_deep_dives,
            "review_questions": self.review_questions,
        }


# -----------------------------------------------------------------------------
# EC2 Investigation Brief (Service-Specific)
# -----------------------------------------------------------------------------


@dataclass
class Ec2InvestigationBrief:
    """EC2-specific investigation brief with enhanced evidence contract.

    This is the output of `kulshan analyze ec2` -- it provides EC2-specific
    analysis including resource-level breakdown and tag coverage.
    """

    # Provenance
    provenance: InvestigationProvenance

    # Cost basis
    cost_basis: CostBasis

    # Service identification
    service: str = "EC2"

    # Period
    previous_period: str = ""
    current_period: str = ""

    # Summary
    previous_cost: float = 0.0
    current_cost: float = 0.0
    delta: float = 0.0
    delta_percent: float | None = None

    # Top movers
    top_accounts: list[DeltaRow] = field(default_factory=list)
    top_regions: list[DeltaRow] = field(default_factory=list)
    top_resources: list[DeltaRow] = field(default_factory=list)
    top_usage_types: list[DeltaRow] = field(default_factory=list)

    # Tag coverage
    tag_coverage: TagCoverage | None = None

    # Evidence
    evidence_available: list[EvidenceItem] = field(default_factory=list)
    evidence_missing: list[EvidenceItem] = field(default_factory=list)
    evidence_contradicting: list[EvidenceItem] = field(default_factory=list)

    # Confidence
    confidence: ConfidenceAssessment | None = None

    # Owner (optional)
    owner_candidate: OwnerCandidate | None = None

    # Review questions
    review_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.provenance.to_dict(),
            "cost_basis": self.cost_basis.to_dict(),
            "service": self.service,
            "period": {
                "previous": self.previous_period,
                "current": self.current_period,
            },
            "summary": {
                "previous_cost_usd": round(self.previous_cost, 2),
                "current_cost_usd": round(self.current_cost, 2),
                "delta_usd": round(self.delta, 2),
                "delta_percent": round(self.delta_percent, 1) if self.delta_percent else None,
            },
            "top_accounts": [r.to_dict() for r in self.top_accounts],
            "top_regions": [r.to_dict() for r in self.top_regions],
            "top_resources": [r.to_dict() for r in self.top_resources],
            "top_usage_types": [r.to_dict() for r in self.top_usage_types],
            "tag_coverage": self.tag_coverage.to_dict() if self.tag_coverage else None,
            "evidence": {
                "available": [e.to_dict() for e in self.evidence_available],
                "missing": [e.to_dict() for e in self.evidence_missing],
                "contradicting": [e.to_dict() for e in self.evidence_contradicting],
            },
            "confidence": self.confidence.to_dict() if self.confidence else None,
            "owner_candidate": self.owner_candidate.to_dict() if self.owner_candidate else None,
            "review_questions": self.review_questions,
        }


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def make_evidence_id(label: str, detail: str = "") -> str:
    """Generate a deterministic evidence ID from label and detail."""
    import hashlib
    return "ev-" + hashlib.md5(f"{label}:{detail}".encode()).hexdigest()[:8]
