"""Stable local data model for research runs and their evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class ResearchRequest:
    city: str
    state: str
    country: str
    roles: tuple[str, ...]
    profile: str = "technology"

    def __post_init__(self) -> None:
        if not all((self.city.strip(), self.state.strip(), self.country.strip())):
            raise ValueError("city, state, and country are all required")
        if not self.roles or not any(role.strip() for role in self.roles):
            raise ValueError("at least one role or technical interest is required")


@dataclass
class Finding:
    kind: str
    evidence: str
    source_url: str
    confidence: str
    suggestion: str


@dataclass
class CompanyResult:
    domain: str
    name: str
    status: str = "needs_review"
    sector: str = "unknown"
    location_verified: bool = False
    findings: list[Finding] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass
class ResearchReport:
    request: ResearchRequest
    companies: list[CompanyResult]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return asdict(self)
