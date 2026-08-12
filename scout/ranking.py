"""Ranks researched companies by verifiable evidence, not an opaque score.

Per docs/MVP_SPEC.md: rank by location confidence, requested-interest
relevance, and evidence strength; show component reasons instead of a single
number. Dated-signal recency is deliberately not ranked on yet - the data
model has no per-finding timestamp (see docs/KNOWN_GAPS.md).
"""

from __future__ import annotations

from scout.models import CompanyResult

_STATUS_RANK = {"eligible": 2, "needs_review": 1, "rejected": 0}
# High confidence means a finding matched one of the requester's own typed
# interests, not just a profile default term - see analyse_company().
_CONFIDENCE_WEIGHT = {"high": 3, "medium": 2, "low": 1}


def _evidence_score(company: CompanyResult) -> int:
    return sum(_CONFIDENCE_WEIGHT.get(finding.confidence, 0) for finding in company.findings)


def rank_key(company: CompanyResult) -> tuple[int, int, int]:
    # A skipped check (specific-company mode) isn't a location failure - don't
    # let it sink below companies that were actually checked and failed.
    location_component = 1 if (company.location_verified or not company.location_checked) else 0
    return (
        _STATUS_RANK.get(company.status, 0),
        location_component,
        _evidence_score(company),
    )


def rank_reason(company: CompanyResult) -> str:
    """Human-readable component reasons behind a company's rank position."""
    if not company.location_checked:
        parts = ["location check skipped (specific-company mode)"]
    else:
        parts = ["location verified" if company.location_verified else "location not verified"]
    high = sum(1 for finding in company.findings if finding.confidence == "high")
    other = len(company.findings) - high
    if high:
        parts.append(f"{high} high-confidence finding{'s' if high != 1 else ''} matching a requested interest")
    if other:
        parts.append(f"{other} other finding{'s' if other != 1 else ''}")
    if not company.findings:
        parts.append("no findings")
    return "; ".join(parts)


def rank_companies(companies: list[CompanyResult]) -> list[CompanyResult]:
    """Highest-priority company first: eligible over needs_review, location verified, then evidence strength.

    A stable sort, so companies tied on every criterion keep their original
    (discovery) order rather than being reshuffled.
    """
    return sorted(companies, key=rank_key, reverse=True)
