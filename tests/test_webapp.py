import functools
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from scout.fetcher import FetchResult
from scout.models import CompanyResult
from scout.profiles import load_profiles
from scout.store import load_companies
from scout.webapp import app, render_map, run_inspect_form, run_research_form


def fetched(url: str, html: str) -> FetchResult:
    return FetchResult(url=url, final_url=url, status=200, html=html)


class WebappFormTests(unittest.TestCase):
    def test_index_is_technology_only(self):
        client = TestClient(app)
        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        other_profiles = [p for p in load_profiles() if p.id != "technology"]
        self.assertTrue(other_profiles, "fixture expectation: profiles.json still defines non-technology profiles")
        for profile in other_profiles:
            self.assertNotIn(profile.label, response.text)

    @patch("scout.research.discover_sitemap_pages", return_value=[])
    def test_run_research_form_reports_eligible_company(self, _sitemap_discovery):
        pages = {
            "https://acme.test": fetched(
                "https://acme.test",
                "<p>Melbourne VIC Australia</p><a href='/careers'>Careers</a><p>software platform</p>",
            ),
            "https://acme.test/careers": fetched(
                "https://acme.test/careers",
                "<h1>Web Developer</h1><p>Maintain customer website features.</p>",
            ),
        }
        fake_fetch = lambda url: pages.get(url, FetchResult(url, error="not found"))
        fake_discover = lambda city, state, country, profile_id, coordinates=None: ["https://acme.test"]

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "report.md"

            html = run_research_form(
                city="Melbourne",
                state="VIC",
                country="Australia",
                profile_id="technology",
                roles_input="web developer",
                fetch=fake_fetch,
                discover=fake_discover,
                output=str(output_path),
                db_path=str(Path(directory) / "test.db"),
            )

            self.assertIn("eligible", html)
            self.assertIn("acme.test", html)
            self.assertIn('href="/map"', html)
            self.assertTrue(output_path.exists())

    def test_run_research_form_reports_no_domains_found(self):
        html = run_research_form(
            city="Nowhere",
            state="XX",
            country="Nowhereland",
            profile_id="technology",
            roles_input="",
            discover=lambda city, state, country, profile_id, coordinates=None: [],
        )

        self.assertIn("Prospect Scout research setup", html)
        self.assertIn("No businesses with a public website were found", html)

    def test_run_research_form_caps_discovered_domains_to_limit(self):
        discovered = [f"https://company{i}.test" for i in range(5)]
        fetched_urls = []

        def fake_fetch(url):
            fetched_urls.append(url)
            return FetchResult(url, error="not found")

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "report.md"

            run_research_form(
                city="Melbourne",
                state="VIC",
                country="Australia",
                profile_id="technology",
                roles_input="",
                fetch=fake_fetch,
                discover=lambda city, state, country, profile_id, coordinates=None: discovered,
                limit=2,
                output=str(output_path),
                db_path=str(Path(directory) / "test.db"),
            )

        # Companies are researched concurrently now (see WEB_CONCURRENCY), so
        # call order isn't guaranteed - only which domains got fetched is.
        self.assertCountEqual(fetched_urls, discovered[:2])

    def test_run_research_form_ranks_eligible_companies_above_needs_review(self):
        pages = {
            "https://weak.test": fetched("https://weak.test", "<p>Our mission is to help. Donate today.</p>"),
            "https://strong.test": fetched(
                "https://strong.test",
                "<p>Melbourne VIC Australia</p><a href='/careers'>Careers</a><p>software platform</p>",
            ),
            "https://strong.test/careers": fetched("https://strong.test/careers", "<h1>Web Developer</h1><p>Maintain customer website features.</p>"),
        }
        fake_fetch = lambda url: pages.get(url, FetchResult(url, error="not found"))
        # Discovery order deliberately puts the weaker candidate first, so a
        # pass only means ranking actually reordered them.
        fake_discover = lambda city, state, country, profile_id, coordinates=None: ["https://weak.test", "https://strong.test"]

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "report.md"

            html_out = run_research_form(
                city="Melbourne",
                state="VIC",
                country="Australia",
                profile_id="technology",
                roles_input="web developer",
                fetch=fake_fetch,
                discover=fake_discover,
                output=str(output_path),
                db_path=str(Path(directory) / "test.db"),
            )

        self.assertLess(html_out.index("strong.test"), html_out.index("weak.test"))
        self.assertIn("Why ranked here", html_out)

    @patch("scout.research.discover_sitemap_pages", return_value=[])
    def test_run_research_form_shows_finding_detail_inline(self, _sitemap_discovery):
        pages = {
            "https://acme.test": fetched(
                "https://acme.test",
                "<p>Melbourne VIC Australia</p><a href='/careers'>Careers</a><p>software platform</p>",
            ),
            "https://acme.test/careers": fetched(
                "https://acme.test/careers",
                "<h1>Web Developer</h1><p>Maintain customer website features.</p>",
            ),
        }
        fake_fetch = lambda url: pages.get(url, FetchResult(url, error="not found"))
        fake_discover = lambda city, state, country, profile_id, coordinates=None: ["https://acme.test"]

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "report.md"

            html_out = run_research_form(
                city="Melbourne",
                state="VIC",
                country="Australia",
                profile_id="technology",
                roles_input="web developer",
                fetch=fake_fetch,
                discover=fake_discover,
                output=str(output_path),
                db_path=str(Path(directory) / "test.db"),
            )

        self.assertIn("Maintain customer website features", html_out)
        self.assertIn("https://acme.test/careers", html_out)
        self.assertIn("Suggested next step", html_out)
        self.assertIn("Outreach angle:", html_out)
        self.assertIn("Maintain customer website features", html_out.split("Outreach angle:")[1][:400])

    def test_render_form_escapes_error_text(self):
        html_out = run_research_form(
            city="Melbourne",
            state="VIC",
            country="Australia",
            profile_id="<script>alert(1)</script>",
            roles_input="",
            discover=lambda city, state, country, profile_id, coordinates=None: ["https://acme.test"],
        )

        self.assertNotIn("<script>alert(1)</script>", html_out)
        self.assertIn("&lt;script&gt;", html_out)

    def test_run_research_form_rejects_unknown_profile(self):
        html = run_research_form(
            city="Melbourne",
            state="VIC",
            country="Australia",
            profile_id="not-a-real-profile",
            roles_input="",
            discover=lambda city, state, country, profile_id, coordinates=None: ["https://acme.test"],
        )

        self.assertIn("Prospect Scout research setup", html)
        self.assertIn("unknown profile", html)


