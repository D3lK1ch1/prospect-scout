"""Sitemap-based page discovery for Prospect Scout.

Finds case-study/blog/career page URLs via the standard sitemap protocol
instead of guessing which nav link leads where. A missing sitemap is a
neutral, common CMS-default outcome -- never treated as a signal about the
company.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

import httpx

from scout.fetcher import TIMEOUT_SECONDS, USER_AGENT

SITEMAP_PATH_KEYWORDS = (
    "case", "customer", "success-stor", "our-work", "project",
    "portfolio", "insight", "resource", "career", "job", "blog",
)

_FALLBACK_SITEMAP_PATHS = ("/sitemap.xml", "/sitemap_index.xml")
_MAX_SITEMAPS_FETCHED = 10
_MAX_SITEMAP_DEPTH = 2


def find_sitemap_urls(base_url: str) -> list[str]:
    """Declared sitemap URL(s) from robots.txt; guessed defaults if unreachable.

    The guessed fallback is neutral, not a negative signal: many legitimate
    sites (especially hand-built ones without a CMS SEO plugin) never
    declare a sitemap at all.
    """
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
        declared = _sitemaps_from_robots(base_url, client)
    if declared:
        return declared
    return [urljoin(base_url, path) for path in _FALLBACK_SITEMAP_PATHS]


def _sitemaps_from_robots(base_url: str, client: httpx.Client) -> list[str]:
    try:
        response = client.get(urljoin(base_url, "/robots.txt"))
    except httpx.HTTPError:
        return []
    if response.status_code >= 400:
        return []
    found: list[str] = []
    for line in response.text.splitlines():
        if line.strip().lower().startswith("sitemap:"):
            url = line.split(":", 1)[1].strip()
            if url and url not in found:
                found.append(url)
    return found


def discover_sitemap_pages(base_url: str, keywords: tuple[str, ...] = SITEMAP_PATH_KEYWORDS, limit: int = 5) -> list[str]:
    """Up to `limit` same-site URLs from base_url's sitemap(s) matching `keywords`."""
    sitemap_urls = find_sitemap_urls(base_url)
    if not sitemap_urls:
        return []

    matches: list[str] = []
    seen: set[str] = set()
    fetched = 0
    queue: list[tuple[str, int]] = [(url, 0) for url in sitemap_urls]

    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
        while queue and fetched < _MAX_SITEMAPS_FETCHED and len(matches) < limit:
            sitemap_url, depth = queue.pop(0)
            fetched += 1
            root = _fetch_sitemap_xml(sitemap_url, client)
            if root is None:
                continue
            kind = _local_name(root.tag)
            if kind == "sitemapindex" and depth < _MAX_SITEMAP_DEPTH:
                for loc in _iter_local(root, "loc"):
                    if loc.text and loc.text.strip():
                        queue.append((loc.text.strip(), depth + 1))
            elif kind == "urlset":
                for loc in _iter_local(root, "loc"):
                    url = (loc.text or "").strip()
                    if not url or url in seen:
                        continue
                    if urlsplit(url).netloc != urlsplit(base_url).netloc:
                        continue  # same-origin guard, matching evidence_urls()'s existing precedent
                    path = urlsplit(url).path.lower()
                    if any(keyword in path for keyword in keywords):
                        seen.add(url)
                        matches.append(url)
                        if len(matches) >= limit:
                            break

    return matches[:limit]


def _fetch_sitemap_xml(url: str, client: httpx.Client) -> ET.Element | None:
    try:
        response = client.get(url)
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    try:
        return ET.fromstring(response.content)
    except ET.ParseError:
        return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _iter_local(element: ET.Element, name: str):
    return (el for el in element.iter() if _local_name(el.tag) == name)
