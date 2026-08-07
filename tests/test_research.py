import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scout.fetcher import FetchResult
from scout.models import CompanyResult, Finding, ResearchRequest
from scout.reporting import write_report
from scout.research import _is_job_page, _looks_like_msp, _nearest_name, analyse_company, contact_finding, evidence_urls, hidden_need_finding, infer_sector, location_is_verified, matching_roles, normalise_domain, page_text, read_domains, team_page_urls, title_artifact_finding
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


class TeamPageUrlsTests(unittest.TestCase):
    @patch("scout.research.discover_sitemap_pages")
    def test_sitemap_results_win_when_present(self, discover):
        discover.return_value = ["https://acme.test/company/leadership"]

        result = team_page_urls("https://acme.test", "<a href='/only-on-page'>Meet the Team</a>")

        self.assertEqual(result, ["https://acme.test/company/leadership"])

    @patch("scout.research.discover_sitemap_pages", return_value=[])
    def test_falls_back_to_link_scan_then_guessed_paths(self, _discover):
        result = team_page_urls("https://acme.test", "<a href='/our-people'>About the team</a>")

        self.assertIn("https://acme.test/our-people", result)

    @patch("scout.research.discover_sitemap_pages", return_value=[])
    def test_no_team_link_falls_back_to_guessed_paths_not_a_crash(self, _discover):
        result = team_page_urls("https://acme.test", "<a href='/products'>Products</a>")

        self.assertTrue(result)
        self.assertTrue(all(url.startswith("https://acme.test") for url in result))


class NearestNameTests(unittest.TestCase):
    def test_finds_name_immediately_before_title(self):
        excerpt = "Meet Jane Doe, our Chief Technology Officer, who leads engineering."

        self.assertEqual(_nearest_name(excerpt, "Chief Technology Officer"), "Jane Doe")

    def test_no_name_shaped_text_returns_none(self):
        excerpt = "our chief technology officer role is currently vacant"

        self.assertIsNone(_nearest_name(excerpt, "chief technology officer"))

    def test_title_term_itself_is_not_returned_as_the_name(self):
        excerpt = "Chief Technology Officer - leadership team"

        self.assertIsNone(_nearest_name(excerpt, "Chief Technology Officer"))

    def test_filler_words_immediately_before_the_title_are_not_mistaken_for_a_name(self):
        # Real bug caught by hand: "Our Head" (article + the title's own first
        # word "Head") is closer in raw character distance to the title span
        # than the real name "Priya Nair" is, so a naive nearest-by-distance
        # pick grabbed "Our Head" instead. The fix requires proper interval
        # overlap, not just "does the candidate start inside the title span".
        excerpt = "Our Head of Engineering, Priya Nair, oversees delivery across the team."

        self.assertEqual(_nearest_name(excerpt, "Head of Engineering"), "Priya Nair")


class ContactFindingTests(unittest.TestCase):
    def test_title_with_nearby_name_is_medium_confidence(self):
        text = "Meet Jane Doe, our CTO, who leads the engineering team."

        finding = contact_finding(text, "https://acme.test/team", ("CTO", "Chief Technology Officer"))

        self.assertIsNotNone(finding)
        self.assertEqual(finding.kind, "team_contact_signal")
        self.assertEqual(finding.confidence, "medium")
        self.assertIn("Jane Doe", finding.suggestion)

    def test_title_without_a_nearby_name_is_low_confidence_not_a_crash(self):
        text = "We're hiring a CTO to join our leadership team next year."

        finding = contact_finding(text, "https://acme.test/team", ("CTO",))

        self.assertIsNotNone(finding)
        self.assertEqual(finding.confidence, "low")

    def test_no_title_present_returns_none(self):
        text = "Our team builds great products together."

        self.assertIsNone(contact_finding(text, "https://acme.test/team", ("CTO", "CIO")))

    def test_director_does_not_false_positive_as_cto(self):
        # "Director" and "doctor" both literally contain the substring "cto" -
        # a naive `in` check would wrongly fire here; word-boundary matching
        # must not.
        text = "Our Sales Director and Managing Director lead the commercial team."

        self.assertIsNone(contact_finding(text, "https://acme.test/team", ("CTO",)))


class ContactFindingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.request = ResearchRequest("Melbourne", "VIC", "Australia", ("web developer",))
        patcher = patch("scout.research.discover_sitemap_pages", return_value=[])
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_analyse_company_adds_a_contact_finding_from_the_team_page(self):
        profile = next(profile for profile in load_profiles() if profile.id == "technology")
        pages = {
            "https://acme.test": fetched(
                "https://acme.test",
                "<p>Melbourne VIC Australia</p><a href='/careers'>Careers</a><a href='/about'>About us</a><p>software platform</p>",
            ),
            "https://acme.test/careers": fetched("https://acme.test/careers", "<h1>Web Developer</h1><p>Maintain customer website features.</p>"),
            "https://acme.test/about": fetched("https://acme.test/about", "<p>Meet Jane Doe, our CTO, who leads engineering.</p>"),
        }

        result = analyse_company("https://acme.test", self.request, fetch=lambda url: pages.get(url, FetchResult(url, error="not found")), profile=profile)

        contact_findings = [f for f in result.findings if f.kind == "team_contact_signal"]
        self.assertEqual(len(contact_findings), 1)
        self.assertIn("Jane Doe", contact_findings[0].suggestion)

    def test_profile_with_no_contact_titles_never_adds_a_contact_finding(self):
        profile = custom_profile("Custom", ("web developer",), ("careers", "about"), "Ask about the workload.")
        pages = {
            "https://acme.test": fetched(
                "https://acme.test",
                "<p>Melbourne VIC Australia</p><a href='/about'>About us</a><p>software platform</p>",
            ),
            "https://acme.test/about": fetched("https://acme.test/about", "<p>Meet Jane Doe, our CTO, who leads engineering.</p>"),
        }

        result = analyse_company("https://acme.test", self.request, fetch=lambda url: pages.get(url, FetchResult(url, error="not found")), profile=profile)

        self.assertFalse(any(f.kind == "team_contact_signal" for f in result.findings))


class InferSectorMinimumMatchesTests(unittest.TestCase):
    """Regression for two confirmed real false positives: a public library
    homepage scored "fashion/retail" on the single word "collection" (a
    library collection, not a clothing one); an energy company scored
    "fashion/retail" on the single word "shop". A lone incidental hit should
    no longer be asserted as a sector fact.
    """

    def test_a_single_keyword_hit_is_not_enough_to_assert_a_sector(self):
        self.assertEqual(infer_sector("Browse our library collection online."), "unknown")

    def test_two_keyword_hits_still_assert_a_sector(self):
        self.assertEqual(infer_sector("Shop our latest fashion collection."), "fashion/retail")

    def test_no_keyword_hits_is_unknown(self):
        self.assertEqual(infer_sector("A page with no sector-indicating words at all."), "unknown")


class HiddenNeedFindingContactAwarenessTests(unittest.TestCase):
    """hidden_need_finding()'s suggestion now differs by whether a technical
    contact was already found for the same company, instead of firing blind
    - the "shouldn't it check for a CTO first?" gap.
    """

    def setUp(self):
        self.profile = next(profile for profile in load_profiles() if profile.id == "technology")
        self.result = CompanyResult(domain="https://acme.test", name="acme.test", sector="technology")

    def test_no_contact_found_states_that_plainly(self):
        finding = hidden_need_finding(self.result, "https://acme.test", "some real page text", self.profile, contact=None)

        self.assertIn("No evidence of a dedicated technical or leadership role", finding.suggestion)

    def test_contact_found_points_the_suggestion_at_them(self):
        contact = Finding(
            kind="team_contact_signal", evidence="...", source_url="https://acme.test/about",
            confidence="medium", suggestion="Possible contact: Jane Doe (CTO) - verify before reaching out.",
        )

        finding = hidden_need_finding(self.result, "https://acme.test", "some real page text", self.profile, contact=contact)

        self.assertIn("team contact finding below", finding.suggestion)
        self.assertNotIn("No evidence of a dedicated technical", finding.suggestion)


