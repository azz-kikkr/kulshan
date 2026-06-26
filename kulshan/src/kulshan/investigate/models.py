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
    evidence_available: list[EvidenceItem]
    evidence_missing: list[EvidenceItem]
    review_questions: list[str]
