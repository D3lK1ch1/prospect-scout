import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from scout.fetcher import FetchResult
from scout.profiles import load_profiles
from scout.webapp import app, run_inspect_form, run_research_form


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
        fake_discover = lambda city, state, country, profile_id: ["https://acme.test"]

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
            )

            self.assertIn("eligible", html)
            self.assertIn("acme.test", html)
            self.assertTrue(output_path.exists())

    def test_run_research_form_reports_no_domains_found(self):
        html = run_research_form(
            city="Nowhere",
            state="XX",
            country="Nowhereland",
            profile_id="technology",
            roles_input="",
            discover=lambda city, state, country, profile_id: [],
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
                discover=lambda city, state, country, profile_id: discovered,
                limit=2,
                output=str(output_path),
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
        fake_discover = lambda city, state, country, profile_id: ["https://weak.test", "https://strong.test"]

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
        fake_discover = lambda city, state, country, profile_id: ["https://acme.test"]

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
            discover=lambda city, state, country, profile_id: ["https://acme.test"],
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
            discover=lambda city, state, country, profile_id: ["https://acme.test"],
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
            )

            self.assertIn("eligible", html)
            self.assertIn("known.test", html)
            self.assertIn("skipped (specific-company mode)", html)
            self.assertTrue(output_path.exists())

    def test_run_inspect_form_rejects_a_url_missing_the_scheme(self):
        html = run_inspect_form(domain="known.test", roles_input="")

        self.assertIn("Enter a full company URL", html)


if __name__ == "__main__":
    unittest.main()