class MspLanguageSignalTests(unittest.TestCase):
    """Regression for RESEARCH.md 2026-08-06: confirmed-only MSP phrases
    (literally quoted from itnetworks.com.au), not extrapolated synonyms.
    HotDoc is the confirmed real counter-example that must NOT be flagged -
    a real target company with neither MSP nor engineering language at all.
    """

    def test_confirmed_msp_phrase_alone_is_flagged(self):
        self.assertTrue(_looks_like_msp("Our service starts with the strategic insight of a Virtual CIO."))

    def test_a_page_with_neither_signal_is_not_flagged(self):
        # HotDoc's real careers page shape: culture/values content, no MSP
        # language, no engineering language either.
        self.assertFalse(_looks_like_msp("Always be empathetic. We offer a benefits pyramid and great office photos."))

    def test_engineering_language_overrides_an_incidental_msp_mention(self):
        text = "We used to rely on managed IT support, until we built our own engineering team."
        self.assertFalse(_looks_like_msp(text))

    def test_hidden_need_finding_flags_confirmed_msp_language(self):
        profile = next(profile for profile in load_profiles() if profile.id == "technology")
        result = CompanyResult(domain="https://itnetworks.test", name="itnetworks.test", sector="unknown")

        finding = hidden_need_finding(
            result, "https://itnetworks.test",
            "Our service starts with the strategic insight of a Virtual CIO.",
            profile, contact=None,
        )

        self.assertIn("IT-services/MSP provider", finding.suggestion)

    def test_hidden_need_finding_does_not_flag_a_real_company_with_neither_signal(self):
        profile = next(profile for profile in load_profiles() if profile.id == "technology")
        result = CompanyResult(domain="https://hotdoc.test", name="hotdoc.test", sector="health")

        finding = hidden_need_finding(
            result, "https://hotdoc.test",
            "Always be empathetic. We offer a benefits pyramid and great office photos.",
            profile, contact=None,
        )

        self.assertNotIn("IT-services/MSP provider", finding.suggestion)
        self.assertIn("No evidence of a dedicated technical or leadership role", finding.suggestion)


class HiddenNeedFindingIntegrationTests(unittest.TestCase):
    """analyse_company() now discovers the team contact before deciding how
    to word hidden_need_finding()'s suggestion, instead of only appending a
    contact finding afterward with no cross-reference.
    """

    def setUp(self):
        self.request = ResearchRequest("Melbourne", "VIC", "Australia", ("web developer",))
        patcher = patch("scout.research.discover_sitemap_pages", return_value=[])
        self.addCleanup(patcher.stop)
        patcher.start()
        self.profile = next(profile for profile in load_profiles() if profile.id == "technology")

    def test_hidden_need_suggestion_references_a_contact_found_on_the_same_site(self):
        pages = {
            "https://acme.test": fetched(
                "https://acme.test",
                "<p>Melbourne VIC Australia</p><a href='/about'>About us</a>",
            ),
            "https://acme.test/about": fetched("https://acme.test/about", "<p>Meet Jane Doe, our CTO, who leads engineering.</p>"),
        }

        result = analyse_company("https://acme.test", self.request, fetch=lambda url: pages.get(url, FetchResult(url, error="not found")), profile=self.profile)

        need = next(f for f in result.findings if f.kind == "potential_role_related_need")
        self.assertIn("team contact finding below", need.suggestion)
        self.assertTrue(any(f.kind == "team_contact_signal" for f in result.findings))

    def test_hidden_need_suggestion_says_so_when_no_contact_exists(self):
        pages = {
            "https://acme.test": fetched("https://acme.test", "<p>Melbourne VIC Australia</p>"),
        }

        result = analyse_company("https://acme.test", self.request, fetch=lambda url: pages.get(url, FetchResult(url, error="not found")), profile=self.profile)

        need = next(f for f in result.findings if f.kind == "potential_role_related_need")
        self.assertIn("No evidence of a dedicated technical or leadership role", need.suggestion)
