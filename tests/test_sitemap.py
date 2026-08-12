"""Fixture-based tests for sitemap-based page discovery. No live network calls.

Fixture shapes are trimmed from real sitemap structures captured manually
this session: a two-level content-type-split index (Rank Math/WordPress),
a flat single urlset with path-keyword-only splitting (Yoast/AllInOneSEO/
custom sites), and a site with no sitemap declared at all.
"""

import unittest
from unittest.mock import patch

import httpx

from scout.research import _TEAM_PAGE_KEYWORDS
from scout.sitemap import STRONG_PATH_KEYWORDS, discover_sitemap_pages, find_sitemap_urls

SITEMAP_NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


class StubClient:
    def __init__(self, responses=None, error=None):
        self.responses = responses or {}
        self.error = error
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.responses.get(url, httpx.Response(404, text="not found", request=httpx.Request("GET", url)))


def xml_response(url, body, status=200):
    return httpx.Response(status, text=body, request=httpx.Request("GET", url))


def robots_with_sitemap(sitemap_url):
    return f"User-agent: *\nDisallow:\n\nSitemap: {sitemap_url}\n"


def sitemapindex(*sub_sitemap_urls):
    entries = "".join(f"<sitemap><loc>{url}</loc></sitemap>" for url in sub_sitemap_urls)
    return f'<?xml version="1.0"?><sitemapindex {SITEMAP_NS}>{entries}</sitemapindex>'


def urlset(*page_urls):
    entries = "".join(f"<url><loc>{url}</loc></url>" for url in page_urls)
    return f'<?xml version="1.0"?><urlset {SITEMAP_NS}>{entries}</urlset>'


class FindSitemapUrlsTests(unittest.TestCase):
    @patch("scout.sitemap.httpx.Client")
    def test_declared_sitemap_from_robots_txt_is_used(self, client_factory):
        client_factory.return_value = StubClient({
            "https://example.test/robots.txt": xml_response(
                "https://example.test/robots.txt", robots_with_sitemap("https://example.test/sitemap_index.xml")
            ),
        })

        self.assertEqual(find_sitemap_urls("https://example.test"), ["https://example.test/sitemap_index.xml"])

    @patch("scout.sitemap.httpx.Client")
    def test_missing_robots_falls_back_to_guessed_paths(self, client_factory):
        client_factory.return_value = StubClient()  # robots.txt not stubbed -> 404

        self.assertEqual(
            find_sitemap_urls("https://example.test"),
            ["https://example.test/sitemap.xml", "https://example.test/sitemap_index.xml"],
        )

    @patch("scout.sitemap.httpx.Client")
    def test_robots_network_error_falls_back_to_guessed_paths(self, client_factory):
        client_factory.return_value = StubClient(error=httpx.ConnectError("offline"))

        self.assertEqual(
            find_sitemap_urls("https://example.test"),
            ["https://example.test/sitemap.xml", "https://example.test/sitemap_index.xml"],
        )


