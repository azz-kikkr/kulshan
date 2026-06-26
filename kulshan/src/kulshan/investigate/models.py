"""Investigation result models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeltaRow:
    """A grouped previous/current cost delta."""

    name: str
    previous_cost: float
    current_cost: float
    delta: float


@dataclass(frozen=True)
class EvidenceItem:
    """One available or missing evidence signal in an investigation brief."""

    label: str
    detail: str


@dataclass(frozen=True)
class TagCoverage:
    """Current-period tag coverage for the investigated EC2 slice."""

    tagged_cost: float
    untagged_cost: float
    owner_values: list[str]
    team_values: list[str]
    application_values: list[str]
    cost_center_values: list[str]
    environment_values: list[str]


@dataclass(frozen=True)
class Ec2InvestigationBrief:
    """The deterministic evidence needed for the first EC2 brief."""

    service: str
    previous_period: str
    current_period: str
    previous_cost: float
    current_cost: float
    delta: float
    delta_percent: float | None
    top_accounts: list[DeltaRow]
    top_regions: list[DeltaRow]
    top_resources: list[DeltaRow]
    top_usage_types: list[DeltaRow]
    tag_coverage: TagCoverage | None
    evidence_available: list[EvidenceItem]
    evidence_missing: list[EvidenceItem]
    review_questions: list[str]