class WebappInspectFormTests(unittest.TestCase):
    def test_inspect_index_offers_the_mode_switch(self):
        client = TestClient(app)
        response = client.get("/inspect")

        self.assertEqual(response.status_code, 200)
        self.assertIn("specific company", response.text.lower())
        self.assertIn('href="/"', response.text)

    @patch("scout.research.discover_sitemap_pages", return_value=[])
    def test_run_inspect_form_reports_eligible_company_with_no_location_check(self, _sitemap_discovery):
        pages = {
            "https://known.test": fetched(
                "https://known.test",
                "<p>software platform</p><a href='/careers'>Careers</a>",
            ),
            "https://known.test/careers": fetched(
                "https://known.test/careers",
                "<h1>Web Developer</h1><p>Maintain customer website features.</p>",
            ),
        }
        fake_fetch = lambda url: pages.get(url, FetchResult(url, error="not found"))

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "report.md"

            html = run_inspect_form(
                domain="https://known.test",
                roles_input="web developer",
                fetch=fake_fetch,
                output=str(output_path),
                db_path=str(Path(directory) / "test.db"),
            )

            self.assertIn("eligible", html)
            self.assertIn("known.test", html)
            self.assertIn("skipped (specific-company mode)", html)
            self.assertIn('href="/map"', html)
            self.assertTrue(output_path.exists())

    def test_run_inspect_form_rejects_a_url_missing_the_scheme(self):
        html = run_inspect_form(domain="known.test", roles_input="")

        self.assertIn("Enter a full company URL", html)

    @patch("scout.research.discover_sitemap_pages", return_value=[])
    def test_run_inspect_form_persists_a_coordinate_from_structured_data(self, _sitemap_discovery):
        # /inspect mode never gets an OSM coordinate (it skips discovery
        # entirely) - structured data on the company's own page is the only
        # coordinate source available here. See scout/research.py:
        # extract_structured_coordinates / analyse_company.
        html = (
            '<p>software platform</p>'
            '<script type="application/ld+json">'
            '{"geo": {"latitude": "-37.95", "longitude": "145.06"}}'
            '</script>'
        )
        fake_fetch = lambda url: fetched(url, html) if url == "https://known.test" else FetchResult(url, error="not found")

        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "test.db")

            run_inspect_form(
                domain="https://known.test",
                roles_input="web developer",
                fetch=fake_fetch,
                output=str(Path(directory) / "report.md"),
                db_path=db_path,
            )

            persisted = {company.domain: company for company in load_companies(db_path=db_path)}

        self.assertEqual(persisted["https://known.test"].lat, -37.95)
        self.assertEqual(persisted["https://known.test"].lon, 145.06)


