"""Polite single-page fetcher for Prospect Scout.

Unit 1: fetch one URL's HTML while honoring the site's robots.txt.
The only server contacted is the target site itself — first for its
robots.txt, then (if allowed) for the page. No parsing happens here
beyond what politeness requires; that's Unit 2's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx
from protego import Protego

# Identify honestly so a site owner reading their logs knows what this was.
USER_AGENT = "ProspectScout/0.1 (local personal-use site audit; run by hand)"

TIMEOUT_SECONDS = 15.0


@dataclass
class FetchResult:
    """Outcome of fetching one page.

    `html` is set on success; `error` is a human-readable reason otherwise.
    `final_url` and `status` are filled whenever a response arrived, even a
    failing one, so the report can show what the server actually said.
    """

    url: str
    final_url: str | None = None
    status: int | None = None
    html: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.html is not None


def _robots_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))


def robots_allows(url: str, client: httpx.Client) -> bool:
    """True if the site's robots.txt permits fetching `url`.

    A missing or unreachable robots.txt counts as permission (the accepted
    convention for 4xx/no-file), but any explicit rule is honored.

    Uses Protego rather than stdlib's urllib.robotparser, which only
    implements the 1996 draft and has no support for the `*`/`$` wildcard
    syntax RFC 9309 requires (confirmed live against real sites in
    python/cpython#115644) - Protego is the RFC 9309-compliant parser Scrapy
    itself adopted as its default for the same reason. Note the flipped
    argument order versus stdlib: url first, user agent second.
    """
    try:
        response = client.get(_robots_url(url))
    except httpx.HTTPError:
        return True
    if response.status_code >= 400:
        return True

    parser = Protego.parse(response.text)
    return parser.can_fetch(url, USER_AGENT)


def fetch_page(url: str) -> FetchResult:
    """Fetch one page politely: robots.txt first, then the page itself."""
    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        if not robots_allows(url, client):
            return FetchResult(url, error="robots.txt disallows fetching this page")

        try:
            response = client.get(url)
        except httpx.HTTPError as exc:
            return FetchResult(url, error=f"request failed: {exc}")

        final_url = str(response.url)
        if response.status_code >= 400:
            return FetchResult(
                url,
                final_url=final_url,
                status=response.status_code,
                error=f"server answered {response.status_code}",
            )

        content_type = response.headers.get("content-type", "")
        if "html" not in content_type:
            return FetchResult(
                url,
                final_url=final_url,
                status=response.status_code,
                error=f"not an HTML page (content-type: {content_type or 'unknown'})",
            )

        return FetchResult(
            url,
            final_url=final_url,
            status=response.status_code,
            html=response.text,
        )
