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
    # False for the specific-company path (scout inspect / webapp /inspect):
    # the human already knows and chose this company, so there is nothing to
    # verify - city/state/country may be blank and no location check runs.
    location_required: bool = True

    def __post_init__(self) -> None:
        if self.location_required and not all((self.city.strip(), self.state.strip(), self.country.strip())):
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
    # ISO-8601 string, when a fetched source declares its own last-modified
    # date (e.g. a sitemap <lastmod>); None when no such date was found.
    # Not yet populated by the research pipeline
    observed_at: str | None = None


@dataclass
class CompanyResult:
    domain: str
    name: str
    status: str = "needs_review"
    sector: str = "unknown"
    location_verified: bool = False
    # False whenever the request behind this result had location_required=False
    # (specific-company mode) - lets ranking/reporting/webapp say "skipped"
    # instead of misreporting an unattempted check as a failed one.
    location_checked: bool = True
    # Nullable: only populated when a coordinate source actually found this
    # company (OSM discovery, or schema.org structured data on its own site).
    # None means "no coordinate available", not "at 0,0".
    lat: float | None = None
    lon: float | None = None
    findings: list[Finding] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass
class ResearchReport:
    request: ResearchRequest
    companies: list[CompanyResult]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return asdict(self)