class DiscoverSitemapPagesTests(unittest.TestCase):
    @patch("scout.sitemap.httpx.Client")
    def test_two_level_content_type_split_shape(self, client_factory):
        """Real Rank Math/WordPress shape: sitemapindex with a dedicated case-study sub-sitemap."""
        client_factory.return_value = StubClient({
            "https://example.test/robots.txt": xml_response(
                "https://example.test/robots.txt", robots_with_sitemap("https://example.test/sitemap_index.xml")
            ),
            "https://example.test/sitemap_index.xml": xml_response(
                "https://example.test/sitemap_index.xml",
                sitemapindex("https://example.test/casestudy-sitemap.xml", "https://example.test/page-sitemap.xml"),
            ),
            "https://example.test/casestudy-sitemap.xml": xml_response(
                "https://example.test/casestudy-sitemap.xml",
                urlset("https://example.test/casestudy/alpha", "https://example.test/casestudy/beta"),
            ),
            "https://example.test/page-sitemap.xml": xml_response(
                "https://example.test/page-sitemap.xml",
                urlset("https://example.test/about", "https://example.test/contact"),
            ),
        })

        result = discover_sitemap_pages("https://example.test")

        self.assertEqual(
            set(result),
            {"https://example.test/casestudy/alpha", "https://example.test/casestudy/beta"},
        )

    @patch("scout.sitemap.httpx.Client")
    def test_flat_single_urlset_shape(self, client_factory):
        """Real Yoast/AllInOneSEO/MYOB shape: one urlset, path-keyword-only splitting."""
        client_factory.return_value = StubClient({
            "https://flat.test/robots.txt": xml_response(
                "https://flat.test/robots.txt", robots_with_sitemap("https://flat.test/sitemap.xml")
            ),
            "https://flat.test/sitemap.xml": xml_response(
                "https://flat.test/sitemap.xml",
                urlset(
                    "https://flat.test/blog/post-1",
                    "https://flat.test/our-work/project-a",
                    "https://flat.test/careers",
                    "https://flat.test/privacy-policy",
                ),
            ),
        })

        result = discover_sitemap_pages("https://flat.test")

        self.assertEqual(
            set(result),
            {"https://flat.test/blog/post-1", "https://flat.test/our-work/project-a", "https://flat.test/careers"},
        )
        self.assertNotIn("https://flat.test/privacy-policy", result)

    @patch("scout.sitemap.httpx.Client")
    def test_no_sitemap_anywhere_returns_empty_and_tries_both_fallbacks(self, client_factory):
        client = StubClient()  # everything 404s
        client_factory.return_value = client

        result = discover_sitemap_pages("https://nositemap.test")

        self.assertEqual(result, [])
        self.assertIn("https://nositemap.test/sitemap.xml", client.calls)
        self.assertIn("https://nositemap.test/sitemap_index.xml", client.calls)

    @patch("scout.sitemap.httpx.Client")
    def test_sitemapindex_recursion_is_bounded(self, client_factory):
        sub_sitemaps = [f"https://example.test/sub-{i}.xml" for i in range(15)]
        client = StubClient({
            "https://example.test/robots.txt": xml_response(
                "https://example.test/robots.txt", robots_with_sitemap("https://example.test/sitemap_index.xml")
            ),
            "https://example.test/sitemap_index.xml": xml_response(
                "https://example.test/sitemap_index.xml", sitemapindex(*sub_sitemaps)
            ),
            # sub-sitemaps deliberately left unstubbed -> each 404s, isolating the fetch-count bound
        })
        client_factory.return_value = client

        result = discover_sitemap_pages("https://example.test")

        self.assertEqual(result, [])
        # 1 robots.txt + at most 10 sitemap-level fetches (the bound), never all 15 sub-sitemaps
        sitemap_level_calls = [call for call in client.calls if call != "https://example.test/robots.txt"]
        self.assertLessEqual(len(sitemap_level_calls), 10)

    @patch("scout.sitemap.httpx.Client")
    def test_depth_beyond_max_is_not_followed(self, client_factory):
        """index1 -> index2 -> index3 -> leaf: index3 sits at depth 2, so its own
        children (leaf) are never enqueued, since depth < _MAX_SITEMAP_DEPTH (2) is false."""
        client_factory.return_value = StubClient({
            "https://deep.test/robots.txt": xml_response(
                "https://deep.test/robots.txt", robots_with_sitemap("https://deep.test/index1.xml")
            ),
            "https://deep.test/index1.xml": xml_response(
                "https://deep.test/index1.xml", sitemapindex("https://deep.test/index2.xml")
            ),
            "https://deep.test/index2.xml": xml_response(
                "https://deep.test/index2.xml", sitemapindex("https://deep.test/index3.xml")
            ),
            "https://deep.test/index3.xml": xml_response(
                "https://deep.test/index3.xml", sitemapindex("https://deep.test/leaf.xml")
            ),
            "https://deep.test/leaf.xml": xml_response(
                "https://deep.test/leaf.xml", urlset("https://deep.test/case-studies/too-deep")
            ),
        })

        result = discover_sitemap_pages("https://deep.test")

        self.assertEqual(result, [])

    @patch("scout.sitemap.httpx.Client")
    def test_malformed_xml_is_skipped_not_fatal(self, client_factory):
        client_factory.return_value = StubClient({
            "https://example.test/robots.txt": xml_response(
                "https://example.test/robots.txt", robots_with_sitemap("https://example.test/sitemap_index.xml")
            ),
            "https://example.test/sitemap_index.xml": xml_response(
                "https://example.test/sitemap_index.xml",
                sitemapindex("https://example.test/broken.xml", "https://example.test/good.xml"),
            ),
            "https://example.test/broken.xml": xml_response("https://example.test/broken.xml", "not xml at all <<<"),
            "https://example.test/good.xml": xml_response(
                "https://example.test/good.xml", urlset("https://example.test/case-studies/real")
            ),
        })

        result = discover_sitemap_pages("https://example.test")

        self.assertEqual(result, ["https://example.test/case-studies/real"])

    @patch("scout.sitemap.httpx.Client")
    def test_limit_caps_results_at_five(self, client_factory):
        many_matches = [f"https://example.test/case-studies/{i}" for i in range(7)]
        client_factory.return_value = StubClient({
            "https://example.test/robots.txt": xml_response(
                "https://example.test/robots.txt", robots_with_sitemap("https://example.test/sitemap.xml")
            ),
            "https://example.test/sitemap.xml": xml_response("https://example.test/sitemap.xml", urlset(*many_matches)),
        })

        result = discover_sitemap_pages("https://example.test")

        self.assertEqual(len(result), 5)

    @patch("scout.sitemap.httpx.Client")
    def test_case_keyword_matches_both_hyphenated_and_unhyphenated_urls(self, client_factory):
        client_factory.return_value = StubClient({
            "https://example.test/robots.txt": xml_response(
                "https://example.test/robots.txt", robots_with_sitemap("https://example.test/sitemap.xml")
            ),
            "https://example.test/sitemap.xml": xml_response(
                "https://example.test/sitemap.xml",
                urlset("https://example.test/casestudy/foo", "https://example.test/case-study/bar"),
            ),
        })

        result = discover_sitemap_pages("https://example.test")

        self.assertEqual(
            set(result),
            {"https://example.test/casestudy/foo", "https://example.test/case-study/bar"},
        )

    @patch("scout.sitemap.httpx.Client")
    def test_cross_origin_entries_are_excluded(self, client_factory):
        client_factory.return_value = StubClient({
            "https://example.test/robots.txt": xml_response(
                "https://example.test/robots.txt", robots_with_sitemap("https://example.test/sitemap.xml")
            ),
            "https://example.test/sitemap.xml": xml_response(
                "https://example.test/sitemap.xml",
                urlset("https://example.test/case-studies/mine", "https://other-domain.test/case-studies/theirs"),
            ),
        })

        result = discover_sitemap_pages("https://example.test")

        self.assertEqual(result, ["https://example.test/case-studies/mine"])


