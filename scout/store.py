"""Local SQLite persistence for researched companies.

Unit 1 of the map/persistence feature: gives a scan memory across reruns
instead of each run producing a throwaway report. Deliberately local-only
(matches README's "runs locally, shares nothing") - a plain file next to
reports/, no server, no remote storage.

Not wired into run_research()/webapp.py yet - that's a later unit. This
module only knows how to save a ResearchReport and load it back.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scout.models import CompanyResult, Finding, ResearchReport

DEFAULT_DB_PATH = "reports/prospect_scout.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    domain TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    sector TEXT NOT NULL,
    location_verified INTEGER NOT NULL,
    location_checked INTEGER NOT NULL,
    lat REAL,
    lon REAL,
    limitations TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS findings (
    company_domain TEXT NOT NULL REFERENCES companies(domain) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    evidence TEXT NOT NULL,
    source_url TEXT NOT NULL,
    confidence TEXT NOT NULL,
    suggestion TEXT NOT NULL,
    observed_at TEXT
);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


def save_report(report: ResearchReport, db_path: str = DEFAULT_DB_PATH) -> None:
    """Persist every company in `report`, replacing any prior row for the same domain.

    Re-saving an already-known domain replaces its row (and, via
    ON DELETE CASCADE, its findings) wholesale rather than appending a
    duplicate - the store always reflects each domain's most recent
    research. This is what makes "rerun the scan, the map still shows one
    pin per company" true instead of accumulating stale duplicates.
    """
    conn = _connect(db_path)
    try:
        for company in report.companies:
            conn.execute("DELETE FROM companies WHERE domain = ?", (company.domain,))
            conn.execute(
                """INSERT INTO companies
                   (domain, name, status, sector, location_verified, location_checked, lat, lon, limitations)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    company.domain,
                    company.name,
                    company.status,
                    company.sector,
                    int(company.location_verified),
                    int(company.location_checked),
                    company.lat,
                    company.lon,
                    json.dumps(company.limitations),
                ),
            )
            for finding in company.findings:
                conn.execute(
                    """INSERT INTO findings
                       (company_domain, kind, evidence, source_url, confidence, suggestion, observed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        company.domain,
                        finding.kind,
                        finding.evidence,
                        finding.source_url,
                        finding.confidence,
                        finding.suggestion,
                        finding.observed_at,
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def load_companies(db_path: str = DEFAULT_DB_PATH) -> list[CompanyResult]:
    """Every persisted company, each with its findings attached."""
    conn = _connect(db_path)
    try:
        results = []
        for row in conn.execute("SELECT * FROM companies").fetchall():
            finding_rows = conn.execute(
                "SELECT * FROM findings WHERE company_domain = ?", (row["domain"],)
            ).fetchall()
            findings = [
                Finding(
                    kind=f["kind"],
                    evidence=f["evidence"],
                    source_url=f["source_url"],
                    confidence=f["confidence"],
                    suggestion=f["suggestion"],
                    observed_at=f["observed_at"],
                )
                for f in finding_rows
            ]
            results.append(
                CompanyResult(
                    domain=row["domain"],
                    name=row["name"],
                    status=row["status"],
                    sector=row["sector"],
                    location_verified=bool(row["location_verified"]),
                    location_checked=bool(row["location_checked"]),
                    lat=row["lat"],
                    lon=row["lon"],
                    findings=findings,
                    limitations=json.loads(row["limitations"]),
                )
            )
        return results
    finally:
        conn.close()
