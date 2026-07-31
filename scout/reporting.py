"""Local JSON and Markdown report writers."""

from __future__ import annotations

import json
from pathlib import Path

from scout.models import ResearchReport


def write_report(report: ResearchReport, output: str) -> tuple[Path, Path]:
    markdown_path = Path(output)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = markdown_path.with_suffix(".json")
    json_path.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")

    lines = [
        "# Prospect Scout report",
        "",
        f"Location requested: {report.request.city}, {report.request.state}, {report.request.country}",
        f"Roles/interests: {', '.join(report.request.roles)}",
        f"Generated: {report.created_at}",
        "",
    ]
    for company in report.companies:
        lines.extend([f"## {company.name}", "", f"Status: **{company.status}**  ", f"Sector: {company.sector}  ", f"Location verified: {'yes' if company.location_verified else 'no'}", ""])
        for finding in company.findings:
            lines.extend([f"### {finding.kind.replace('_', ' ').title()}", "", f"Evidence: {finding.evidence}", "", f"Source: {finding.source_url}", "", f"Confidence: {finding.confidence}", "", f"Suggested next step: {finding.suggestion}", ""])
        for limitation in company.limitations:
            lines.extend([f"- Limitation: {limitation}", ""])
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return markdown_path, json_path
