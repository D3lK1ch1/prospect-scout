import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scout.fetcher import FetchResult
from scout.models import CompanyResult, Finding, ResearchRequest
from scout.reporting import write_report
from scout.research import _is_job_page, _looks_like_msp, _nearest_name, analyse_company, contact_finding, detect_platform_signal, evidence_urls, hidden_need_finding, infer_sector, location_is_verified, matching_roles, normalise_domain, page_text, read_domains, team_page_urls, text_excerpt, title_artifact_finding
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

    def test_specific_company_mode_skips_location_check_entirely(self):
        # location_required=False (scout inspect / webapp /inspect): a company
        # the human already picked and knows the location of shouldn't need
        # "Melbourne VIC Australia" on the page to become eligible.
        request = ResearchRequest(city="", state="", country="", roles=("web developer",), location_required=False)
        pages = {
            "https://known.test": fetched("https://known.test", "<p>software platform</p><a href='/careers'>Careers</a>"),
            "https://known.test/careers": fetched("https://known.test/careers", "<h1>Web Developer</h1><p>Maintain customer website features.</p>"),
        }

        result = analyse_company("https://known.test", request, fetch=lambda url: pages.get(url, FetchResult(url, error="not found")))

        self.assertEqual(result.status, "eligible")
        self.assertFalse(result.location_verified)
        self.assertFalse(result.location_checked)
        self.assertEqual(result.limitations, [])  # never claims an unattempted check "failed"

    def test_blank_location_is_allowed_only_when_not_required(self):
        with self.assertRaises(ValueError):
            ResearchRequest(city="", state="", country="", roles=("web developer",))  # location_required defaults True

        # Doesn't raise:
        ResearchRequest(city="", state="", country="", roles=("web developer",), location_required=False)

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

    def test_strips_wordpress_skip_link_outside_any_chrome_tag(self):
        # Regression for a confirmed real leak: dnx.solutions' skip-link sits
        # as a direct child of <body>, not inside nav/header/footer, so the
        # existing tag-based stripping above never touched it - "Skip to
        # content" leaked straight into extracted evidence text.
        html = '<body><a class="skip-link screen-reader-text" href="#content">Skip to content</a><p>Real page content.</p></body>'
        text = page_text(html)
        self.assertIn("Real page content", text)
        self.assertNotIn("Skip to content", text)


class TextExcerptTests(unittest.TestCase):
    """Regression for two confirmed real bugs found live against dnx.solutions
    and enspyr.co (see RESEARCH.md): a naive substring search could land on a
    coincidental match inside an unrelated word, and even a correct match
    location got cut mid-word by the fixed-width character slice.
    """

    def test_finds_the_real_whole_word_occurrence_not_a_coincidental_substring(self):
        # "cto" is a literal substring of "Director" - a naive .find("cto")
        # locks onto that instead of the real "CTO" mention later in the text,
        # far enough away that it falls outside the excerpt window once the
        # search starts from the correct, word-boundary location.
        text = (
            "Nicholas Meinhold Director and Tech Lead. "
            + ("padding words here to push it further away. " * 3)
            + "Concurrently CTO or co-founder of several startups across many countries doing many things."
        )
        excerpt = text_excerpt(text, "CTO")
        self.assertIn("Concurrently CTO or co-founder", excerpt)
        self.assertNotIn("Director", excerpt)

    def test_excerpt_boundaries_snap_to_whole_words_not_mid_word(self):
        # A phrase far enough into the text that a fixed 60-char lookback
        # would land mid-word inside "Workstar" - the real bug shape.
        text = "x" * 55 + " Workstar: Modernising a Windows-based application by applying DevOps on AWS today."
        excerpt = text_excerpt(text, "DevOps")
        self.assertTrue(excerpt.startswith("Workstar") or excerpt.startswith("Modernising"))
        self.assertNotIn("rkstar", excerpt)

    def test_falls_back_to_a_naive_search_when_no_whole_word_match_exists(self):
        # No literal callers pass a genuinely partial phrase today, but the
        # fallback should still degrade to something rather than nothing.
        text = "prefixCTOsuffix and other text here padding it out further."
        excerpt = text_excerpt(text, "CTO")
        self.assertIn("prefixCTOsuffix", excerpt)


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


class DetectPlatformSignalTests(unittest.TestCase):
    """Confirmed live this session against thesocialstudio.org (Shopify) and
    corporate2contract.com (Squarespace) - both markers are literal
    substrings actually seen on those real, fetched pages.
    """

    def test_shopify_marker_is_detected(self):
        html = '<link rel="preconnect" href="https://cdn.shopify.com" crossorigin>'

        finding = detect_platform_signal("https://acme.test", html)

        self.assertIsNotNone(finding)
        self.assertEqual(finding.kind, "platform_detected")
        self.assertIn("Shopify", finding.suggestion)
        self.assertEqual(finding.confidence, "high")

    def test_squarespace_marker_is_detected(self):
        html = "<!-- This is Squarespace. --><base href=''>"

        finding = detect_platform_signal("https://acme.test", html)

        self.assertIsNotNone(finding)
        self.assertIn("Squarespace", finding.suggestion)

    def test_ordinary_site_is_not_flagged(self):
        html = "<p>We build custom software for clients.</p>"

        self.assertIsNone(detect_platform_signal("https://acme.test", html))


