import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scout.fetcher import FetchResult
from scout.models import ResearchRequest
from scout.reporting import write_report
from scout.research import _is_job_page, analyse_company, evidence_urls, location_is_verified, matching_roles, normalise_domain, page_text, read_domains, title_artifact_finding
from scout.profiles import custom_profile, load_profiles


def fetched(url: str, html: str) -> FetchResult:
    return FetchResult(url=url, final_url=url, status=200, html=html)


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.request = ResearchRequest("Melbourne", "VIC", "Australia", ("web developer", "technical support"))
        # evidence_urls() tries live sitemap discovery first (Unit C); keep these
        # tests offline and exercising the pre-existing link-scan fallback path.
        patcher = patch("scout.research.discover_sitemap_pages", return_value=[])
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_career_role_with_verified_location_is_eligible(self):
        pages = {
            "https://acme.test": fetched("https://acme.test", "<p>Melbourne VIC Australia</p><a href='/careers'>Careers</a><p>software platform</p>"),
            "https://acme.test/careers": fetched("https://acme.test/careers", "<h1>Web Developer</h1><p>Maintain customer website features.</p>"),
        }

        result = analyse_company("https://acme.test", self.request, fetch=lambda url: pages.get(url, FetchResult(url, error="not found")))

        self.assertEqual(result.status, "eligible")
        self.assertTrue(result.location_verified)
        self.assertEqual(result.findings[0].kind, "advertised_role_signal")
        self.assertIn("Web Developer", result.findings[0].evidence)

    def test_sector_idea_stays_low_confidence_and_needs_review(self):
        result = analyse_company(
            "https://charity.test",
            self.request,
            fetch=lambda url: fetched(url, "<p>Our mission is to help. Donate today.</p>") if url == "https://charity.test" else FetchResult(url, error="not found"),
        )

        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.sector, "non-profit")
        self.assertEqual(result.findings[0].kind, "potential_role_related_need")
        self.assertEqual(result.findings[0].confidence, "low")

    def test_technology_case_study_becomes_a_medium_confidence_signal(self):
        pages = {
            "https://tech.test": fetched("https://tech.test", "<p>Melbourne VIC Australia software platform</p><a href='/case-studies'>Customer stories</a>"),
            "https://tech.test/case-studies": fetched("https://tech.test/case-studies", "<p>Our full-stack team built a customer portal.</p>"),
        }

        result = analyse_company("https://tech.test", self.request, fetch=lambda url: pages.get(url, FetchResult(url, error="not found")))

        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.findings[0].kind, "case_study_role_signal")
        self.assertEqual(result.findings[0].confidence, "medium")

    def test_custom_profile_controls_role_and_page_scanning(self):
        profile = custom_profile(
            "Fundraising", ("grants coordinator",), ("vacancies",),
            "Ask about grant-application and donor workflow support; do not assume an open role.",
        )
        pages = {
            "https://fund.test": fetched("https://fund.test", "<p>Melbourne VIC Australia</p><a href='/vacancies'>Vacancies</a>"),
            "https://fund.test/vacancies": fetched("https://fund.test/vacancies", "<h1>Grants Coordinator</h1>"),
        }
        result = analyse_company("https://fund.test", self.request, fetch=lambda url: pages.get(url, FetchResult(url, error="not found")), profile=profile)

        self.assertEqual(result.status, "eligible")
        self.assertIn("Grants Coordinator", result.findings[0].evidence)
        self.assertTrue(any(profile.id == "technology" for profile in load_profiles()))

    def test_domain_file_deduplicates_and_ignores_comments(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "domains.txt"
            source.write_text("# candidates\nacme.test\nhttps://acme.test/path\nother.test\n", encoding="utf-8")
            self.assertEqual(read_domains(str(source)), ["https://acme.test", "https://other.test"])
        self.assertIsNone(normalise_domain("not a host"))

    def test_report_writes_matching_json_and_markdown(self):
        from scout.research import run_research

        with tempfile.TemporaryDirectory() as directory:
            report = run_research(self.request, ["https://empty.test"], fetch=lambda url: FetchResult(url, error="offline"))
            markdown, json_file = write_report(report, str(Path(directory) / "report.md"))
            payload = json.loads(json_file.read_text(encoding="utf-8"))
            self.assertTrue(markdown.exists())
            self.assertEqual(payload["companies"][0]["domain"], "https://empty.test")
            self.assertIn("Homepage not fetched", markdown.read_text(encoding="utf-8"))

    def test_run_research_with_concurrency_preserves_domain_order_and_results(self):
        from scout.research import run_research

        domains = [f"https://company{i}.test" for i in range(6)]

        def fake_fetch(url):
            return fetched(url, "<p>Melbourne VIC Australia</p>") if url in domains else FetchResult(url, error="not found")

        report = run_research(self.request, domains, fetch=fake_fetch, max_workers=4)

        self.assertEqual([company.domain for company in report.companies], domains)
        self.assertTrue(all(company.location_verified for company in report.companies))

    def test_technology_profile_covers_projects_and_portfolio_vocabulary(self):
        tech = next(profile for profile in load_profiles() if profile.id == "technology")
        self.assertIn("projects", tech.page_terms)
        self.assertIn("portfolio", tech.page_terms)

    def test_university_course_catalog_page_is_not_an_advertised_role_signal(self):
        # Regression for the confirmed real MIT false positive. The typed
        # interest ("software engineering") is a literal degree name here, so
        # a match still happens - keyword matching can't tell "the company's
        # own need" from "a subject taught here" (see docs/KNOWN_GAPS.md).
        # What the fix guarantees: it's labeled as weaker case-study evidence
        # ("do not assume an open role"), never as an advertised vacancy.
        request = ResearchRequest("Melbourne", "VIC", "Australia", ("software engineering",))
        pages = {
            "https://mit.test": fetched(
                "https://mit.test",
                "<p>Melbourne VIC Australia</p><a href='/students/career-development'>Engineering pathways</a>",
            ),
            "https://mit.test/students/career-development": fetched(
                "https://mit.test/students/career-development",
                "<p>Bachelor of Networking, major in Software Engineering.</p>",
            ),
        }
        result = analyse_company("https://mit.test", request, fetch=lambda url: pages.get(url, FetchResult(url, error="not found")))

        self.assertEqual(result.findings[0].kind, "case_study_role_signal")


class MatchingRolesTests(unittest.TestCase):
    def test_role_term_does_not_match_a_longer_word_sharing_its_prefix(self):
        # Confirmed real: MIT's "students/career-development" page listing
        # "Bachelor of Software Engineering" was matching the role term
        # "software engineer" via raw substring - a degree name, not a role.
        self.assertEqual(matching_roles("Bachelor of Software Engineering", ("software engineer",)), [])

    def test_role_term_still_matches_its_own_plural(self):
        self.assertEqual(matching_roles("we are hiring Software Engineers", ("software engineer",)), ["software engineer"])

    def test_exact_singular_match_still_works(self):
        self.assertEqual(matching_roles("a software engineer role", ("software engineer",)), ["software engineer"])


class JobPageClassificationTests(unittest.TestCase):
    def test_careers_url_is_a_job_page(self):
        self.assertTrue(_is_job_page("https://acme.test/careers"))

    def test_career_development_url_is_not_a_job_page(self):
        # Confirmed real false positive: a university's general student
        # career-services page, not a vacancy listing.
        self.assertFalse(_is_job_page("https://www.mit.edu.au/students/career-development"))

    def test_blog_url_is_not_a_job_page(self):
        self.assertFalse(_is_job_page("https://commgen.com.au/2024/01/20/is-logistics-a-good-career-path/"))


class LocationIsVerifiedTests(unittest.TestCase):
    def setUp(self):
        self.request = ResearchRequest("Melbourne", "VIC", "Australia", ("web developer",))

    def test_exact_literal_match_still_verifies(self):
        self.assertTrue(location_is_verified("Melbourne VIC Australia", self.request, "https://acme.com.au"))

    def test_full_state_name_satisfies_a_typed_abbreviation(self):
        self.assertTrue(location_is_verified("Melbourne Victoria Australia", self.request, "https://acme.com.au"))

    def test_state_abbreviation_satisfies_a_typed_full_name(self):
        request = ResearchRequest("Melbourne", "Victoria", "Australia", ("web developer",))
        self.assertTrue(location_is_verified("Melbourne VIC Australia", request, "https://acme.com.au"))

    def test_com_au_domain_satisfies_country_when_word_is_absent(self):
        self.assertTrue(location_is_verified("Melbourne VIC, proudly local", self.request, "https://acme.com.au"))

    def test_non_au_domain_still_requires_the_literal_country_word(self):
        self.assertFalse(location_is_verified("Melbourne VIC, proudly local", self.request, "https://acme.com"))

    def test_city_is_still_a_hard_requirement(self):
        self.assertFalse(location_is_verified("Sydney NSW Australia", self.request, "https://acme.com.au"))

    def test_unlisted_country_falls_back_to_requiring_the_literal_word(self):
        request = ResearchRequest("Auckland", "Auckland", "New Zealand", ("web developer",))
        self.assertFalse(location_is_verified("Auckland Auckland, proudly local", request, "https://acme.co.nz"))


class PageTextChromeStrippingTests(unittest.TestCase):
    def test_strips_nav_header_footer_but_keeps_real_content(self):
        html = (
            "<nav>Home About Careers</nav>"
            "<header>Acme Corp site header</header>"
            "<p>We build custom software platforms for clients.</p>"
            "<footer>Copyright 2026 Acme</footer>"
        )
        text = page_text(html)
        self.assertIn("custom software platforms", text)
        self.assertNotIn("Home About Careers", text)
        self.assertNotIn("site header", text)
        self.assertNotIn("Copyright 2026", text)

    def test_strips_script_and_style_text_content(self):
        html = "<script>console.log('leak-me')</script><style>.leak{color:red}</style><p>Visible</p>"
        text = page_text(html)
        self.assertIn("Visible", text)
        self.assertNotIn("leak-me", text)
        self.assertNotIn("leak{color", text)

    def test_strips_noscript(self):
        html = "<noscript>Enable JavaScript to view this leak-me content</noscript><p>Visible</p>"
        text = page_text(html)
        self.assertIn("Visible", text)
        self.assertNotIn("leak-me", text)


class TitleArtifactFindingTests(unittest.TestCase):
    def test_copy_artifact_matches(self):
        finding = title_artifact_finding("Corporate2Contract (Copy)", "https://corporate2contract.test")
        self.assertIsNotNone(finding)
        self.assertEqual(finding.kind, "seo_metadata_gap")
        self.assertEqual(finding.confidence, "high")
        self.assertIn("Corporate2Contract (Copy)", finding.evidence)

    def test_draft_artifact_matches(self):
        self.assertIsNotNone(title_artifact_finding("Homepage (Draft)", "https://acme.test"))

    def test_untitled_prefix_matches(self):
        self.assertIsNotNone(title_artifact_finding("Untitled Document", "https://acme.test"))

    def test_trailing_copy_suffix_matches(self):
        self.assertIsNotNone(title_artifact_finding("About Us - Copy", "https://acme.test"))

    def test_ordinary_title_does_not_match(self):
        self.assertIsNone(title_artifact_finding("Acme Corp | Home", "https://acme.test"))

    def test_copy_in_a_real_business_name_does_not_false_positive(self):
        self.assertIsNone(title_artifact_finding("Copy Craft Studio", "https://acme.test"))

    def test_empty_title_returns_none(self):
        self.assertIsNone(title_artifact_finding("", "https://acme.test"))
        self.assertIsNone(title_artifact_finding("   ", "https://acme.test"))


class TitleArtifactIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.request = ResearchRequest("Melbourne", "VIC", "Australia", ("web developer", "technical support"))
        patcher = patch("scout.research.discover_sitemap_pages", return_value=[])
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_real_role_finding_still_wins_findings_first_slot(self):
        pages = {
            "https://acme.test": fetched(
                "https://acme.test",
                "<title>Acme Corp (Copy)</title><p>Melbourne VIC Australia</p><a href='/careers'>Careers</a>",
            ),
            "https://acme.test/careers": fetched("https://acme.test/careers", "<h1>Web Developer</h1><p>Maintain customer website features.</p>"),
        }

        result = analyse_company("https://acme.test", self.request, fetch=lambda url: pages.get(url, FetchResult(url, error="not found")))

        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.findings[0].kind, "advertised_role_signal")
        self.assertTrue(any(finding.kind == "seo_metadata_gap" for finding in result.findings))

    def test_title_artifact_appears_alongside_hidden_need_finding(self):
        result = analyse_company(
            "https://charity.test",
            self.request,
            fetch=lambda url: fetched(url, "<title>Charity Co (Copy)</title><p>Our mission is to help. Donate today.</p>") if url == "https://charity.test" else FetchResult(url, error="not found"),
        )

        kinds = [finding.kind for finding in result.findings]
        self.assertIn("potential_role_related_need", kinds)
        self.assertIn("seo_metadata_gap", kinds)


class EvidenceUrlsSitemapIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.profile = next(profile for profile in load_profiles() if profile.id == "technology")
        # A homepage whose only real <a href> link points somewhere the sitemap
        # result deliberately does NOT include, so passing means the sitemap
        # path actually won, not that both paths happened to agree.
        self.html = "<a href='/only-on-page-link'>Case studies</a>"

    @patch("scout.research.discover_sitemap_pages")
    def test_sitemap_results_win_over_link_scan_when_present(self, discover):
        discover.return_value = ["https://acme.test/case-studies/alpha", "https://acme.test/case-studies/beta"]

        result = evidence_urls("https://acme.test", self.html, self.profile)

        self.assertEqual(result, ["https://acme.test/case-studies/alpha", "https://acme.test/case-studies/beta"])
        self.assertNotIn("https://acme.test/only-on-page-link", result)

    @patch("scout.research.discover_sitemap_pages", return_value=[])
    def test_falls_back_to_link_scan_when_sitemap_empty(self, discover):
        result = evidence_urls("https://acme.test", self.html, self.profile)

        self.assertIn("https://acme.test/only-on-page-link", result)
