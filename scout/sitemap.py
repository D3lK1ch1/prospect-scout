"""Sitemap-based page discovery for Prospect Scout.

Finds case-study/blog/career page URLs via the standard sitemap protocol
instead of guessing which nav link leads where. A missing sitemap is a
neutral, common CMS-default outcome -- never treated as a signal about the
company.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

import httpx

from scout.fetcher import TIMEOUT_SECONDS, USER_AGENT

# "Strong" keywords are precise page-type signals - a hit here is very
# likely the actual page, not a passing mention. "Weak" keywords are looser
# content-hub words: real signal on a small site whose one relevant page is
# literally called "/resources", but on a large content-marketing site they
# flood with thousands of unrelated blog/tag URLs (confirmed live against
# myob.com's real sitemap - "resource"/"insight" alone matched its entire
# blog taxonomy before ever reaching the two genuine case-study posts in the
# same file; see RESEARCH.md). Both tiers still count for *inclusion* - only
# ranking within discover_sitemap_pages() tells them apart.
STRONG_PATH_KEYWORDS = (
    "case", "customer", "success-stor", "our-work", "project",
    "portfolio", "career", "job", "role",
)
_WEAK_PATH_KEYWORDS = ("insight", "resource", "blog")
SITEMAP_PATH_KEYWORDS = STRONG_PATH_KEYWORDS + _WEAK_PATH_KEYWORDS

_STOPWORDS = frozenset({"a", "an", "the", "of", "and", "or", "for", "to", "in", "on", "with"})

_FALLBACK_SITEMAP_PATHS = ("/sitemap.xml", "/sitemap_index.xml")
_MAX_SITEMAPS_FETCHED = 10
_MAX_SITEMAP_DEPTH = 2

# Compound public suffixes this project's Australian/NZ/UK-adjacent research
# actually encounters - add more as real runs need them, not speculatively
# (same policy already used for research.py's _COUNTRY_TLDS). Not a full
# public-suffix-list implementation; just enough to tell "careers.myob.com"
# (a subdomain of myob.com, MYOB's own registrable domain) apart from a
# genuinely different company's domain.
_COMPOUND_PUBLIC_SUFFIXES = frozenset({
    "com.au", "net.au", "org.au", "gov.au", "edu.au", "id.au", "asn.au",
    "co.uk", "org.uk", "gov.uk", "co.nz", "org.nz", "govt.nz",
})


def _registrable_domain(hostname: str) -> str:
    labels = hostname.lower().split(".")
    if len(labels) <= 2:
        return hostname.lower()
    last_two = ".".join(labels[-2:])
    if last_two in _COMPOUND_PUBLIC_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return last_two


def same_site(netloc_a: str, netloc_b: str) -> bool:
    """True for an exact host match or a subdomain of the same registrable
    domain - e.g. careers.myob.com counts as myob.com's own site. Confirmed
    real case: MYOB's own homepage links its careers portal at exactly that
    subdomain, but MYOB's sitemap never references it at all - see
    RESEARCH.md. Never crosses to a genuinely different company's domain.
    """
    return _registrable_domain(netloc_a) == _registrable_domain(netloc_b)


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


def _path_matches(path: str, keywords: tuple[str, ...], whole_word: bool) -> bool:
    """Substring match by default - deliberately loose so a truncated keyword
    like "success-stor" still catches "success-story"/"success-stories".
    `whole_word=True` instead requires a real word/phrase boundary (via `\\b`,
    same mechanism already used by matching_roles()/contact_finding()) -
    confirmed real false positive without it: the team-page keyword "team"
    matched inside "microsoft-teams" (an unrelated product name in a blog
    URL, not an org-chart "team"), because "teams" is also a lexically valid
    plural of "team" - a boundary check alone can't tell the two apart, but
    a *bare* `\\b...\\b` (no plural tolerance, unlike matching_roles()) can:
    it requires a non-word character (or the URL's start/end) immediately
    after the keyword, which "teams" fails and "meet-the-team/" satisfies.
    """
    if not whole_word:
        return any(keyword in path for keyword in keywords)
    return any(re.search(rf"\b{re.escape(keyword)}\b", path) for keyword in keywords)


def _term_words(term: str) -> list[str]:
    return [word for word in re.split(r"\s+", term.lower()) if word and word not in _STOPWORDS]


def _term_matches_path(term_words: list[str], path_words: set[str]) -> bool:
    """All of a term's words must appear in the path - not just one.

    A single-word check would let a generic word shared across role terms
    (e.g. "software" appearing in both "software developer" and "software
    engineer") spuriously boost a company's own product pages, which use the
    same generic vocabulary without being job-related at all. Confirmed real
    case: myob.com's product pages ("job-software-for-construction",
    "project-accounting-software") outranked its genuine case-study post
    under single-word matching, because "software" alone matched, even
    though "developer"/"engineer" never appeared alongside it.
    """
    return bool(term_words) and all(word in path_words for word in term_words)


def _match_score(path: str, priority_keywords: tuple[str, ...], whole_word: bool, boost_terms: tuple[str, ...]) -> int:
    """Higher ranks first: a precise page-type keyword outranks a merely-included
    weak one, and a path whose words fully cover one of the requester's own
    typed role/interest terms outranks an equally-tiered page that doesn't.
    """
    score = 0
    if priority_keywords and _path_matches(path, priority_keywords, whole_word):
        score += 2
    if boost_terms:
        path_words = set(re.split(r"[/\-_]+", path))
        if any(_term_matches_path(_term_words(term), path_words) for term in boost_terms):
            score += 1
    return score


def discover_sitemap_pages(
    base_url: str,
    keywords: tuple[str, ...] = SITEMAP_PATH_KEYWORDS,
    limit: int = 5,
    whole_word: bool = False,
    priority_keywords: tuple[str, ...] = (),
    boost_terms: tuple[str, ...] = (),
) -> list[str]:
    """Up to `limit` same-site URLs from base_url's sitemap(s) matching `keywords`,
    ranked so the most relevant ones survive the cutoff.

    Walks every reachable sitemap file up to the network-call bound below -
    not just until `limit` raw matches turn up. A single sitemap file can list
    thousands of URLs, and the first `limit` keyword hits in document order
    are not necessarily the most relevant ones (confirmed real case: a large
    site's sitemap listed blog/tag pages matching a loose keyword before its
    own genuine case-study posts, later in the same file - see RESEARCH.md).
    `priority_keywords` (a precise subset of `keywords`) and `boost_terms`
    (the requester's own typed roles/interests) only affect ranking among
    already-included matches, never which pages get included at all.
    """
    sitemap_urls = find_sitemap_urls(base_url)
    if not sitemap_urls:
        return []

    matches: list[str] = []
    seen: set[str] = set()
    fetched = 0
    queue: list[tuple[str, int]] = [(url, 0) for url in sitemap_urls]

    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
        while queue and fetched < _MAX_SITEMAPS_FETCHED:
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
                    if not same_site(urlsplit(url).netloc, urlsplit(base_url).netloc):
                        continue  # same-registrable-domain guard - never a genuinely different company's domain
                    path = urlsplit(url).path.lower()
                    if _path_matches(path, keywords, whole_word):
                        seen.add(url)
                        matches.append(url)

    # Stable sort: Python's reverse=True still preserves original relative
    # order among equal scores, so untiered matches keep document order.
    ranked = sorted(matches, key=lambda url: _match_score(urlsplit(url).path.lower(), priority_keywords, whole_word, boost_terms), reverse=True)
    return ranked[:limit]


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