class RankedMatchingTests(unittest.TestCase):
    """Regression for a confirmed real bug found live against myob.com: a
    single 3,400-line sitemap listed dozens of weak-keyword ("resource"/
    "insight") blog/tag pages before its two genuine case-study posts, later
    in the same file. The old code stopped scanning the moment it hit
    `limit` raw matches - it never reached the real ones at all.
    """

    @patch("scout.sitemap.httpx.Client")
    def test_whole_sitemap_is_scanned_not_just_the_first_limit_matches(self, client_factory):
        # 6 weak-keyword matches appear before 2 strong-keyword ones in
        # document order - old code (limit=5, stop-on-first-5) would never
        # even see the strong ones.
        weak_first = [f"https://example.test/insight/topic/{i}" for i in range(6)]
        strong_later = [
            "https://example.test/insight/post/real-case-study",
            "https://example.test/insight/post/another-case-story",
        ]
        client_factory.return_value = StubClient({
            "https://example.test/robots.txt": xml_response(
                "https://example.test/robots.txt", robots_with_sitemap("https://example.test/sitemap.xml")
            ),
            "https://example.test/sitemap.xml": xml_response(
                "https://example.test/sitemap.xml", urlset(*weak_first, *strong_later)
            ),
        })

        result = discover_sitemap_pages("https://example.test", priority_keywords=STRONG_PATH_KEYWORDS)

        self.assertEqual(set(result) & set(strong_later), set(strong_later), "both strong matches should survive the cutoff")

    @patch("scout.sitemap.httpx.Client")
    def test_priority_keywords_outrank_weak_only_matches(self, client_factory):
        client_factory.return_value = StubClient({
            "https://example.test/robots.txt": xml_response(
                "https://example.test/robots.txt", robots_with_sitemap("https://example.test/sitemap.xml")
            ),
            "https://example.test/sitemap.xml": xml_response(
                "https://example.test/sitemap.xml",
                urlset("https://example.test/insight/topic/gst", "https://example.test/insight/post/case-study-acme"),
            ),
        })

        result = discover_sitemap_pages("https://example.test", limit=1, priority_keywords=STRONG_PATH_KEYWORDS)

        self.assertEqual(result, ["https://example.test/insight/post/case-study-acme"])

    @patch("scout.sitemap.httpx.Client")
    def test_boost_term_requires_all_its_words_not_just_one_shared_word(self, client_factory):
        # Regression for a confirmed real bug: single-word boost matching let
        # "software" alone (shared between "software developer" and MYOB's
        # own product-page vocabulary) artificially outrank a genuine
        # case-study page that never mentioned "developer"/"engineer" at
        # all - even though both are otherwise the same priority tier ("job"
        # and "case" are both strong keywords) and the case study is listed
        # first in the sitemap. With the bug, the product page's spurious
        # +1 pushed it ahead despite that; without it, the tie is broken by
        # document order, same as any other equally-tiered pair.
        client_factory.return_value = StubClient({
            "https://example.test/robots.txt": xml_response(
                "https://example.test/robots.txt", robots_with_sitemap("https://example.test/sitemap.xml")
            ),
            "https://example.test/sitemap.xml": xml_response(
                "https://example.test/sitemap.xml",
                urlset(
                    "https://example.test/insight/post/case-study-acme",  # genuine case study: no boost words at all
                    "https://example.test/features/job-software-for-tradies",  # product page: "software" only
                ),
            ),
        })

        result = discover_sitemap_pages(
            "https://example.test",
            limit=1,
            priority_keywords=STRONG_PATH_KEYWORDS,
            boost_terms=("software developer", "software engineer"),
        )

        self.assertEqual(result, ["https://example.test/insight/post/case-study-acme"])

    @patch("scout.sitemap.httpx.Client")
    def test_boost_term_ranks_a_full_phrase_match_above_a_same_tier_competitor(self, client_factory):
        client_factory.return_value = StubClient({
            "https://example.test/robots.txt": xml_response(
                "https://example.test/robots.txt", robots_with_sitemap("https://example.test/sitemap.xml")
            ),
            "https://example.test/sitemap.xml": xml_response(
                "https://example.test/sitemap.xml",
                urlset(
                    "https://example.test/careers/office-manager",
                    "https://example.test/careers/software-developer",
                ),
            ),
        })

        result = discover_sitemap_pages(
            "https://example.test", limit=1, priority_keywords=STRONG_PATH_KEYWORDS, boost_terms=("software developer",)
        )

        self.assertEqual(result, ["https://example.test/careers/software-developer"])


