"""Local JSON and Markdown report writers."""

from __future__ import annotations

import json
from pathlib import Path

from scout.models import ResearchReport
from scout.outreach import suggest_outreach_points


def write_report(report: ResearchReport, output: str) -> tuple[Path, Path]:
    markdown_path = Path(output)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = markdown_path.with_suffix(".json")
    json_path.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")

    location_line = (
        f"Location requested: {report.request.city}, {report.request.state}, {report.request.country}"
        if report.request.location_required
        else "Location requested: none - specific-company mode, location check skipped"
    )
    lines = [
        "# Prospect Scout report",
        "",
        location_line,
        f"Roles/interests: {', '.join(report.request.roles)}",
        f"Generated: {report.created_at}",
        "",
    ]
    for company in report.companies:
        location_status = (
            ("yes" if company.location_verified else "no")
            if company.location_checked
            else "skipped (specific-company mode)"
        )
        lines.extend([f"## {company.name}", "", f"Status: **{company.status}**  ", f"Sector: {company.sector}  ", f"Location verified: {location_status}", ""])
        for finding in company.findings:
            lines.extend([f"### {finding.kind.replace('_', ' ').title()}", "", f"Evidence: {finding.evidence}", "", f"Source: {finding.source_url}", "", f"Confidence: {finding.confidence}", "", f"Suggested next step: {finding.suggestion}", ""])
            points = suggest_outreach_points(finding)
            if points:
                lines.extend([f"Outreach angle: {points}", ""])
        for limitation in company.limitations:
            lines.extend([f"- Limitation: {limitation}", ""])
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return markdown_path, json_path
