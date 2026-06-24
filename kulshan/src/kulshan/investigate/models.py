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
class Ec2InvestigationBrief:
    """The deterministic evidence needed for the first EC2 brief."""

    service: str
    previous_period: str
    current_period: str
    previous_cost: float
    current_cost: float
    delta: float
    delta_percent: float | None
    top_resources: list[DeltaRow]
    top_usage_types: list[DeltaRow]
    review_questions: list[str]
