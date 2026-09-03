"""Offline tests for Prospect Scout's completed fetch foundation."""

import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import httpx

import scout.fetcher
from scout.fetcher import FetchResult, robots_allows


class StubClient:
    def __init__(self, response=None, error=None, responses=None):
        self.response = response
        self.error = error
        self.responses = responses or {}
        self.urls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.responses.get(url, self.response)


def response(url, status=200, text="", headers=None):
    """Make an httpx response whose URL can be safely inspected."""
    return httpx.Response(
        status,
        text=text,
        headers=headers,
        request=httpx.Request("GET", url),
    )


class RobotsAllowsTests(unittest.TestCase):
    def test_disallow_rule_is_honoured(self):
        client = StubClient(response("https://example.test/robots.txt", text="User-agent: *\nDisallow: /private"))

        self.assertFalse(robots_allows("https://example.test/private/a", client))
        self.assertEqual(client.urls, ["https://example.test/robots.txt"])

    def test_missing_robots_is_treated_as_permission(self):
        self.assertTrue(robots_allows("https://example.test/a", StubClient(response("https://example.test/robots.txt", 404))))

    def test_robots_network_error_is_treated_as_permission(self):
        client = StubClient(error=httpx.ConnectError("offline"))
        self.assertTrue(robots_allows("https://example.test/a", client))


class FetchResultTests(unittest.TestCase):
    def test_ok_requires_html(self):
        self.assertTrue(FetchResult("https://example.test", html="<html>").ok)
        self.assertFalse(FetchResult("https://example.test", error="blocked").ok)

    @patch("scout.fetcher.httpx.Client")
    def test_robots_denial_prevents_page_fetch(self, client_factory):
        from scout.fetcher import fetch_page

        client = StubClient(response("https://example.test/robots.txt", text="User-agent: *\nDisallow: /"))
        client_factory.return_value = client

        result = fetch_page("https://example.test/private")

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "robots.txt disallows fetching this page")
        self.assertEqual(client.urls, ["https://example.test/robots.txt"])

    @patch("scout.fetcher.httpx.Client")
    def test_non_html_response_has_diagnostic_context(self, client_factory):
        from scout.fetcher import fetch_page

        robots = response("https://example.test/robots.txt", text="User-agent: *\nDisallow:")
        page = response("https://example.test/file.pdf", headers={"content-type": "application/pdf"})
        client = StubClient(responses={"https://example.test/robots.txt": robots, "https://example.test/file.pdf": page})
        client_factory.return_value = client

        result = fetch_page("https://example.test/file.pdf")

        self.assertFalse(result.ok)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.final_url, "https://example.test/file.pdf")
        self.assertIn("not an HTML page", result.error)


class CliTests(unittest.TestCase):
    def test_invalid_url_fails_before_fetching(self):
        from scout.__main__ import cmd_audit

        output = StringIO()
        with patch("scout.__main__.fetch_page") as fetch, redirect_stdout(output):
            result = cmd_audit("example.test")

        self.assertEqual(result, 1)
        fetch.assert_not_called()
        self.assertIn("Include http:// or https://", output.getvalue())

    def test_inspect_invalid_url_fails_before_fetching(self):
        from argparse import Namespace

        from scout.__main__ import cmd_inspect

        args = Namespace(domain="known.test", role=[], profile="technology", profile_name=None, page_term=[], opportunity_prompt=None, output="reports/x.md")

        output = StringIO()
        with patch("scout.research.fetch_page") as fetch, redirect_stdout(output):
            result = cmd_inspect(args)

        self.assertEqual(result, 1)
        fetch.assert_not_called()
        self.assertIn("Include http:// or https://", output.getvalue())


class CrawlDelayTests(unittest.TestCase):
    """_wait_for_crawl_delay() tested directly against a mocked clock - no
    real sleeping, so this stays fast and deterministic regardless of the
    delay value used.
    """

    def setUp(self):
        scout.fetcher._LAST_REQUEST_AT.clear()

    def test_first_request_to_a_host_never_waits(self):
        with patch("scout.fetcher.time.monotonic", return_value=100.0), patch("scout.fetcher.time.sleep") as sleep:
            scout.fetcher._wait_for_crawl_delay("example.test", 10.0)

        sleep.assert_not_called()
        self.assertEqual(scout.fetcher._LAST_REQUEST_AT["example.test"], 100.0)

    def test_second_request_inside_the_delay_window_sleeps_for_the_remainder(self):
        # 100.0: first call claims this instant. 100.05: second call arrives
        # 0.05s later, so 0.15s remains of the 0.2s delay. 100.2: the recheck
        # after the (mocked) sleep, simulating that the sleep occurred.
        clock = iter([100.0, 100.05, 100.2])
        with patch("scout.fetcher.time.monotonic", side_effect=lambda: next(clock)), patch("scout.fetcher.time.sleep") as sleep:
            scout.fetcher._wait_for_crawl_delay("example.test", 0.2)
            scout.fetcher._wait_for_crawl_delay("example.test", 0.2)

        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args[0][0], 0.15, places=5)

    @patch("scout.fetcher.httpx.Client")
    def test_fetch_page_honours_a_declared_crawl_delay(self, client_factory):
        from scout.fetcher import fetch_page

        robots = response("https://example.test/robots.txt", text="User-agent: *\nCrawl-delay: 7")
        page = response("https://example.test/a", text="<html>ok</html>", headers={"content-type": "text/html"})
        client = StubClient(responses={"https://example.test/robots.txt": robots, "https://example.test/a": page})
        client_factory.return_value = client

        with patch("scout.fetcher._wait_for_crawl_delay") as wait:
            fetch_page("https://example.test/a")

        wait.assert_called_once_with("example.test", 7.0)

    @patch("scout.fetcher.httpx.Client")
    def test_fetch_page_never_waits_when_no_delay_is_declared(self, client_factory):
        from scout.fetcher import fetch_page

        robots = response("https://example.test/robots.txt", text="User-agent: *\nDisallow:")
        page = response("https://example.test/a", text="<html>ok</html>", headers={"content-type": "text/html"})
        client = StubClient(responses={"https://example.test/robots.txt": robots, "https://example.test/a": page})
        client_factory.return_value = client

        with patch("scout.fetcher._wait_for_crawl_delay") as wait:
            fetch_page("https://example.test/a")

        wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