class MapViewTests(unittest.TestCase):
    def test_render_map_shows_pins_and_lists_unplotted(self):
        mapped = CompanyResult(domain="https://acme.test", name="Acme", status="eligible", lat=-37.8136, lon=144.9631)
        unmapped = CompanyResult(domain="https://known.test", name="known.test", status="needs_review")

        html_out = render_map([mapped, unmapped])

        self.assertIn("-37.8136", html_out)
        self.assertIn("144.9631", html_out)
        self.assertIn("acme.test", html_out)
        self.assertIn("No coordinate available (1)", html_out)
        self.assertIn("known.test", html_out)

    def test_render_map_handles_no_companies_at_all(self):
        self.assertIn("No companies persisted yet", render_map([]))

    def test_render_map_handles_companies_with_no_coordinates_at_all(self):
        html_out = render_map([CompanyResult(domain="https://known.test", name="known.test")])

        self.assertIn("No persisted company has a coordinate yet", html_out)
        self.assertIn("known.test", html_out)

    def test_map_route_is_reachable(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "test.db")
            with patch("scout.webapp.load_companies", functools.partial(load_companies, db_path=db_path)):
                response = TestClient(app).get("/map")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Map", response.text)

    @patch("scout.research.discover_sitemap_pages", return_value=[])
    def test_e2e_research_then_map_shows_the_researched_company(self, _sitemap_discovery):
        pages = {
            "https://acme.test": fetched(
                "https://acme.test",
                "<p>Melbourne VIC Australia</p><a href='/careers'>Careers</a><p>software platform</p>",
            ),
            "https://acme.test/careers": fetched(
                "https://acme.test/careers",
                "<h1>Web Developer</h1><p>Maintain customer website features.</p>",
            ),
        }
        fake_fetch = lambda url: pages.get(url, FetchResult(url, error="not found"))

        def fake_discover(city, state, country, profile_id, coordinates=None):
            if coordinates is not None:
                coordinates["https://acme.test"] = (-37.8136, 144.9631)
            return ["https://acme.test"]

        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "test.db")

            run_research_form(
                city="Melbourne", state="VIC", country="Australia",
                profile_id="technology", roles_input="web developer",
                fetch=fake_fetch, discover=fake_discover,
                output=str(Path(directory) / "report.md"), db_path=db_path,
            )

            with patch("scout.webapp.load_companies", functools.partial(load_companies, db_path=db_path)):
                response = TestClient(app).get("/map")

        self.assertEqual(response.status_code, 200)
        self.assertIn("acme.test", response.text)
        self.assertIn("-37.8136", response.text)

    @patch("scout.research.discover_sitemap_pages", return_value=[])
    def test_e2e_rerun_still_shows_one_pin_not_two(self, _sitemap_discovery):
        fake_fetch = lambda url: fetched(url, "<p>Melbourne VIC Australia</p>") if url == "https://acme.test" else FetchResult(url, error="not found")

        def fake_discover(city, state, country, profile_id, coordinates=None):
            if coordinates is not None:
                coordinates["https://acme.test"] = (-37.8136, 144.9631)
            return ["https://acme.test"]

        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "test.db")

            for _ in range(2):
                run_research_form(
                    city="Melbourne", state="VIC", country="Australia",
                    profile_id="technology", roles_input="web developer",
                    fetch=fake_fetch, discover=fake_discover,
                    output=str(Path(directory) / "report.md"), db_path=db_path,
                )

            with patch("scout.webapp.load_companies", functools.partial(load_companies, db_path=db_path)):
                response = TestClient(app).get("/map")

        self.assertEqual(response.text.count('"https://acme.test"'), 1, "rerun must not duplicate the pin")


if __name__ == "__main__":
    unittest.main()
