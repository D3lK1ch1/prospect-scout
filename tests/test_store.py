import tempfile
import unittest
from pathlib import Path

from scout.models import CompanyResult, Finding, ResearchReport, ResearchRequest
from scout.store import load_companies, save_report


def _report(companies: list[CompanyResult]) -> ResearchReport:
    request = ResearchRequest(city="Melbourne", state="VIC", country="Australia", roles=("web developer",))
    return ResearchReport(request=request, companies=companies)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmpdir.name) / "test.db")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_save_and_load_round_trip(self):
        company = CompanyResult(
            domain="acme.test",
            name="Acme",
            status="eligible",
            sector="technology",
            location_verified=True,
            location_checked=True,
            lat=-37.8136,
            lon=144.9631,
            findings=[
                Finding(
                    kind="advertised_role",
                    evidence="Web Developer listed",
                    source_url="https://acme.test/careers",
                    confidence="high",
                    suggestion="Reach out about the web developer role.",
                    observed_at="2026-09-01T00:00:00+00:00",
                )
            ],
            limitations=["Homepage fetch was slow"],
        )
        save_report(_report([company]), db_path=self.db_path)

        loaded = load_companies(db_path=self.db_path)

        self.assertEqual(len(loaded), 1)
        result = loaded[0]
        self.assertEqual(result.domain, "acme.test")
        self.assertEqual(result.name, "Acme")
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.sector, "technology")
        self.assertTrue(result.location_verified)
        self.assertTrue(result.location_checked)
        self.assertEqual(result.lat, -37.8136)
        self.assertEqual(result.lon, 144.9631)
        self.assertEqual(result.limitations, ["Homepage fetch was slow"])
        self.assertEqual(len(result.findings), 1)
        finding = result.findings[0]
        self.assertEqual(finding.kind, "advertised_role")
        self.assertEqual(finding.evidence, "Web Developer listed")
        self.assertEqual(finding.source_url, "https://acme.test/careers")
        self.assertEqual(finding.confidence, "high")
        self.assertEqual(finding.suggestion, "Reach out about the web developer role.")
        self.assertEqual(finding.observed_at, "2026-09-01T00:00:00+00:00")

    def test_null_coordinates_round_trip(self):
        company = CompanyResult(domain="known.test", name="known.test", location_checked=False)

        save_report(_report([company]), db_path=self.db_path)
        loaded = load_companies(db_path=self.db_path)

        self.assertEqual(len(loaded), 1)
        self.assertIsNone(loaded[0].lat)
        self.assertIsNone(loaded[0].lon)

    def test_rerun_same_domain_updates_in_place(self):
        first = CompanyResult(
            domain="acme.test",
            name="Acme",
            status="needs_review",
            findings=[
                Finding(
                    kind="case_study_role_signal",
                    evidence="old evidence",
                    source_url="https://acme.test",
                    confidence="low",
                    suggestion="old suggestion",
                )
            ],
        )
        save_report(_report([first]), db_path=self.db_path)

        second = CompanyResult(
            domain="acme.test",
            name="Acme",
            status="eligible",
            findings=[
                Finding(
                    kind="advertised_role",
                    evidence="new evidence",
                    source_url="https://acme.test/careers",
                    confidence="high",
                    suggestion="new suggestion",
                )
            ],
        )
        save_report(_report([second]), db_path=self.db_path)

        loaded = load_companies(db_path=self.db_path)

        self.assertEqual(len(loaded), 1, "rerun must replace the row, not add a second one")
        self.assertEqual(loaded[0].status, "eligible")
        self.assertEqual(len(loaded[0].findings), 1, "stale findings from the first save must not linger")
        self.assertEqual(loaded[0].findings[0].evidence, "new evidence")

    def test_load_companies_on_empty_store_returns_empty_list(self):
        self.assertEqual(load_companies(db_path=self.db_path), [])

    def test_multiple_companies_persist_independently(self):
        companies = [
            CompanyResult(
                domain="a.test",
                name="A",
                findings=[Finding(kind="x", evidence="a-evidence", source_url="https://a.test", confidence="low", suggestion="s")],
            ),
            CompanyResult(
                domain="b.test",
                name="B",
                findings=[Finding(kind="x", evidence="b-evidence", source_url="https://b.test", confidence="low", suggestion="s")],
            ),
            CompanyResult(domain="c.test", name="C", findings=[]),
        ]
        save_report(_report(companies), db_path=self.db_path)

        loaded = {company.domain: company for company in load_companies(db_path=self.db_path)}

        self.assertEqual(set(loaded), {"a.test", "b.test", "c.test"})
        self.assertEqual(loaded["a.test"].findings[0].evidence, "a-evidence")
        self.assertEqual(loaded["b.test"].findings[0].evidence, "b-evidence")
        self.assertEqual(loaded["c.test"].findings, [])


if __name__ == "__main__":
    unittest.main()