class WholeWordKeywordMatchingTests(unittest.TestCase):
    """Regression for a confirmed real false positive: itnetworks.com.au's
    team-contact discovery treated /blog/how-to-install-microsoft-teams/ as
    a team/leadership page, because the keyword "team" is a plain substring
    of "teams" (Microsoft Teams, the product) - a raw `in` check can't tell
    that apart from a genuine plural. whole_word=True fixes this without
    touching the default (non-team-page) substring behavior other tests in
    this file already rely on for deliberately truncated keywords like
    "success-stor".
    """

    @patch("scout.sitemap.httpx.Client")
    def test_team_keyword_does_not_match_an_unrelated_product_name_url(self, client_factory):
        client_factory.return_value = StubClient({
            "https://example.test/robots.txt": xml_response(
                "https://example.test/robots.txt", robots_with_sitemap("https://example.test/sitemap.xml")
            ),
            "https://example.test/sitemap.xml": xml_response(
                "https://example.test/sitemap.xml",
                urlset("https://example.test/blog/how-to-install-microsoft-teams/"),
            ),
        })

        result = discover_sitemap_pages("https://example.test", keywords=_TEAM_PAGE_KEYWORDS, whole_word=True)

        self.assertEqual(result, [])

    @patch("scout.sitemap.httpx.Client")
    def test_team_keyword_still_matches_a_real_team_page(self, client_factory):
        client_factory.return_value = StubClient({
            "https://example.test/robots.txt": xml_response(
                "https://example.test/robots.txt", robots_with_sitemap("https://example.test/sitemap.xml")
            ),
            "https://example.test/sitemap.xml": xml_response(
                "https://example.test/sitemap.xml",
                urlset("https://example.test/meet-the-team/", "https://example.test/our-team"),
            ),
        })

        result = discover_sitemap_pages("https://example.test", keywords=_TEAM_PAGE_KEYWORDS, whole_word=True)

        self.assertEqual(
            set(result),
            {"https://example.test/meet-the-team/", "https://example.test/our-team"},
        )

    @patch("scout.sitemap.httpx.Client")
    def test_default_substring_behavior_is_unchanged_for_truncated_keywords(self, client_factory):
        client_factory.return_value = StubClient({
            "https://example.test/robots.txt": xml_response(
                "https://example.test/robots.txt", robots_with_sitemap("https://example.test/sitemap.xml")
            ),
            "https://example.test/sitemap.xml": xml_response(
                "https://example.test/sitemap.xml",
                urlset("https://example.test/success-stories/client-a"),
            ),
        })

        result = discover_sitemap_pages("https://example.test")

        self.assertEqual(result, ["https://example.test/success-stories/client-a"])


if __name__ == "__main__":
    unittest.main()