class BrokenLinkSignalTests(unittest.TestCase):
    def setUp(self):
        self.request = ResearchRequest("Melbourne", "VIC", "Australia", ("web developer",))
        self.profile = next(profile for profile in load_profiles() if profile.id == "technology")

    @patch("scout.research.discover_sitemap_pages")
    def test_a_sitemap_sourced_broken_link_is_surfaced(self, discover):
        # A URL the company's own sitemap declared, that 404s - real evidence.
        discover.return_value = ["https://acme.test/case-studies/old-page"]
        pages = {
            "https://acme.test": fetched("https://acme.test", "<p>Melbourne VIC Australia</p>"),
        }

        result = analyse_company(
            "https://acme.test", self.request,
            fetch=lambda url: pages.get(url, FetchResult(url, error="404 not found")),
            profile=self.profile,
        )

        broken = [f for f in result.findings if f.kind == "broken_link_signal"]
        self.assertEqual(len(broken), 1)
        self.assertIn("https://acme.test/case-studies/old-page", broken[0].source_url)

    @patch("scout.research.discover_sitemap_pages", return_value=[])
    def test_a_guessed_fallback_path_that_404s_is_not_flagged(self, _discover):
        # No sitemap, no matching link on the homepage - evidence_urls() falls
        # back to guessing profile.fallback_paths. A guess being wrong is not
        # evidence about the company's own site.
        pages = {
            "https://acme.test": fetched("https://acme.test", "<p>Melbourne VIC Australia</p>"),
        }

        result = analyse_company(
            "https://acme.test", self.request,
            fetch=lambda url: pages.get(url, FetchResult(url, error="404 not found")),
            profile=self.profile,
        )

        self.assertFalse(any(f.kind == "broken_link_signal" for f in result.findings))

    @patch("scout.research.discover_sitemap_pages")
    def test_a_sitemap_sourced_image_link_is_not_flagged_as_broken(self, discover):
        # Confirmed real false positive (accelit.com.au, ideabox.com.au): a
        # sitemap/homepage link to an image loads fine (200) but isn't HTML -
        # that's not evidence of a dead link, just not usable page text.
        discover.return_value = ["https://acme.test/case-studies/photo.jpg"]
        pages = {
            "https://acme.test": fetched("https://acme.test", "<p>Melbourne VIC Australia</p>"),
            "https://acme.test/case-studies/photo.jpg": FetchResult(
                "https://acme.test/case-studies/photo.jpg",
                status=200,
                error="not an HTML page (content-type: image/jpeg)",
            ),
        }

        result = analyse_company(
            "https://acme.test", self.request,
            fetch=lambda url: pages.get(url, FetchResult(url, error="404 not found")),
            profile=self.profile,
        )

        self.assertFalse(any(f.kind == "broken_link_signal" for f in result.findings))

    @patch("scout.research.discover_sitemap_pages")
    def test_broken_link_never_takes_the_findings_zero_slot(self, discover):
        # A real role finding must still win first slot and decide
        # eligibility, even when a broken link was also found.
        discover.return_value = ["https://acme.test/careers", "https://acme.test/case-studies/dead"]
        pages = {
            "https://acme.test": fetched("https://acme.test", "<p>Melbourne VIC Australia</p>"),
            "https://acme.test/careers": fetched("https://acme.test/careers", "<h1>Web Developer</h1><p>Maintain customer website features.</p>"),
        }

        result = analyse_company(
            "https://acme.test", self.request,
            fetch=lambda url: pages.get(url, FetchResult(url, error="404 not found")),
            profile=self.profile,
        )

        self.assertEqual(result.findings[0].kind, "advertised_role_signal")
        self.assertEqual(result.status, "eligible")
        self.assertTrue(any(f.kind == "broken_link_signal" for f in result.findings))


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

    def test_hidden_need_finding_flags_professional_services_technical_ambiguity(self):
        # Confirmed live against bgprivate.com.au this session: "technical"
        # in an accounting firm's own job ads means tax/audit domain depth,
        # never software skill - a real terminology collision this sector
        # is prone to that other sectors aren't.
        profile = next(profile for profile in load_profiles() if profile.id == "technology")
        result = CompanyResult(domain="https://bgprivate.test", name="bgprivate.test", sector="professional services")

        finding = hidden_need_finding(
            result, "https://bgprivate.test",
            "Strong technical accounting and advisory expertise required.",
            profile, contact=None,
        )

        self.assertIn("usually means domain expertise", finding.suggestion)

    def test_hidden_need_finding_does_not_flag_technical_ambiguity_outside_professional_services(self):
        profile = next(profile for profile in load_profiles() if profile.id == "technology")
        result = CompanyResult(domain="https://hotdoc.test", name="hotdoc.test", sector="health")

        finding = hidden_need_finding(
            result, "https://hotdoc.test",
            "Always be empathetic. We offer a benefits pyramid and great office photos.",
            profile, contact=None,
        )

        self.assertNotIn("usually means domain expertise", finding.suggestion)


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
