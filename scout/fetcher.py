"""Polite single-page fetcher for Prospect Scout.

Unit 1: fetch one URL's HTML while honoring the site's robots.txt.
The only server contacted is the target site itself — first for its
robots.txt, then (if allowed) for the page. No parsing happens here
beyond what politeness requires; that's Unit 2's job.
"""

from __future__ import annotations

import threading
import time
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


def _fetch_robots(url: str, client: httpx.Client) -> Protego | None:
    """Fetch and parse `url`'s robots.txt, or None if missing/unreachable.

    A missing or unreachable robots.txt counts as permission (the accepted
    convention for 4xx/no-file) - callers treat None as "allow everything, no
    declared crawl delay." This fetch itself is never throttled by a
    previously-seen Crawl-delay: the delay value lives inside the file being
    fetched, so honoring it here would mean waiting on information not yet
    read (chicken-and-egg on a host's very first request this run).
    """
    try:
        response = client.get(_robots_url(url))
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    return Protego.parse(response.text)


def robots_allows(url: str, client: httpx.Client) -> bool:
    """True if the site's robots.txt permits fetching `url`.

    Uses Protego rather than stdlib's urllib.robotparser, which only
    implements the 1996 draft and has no support for the `*`/`$` wildcard
    syntax RFC 9309 requires (confirmed live against real sites in
    python/cpython#115644) - Protego is the RFC 9309-compliant parser Scrapy
    itself adopted as its default for the same reason. Note the flipped
    argument order versus stdlib: url first, user agent second.
    """
    parser = _fetch_robots(url, client)
    if parser is None:
        return True
    return parser.can_fetch(url, USER_AGENT)


# Per-host "last request sent at" timestamps, shared across every fetch_page()
# call in this process. run_research() can fetch several companies' domains
# concurrently, so this is lock-guarded rather than a
# plain dict - two threads racing to fetch the same host must still end up
# spaced apart by that host's own declared Crawl-delay, not both slip through
# at once.
_LAST_REQUEST_AT: dict[str, float] = {}
_LAST_REQUEST_LOCK = threading.Lock()


def _wait_for_crawl_delay(host: str, delay: float) -> None:
    """Block until at least `delay` seconds have passed since the last
    request to `host` was sent, then claim this moment as the new "last
    request" before releasing the lock - so a second thread that wakes up
    from the same wait recomputes against the claim just made, instead of
    both proceeding together.
    """
    while True:
        with _LAST_REQUEST_LOCK:
            last = _LAST_REQUEST_AT.get(host)
            now = time.monotonic()
            if last is None or now - last >= delay:
                _LAST_REQUEST_AT[host] = now
                return
            remaining = delay - (now - last)
        time.sleep(remaining)


def fetch_page(url: str) -> FetchResult:
    """Fetch one page politely: robots.txt first, then the page itself."""
    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        parser = _fetch_robots(url, client)
        if parser is not None and not parser.can_fetch(url, USER_AGENT):
            return FetchResult(url, error="robots.txt disallows fetching this page")

        delay = parser.crawl_delay(USER_AGENT) if parser is not None else None
        if delay:
            _wait_for_crawl_delay(urlsplit(url).netloc, delay)

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
