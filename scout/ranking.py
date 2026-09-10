"""Ranks researched companies by verifiable evidence, not an opaque score.

Per docs/MVP_SPEC.md: rank by location confidence, requested-interest
relevance, evidence strength, and dated-signal recency; show component
reasons instead of a single number. Recency data comes from each finding's source URL's own
sitemap <lastmod>, when its page was discovered that way - most findings
still have none, which is an honest "no declared date", not a penalty.
"""

from __future__ import annotations

from datetime import datetime, timezone

from scout.models import CompanyResult

_STATUS_RANK = {"eligible": 2, "needs_review": 1, "rejected": 0}
# High confidence means a finding matched one of the requester's own typed
# interests, not just a profile default term - see analyse_company().
_CONFIDENCE_WEIGHT = {"high": 3, "medium": 2, "low": 1}

# The floor both an undeclared and an unparseable observed_at fall back to -
# ranks below any real declared date, never above one, and never below
# another company that has no dated evidence at all (they tie on this
# component and fall through to whichever ranked equal before it).
_NO_DATE = datetime.min.replace(tzinfo=timezone.utc)


def _evidence_score(company: CompanyResult) -> int:
    return sum(_CONFIDENCE_WEIGHT.get(finding.confidence, 0) for finding in company.findings)


def _parse_observed_at(value: str | None) -> datetime:
    if not value:
        return _NO_DATE
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _NO_DATE
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _freshest_observed_at(company: CompanyResult) -> str | None:
    """The raw observed_at string behind the company's most recent dated
    finding, or None if it has no dated evidence at all - kept as the
    original string (not the parsed datetime) so rank_reason() can display
    exactly what the sitemap declared, not a reformatted version of it.
    """
    dated = [finding.observed_at for finding in company.findings if finding.observed_at]
    return max(dated, key=_parse_observed_at) if dated else None


def _recency_score(company: CompanyResult) -> datetime:
    return _parse_observed_at(_freshest_observed_at(company))


def rank_key(company: CompanyResult) -> tuple[int, int, int, datetime]:
    # A skipped check (specific-company mode) isn't a location failure - don't
    # let it sink below companies that were actually checked and failed.
    location_component = 1 if (company.location_verified or not company.location_checked) else 0
    return (
        _STATUS_RANK.get(company.status, 0),
        location_component,
        _evidence_score(company),
        _recency_score(company),
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
    freshest = _freshest_observed_at(company)
    if freshest:
        parts.append(f"most recent dated evidence: {freshest}")
    return "; ".join(parts)


def rank_companies(companies: list[CompanyResult]) -> list[CompanyResult]:
    """Highest-priority company first: eligible over needs_review, location verified, then evidence strength.

    A stable sort, so companies tied on every criterion keep their original
    (discovery) order rather than being reshuffled.
    """
    return sorted(companies, key=rank_key, reverse=True)
