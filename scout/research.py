"""Company-site research: profile-guided signals, then cautious opportunity ideas."""

from __future__ import annotations

import functools
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from scout.fetcher import FetchResult, fetch_page
from scout.models import CompanyResult, Finding, ResearchReport, ResearchRequest
from scout.profiles import ResearchProfile, profile_by_id
from scout.sitemap import SITEMAP_PATH_KEYWORDS, STRONG_PATH_KEYWORDS, discover_sitemap_pages, same_site

Fetch = Callable[[str], FetchResult]

SECTORS = {
    "non-profit": ("donate", "charity", "not-for-profit", "nonprofit", "our mission"),
    "fashion/retail": ("shop", "collection", "add to cart", "fashion", "clothing"),
    "health": ("patient", "clinic", "healthcare", "medical"),
    "education": ("student", "course", "school", "education"),
    "professional services": ("consulting", "advisory", "our services", "legal"),
    "technology": ("software", "platform", "saas", "technology"),
}


def normalise_domain(value: str) -> str | None:
    value = value.strip()
    if not value or value.startswith("#"):
        return None
    url = value if value.startswith(("http://", "https://")) else f"https://{value}"
    parts = urlsplit(url)
    if not parts.netloc or not parts.hostname or any(character.isspace() for character in parts.netloc):
        return None
    return f"{parts.scheme}://{parts.netloc}"


def read_domains(path: str) -> list[str]:
    """Read one URL/domain per line, preserving first occurrence only."""
    found: list[str] = []
    seen: set[str] = set()
    with open(path, encoding="utf-8") as source:
        for line in source:
            domain = normalise_domain(line)
            if domain and domain not in seen:
                seen.add(domain)
                found.append(domain)
    return found


def _snap_start(text: str, index: int) -> int:
    """Nudge an excerpt's start index forward to the next word boundary,
    never backward into a word already in progress at `index`."""
    if index <= 0 or text[index - 1].isspace():
        return index
    next_space = text.find(" ", index)
    return next_space + 1 if next_space != -1 else index


def _snap_end(text: str, index: int) -> int:
    """Nudge an excerpt's end index backward to the previous word boundary."""
    if index >= len(text) or text[index].isspace():
        return index
    prev_space = text.rfind(" ", 0, index)
    return prev_space if prev_space != -1 else index


def text_excerpt(text: str, phrase: str, width: int = 180) -> str:
    """A snippet of text around `phrase`'s real, whole-word occurrence.

    Searches by word boundary first, not a raw substring find - confirmed
    real bug: a naive `.find("cto")` matched inside "Director" (a literal
    substring of that unrelated word) instead of the real "CTO" mention
    elsewhere on the page, so the excerpt - and any name-guessing built on
    it - was built around the wrong sentence entirely. Falls back to a plain
    substring search only if no whole-word match exists, so callers that
    ever hand in a genuinely partial phrase still degrade to something
    rather than nothing.

    The excerpt's start/end are also snapped to word boundaries, not cut at
    a fixed character offset - confirmed real bug: a fixed-width cut clipped
    "Workstar" down to "rkstar" because the cutoff landed two characters
    into the word, not before it.
    """
    lower = text.lower()
    match = re.search(rf"\b{re.escape(phrase.lower())}\b", lower)
    index = match.start() if match else lower.find(phrase.lower())
    if index < 0:
        return text[:width].strip()
    start = _snap_start(text, max(0, index - 60))
    end = _snap_end(text, index + len(phrase) + 100)
    return text[start:end].strip()


_CHROME_TAGS = ("nav", "header", "footer", "script", "style", "noscript")
# WordPress' near-universal accessibility skip-link ("Skip to content") sits
# directly under <body>, outside every _CHROME_TAGS wrapper above - confirmed
# real leak: dnx.solutions' case-study excerpts included the literal words
# "Skip to content" ahead of the real page content. Visually hidden from a
# sighted visitor by the class itself, not genuine page content; stripping it
# isn't losing real evidence, it's removing a screen-reader-only nav aid.
_CHROME_CLASSES = ("skip-link",)


def page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_CHROME_TAGS):
        tag.decompose()
    for class_name in _CHROME_CLASSES:
        for tag in soup.find_all(class_=class_name):
            tag.decompose()
    return soup.get_text(" ", strip=True)


def _cross_subdomain_nav_links(base_url: str, html: str, priority_keywords: tuple[str, ...]) -> list[str]:
    """Direct <a href> links on the homepage itself, to a *different*
    subdomain of the same registrable domain, whose link text or path hits a
    precise page-type keyword - e.g. a "Careers" nav link pointing at a
    dedicated careers.<company>.com portal.

    Confirmed real case: myob.com's own homepage footer links
    careers.myob.com verbatim (`<a title="Careers" href="https://careers.
    myob.com/">`), but myob.com's sitemap never references that subdomain at
    all - sitemap-based discovery alone can't reach it no matter how it's
    ranked. This is a small, targeted check (the company's own top-level
    nav/footer, already-fetched HTML, no new network calls) - not a broad
    crawl - so it runs every time, not only when the sitemap comes back
    empty; a company's own direct link to its own careers portal is stronger
    evidence than anything sitemap-ranking can infer.
    """
    soup = BeautifulSoup(html, "html.parser")
    base_netloc = urlsplit(base_url).netloc
    found: list[str] = []
    for link in soup.select("a[href]"):
        href = link["href"]
        url = urljoin(base_url, href)
        netloc = urlsplit(url).netloc
        if netloc == base_netloc or not same_site(netloc, base_netloc):
            continue  # same-origin already covered elsewhere; this wants a genuine subdomain hop only
        label = f"{link.get_text(' ', strip=True)} {href}".lower()
        if any(word.lower() in label for word in priority_keywords) and url not in found:
            found.append(url)
    return found


# A subdomain worth linking to from the homepage is often worth checking for
# its own sitemap too, not just fetching the one linked page. Confirmed real
# case: careers.myob.com is linked from myob.com's homepage *and* separately
# publishes its own sitemap.xml listing careers.myob.com/explore-roles -
# myob.com's own sitemap never references either. Bounded to a small number
# of distinct hosts - the nav-link scan feeding this is already small and
# curated (a handful of homepage nav/footer links), never a broad crawl.
_MAX_CROSS_SUBDOMAIN_HOSTS = 2


def _cross_subdomain_sitemap_urls(cross_subdomain_links: list[str], keywords: tuple[str, ...], limit: int, whole_word: bool, priority_keywords: tuple[str, ...], boost_terms: tuple[str, ...] = ()) -> list[str]:
    seen_hosts: set[str] = set()
    matches: list[str] = []
    for url in cross_subdomain_links:
        if len(seen_hosts) >= _MAX_CROSS_SUBDOMAIN_HOSTS:
            break
        parts = urlsplit(url)
        host_base = f"{parts.scheme}://{parts.netloc}"
        if host_base in seen_hosts:
            continue
        seen_hosts.add(host_base)
        matches.extend(discover_sitemap_pages(host_base, keywords=keywords, limit=limit, whole_word=whole_word, priority_keywords=priority_keywords, boost_terms=boost_terms))
    return matches


def evidence_urls(base_url: str, html: str, profile: ResearchProfile, boost_terms: tuple[str, ...] = ()) -> list[str]:
    """Discover a bounded set of pages relevant to the selected profile.

    Tries sitemap-based discovery first (finds the right pages without
    guessing which nav link leads where), combined with any direct
    cross-subdomain nav link the company's own homepage publishes and that
    subdomain's own sitemap if it has one (see _cross_subdomain_nav_links /
    _cross_subdomain_sitemap_urls); falls back to scanning the homepage's
    own same-origin links when none of that finds anything.
    `boost_terms` - the requester's own typed roles/interests - only affects
    which of the matched pages rank highest, never which ones are found.
    """
    sitemap_urls = discover_sitemap_pages(base_url, priority_keywords=STRONG_PATH_KEYWORDS, boost_terms=boost_terms)
    cross_subdomain = _cross_subdomain_nav_links(base_url, html, STRONG_PATH_KEYWORDS)
    cross_subdomain_sitemap = _cross_subdomain_sitemap_urls(cross_subdomain, SITEMAP_PATH_KEYWORDS, 5, False, STRONG_PATH_KEYWORDS, boost_terms)
    combined = list(dict.fromkeys((*cross_subdomain, *cross_subdomain_sitemap, *sitemap_urls)))  # the company's own direct link ranks first
    if combined:
        return combined[:5]

    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for link in soup.select("a[href]"):
        label = f"{link.get_text(' ', strip=True)} {link['href']}".lower()
        if any(word.lower() in label for word in profile.page_terms):
            url = urljoin(base_url, link["href"])
            if urlsplit(url).netloc == urlsplit(base_url).netloc and url not in urls:
                urls.append(url)
    # A small predictable fallback avoids speculative crawling.
    for path in profile.fallback_paths:
        url = urljoin(base_url, path)
        if url not in urls:
            urls.append(url)
    return urls[:5]


# Team/about/leadership page discovery, parallel to evidence_urls() but
# scoped to its own vocabulary instead of a research profile's role/
# case-study terms - contacts are worth checking regardless of which profile
# is selected, so this isn't profile-keyed the way OSM's tags now are.
_TEAM_PAGE_KEYWORDS = ("team", "about", "leadership", "people", "who-we-are", "meet-the-team")
_TEAM_PAGE_PATHS = ("/team", "/about", "/about-us", "/leadership", "/people")


def team_page_urls(base_url: str, html: str) -> list[str]:
    """Up to 2 candidate team/about/leadership page URLs: sitemap plus any
    direct cross-subdomain nav link and that subdomain's own sitemap (same
    combined approach as evidence_urls() - see _cross_subdomain_nav_links /
    _cross_subdomain_sitemap_urls), homepage-link scan as fallback. Bounded
    to 2 - this only needs one real hit, not exhaustive coverage.
    """
    sitemap_urls = discover_sitemap_pages(base_url, keywords=_TEAM_PAGE_KEYWORDS, limit=2, whole_word=True, priority_keywords=_TEAM_PAGE_KEYWORDS)
    cross_subdomain = _cross_subdomain_nav_links(base_url, html, _TEAM_PAGE_KEYWORDS)
    cross_subdomain_sitemap = _cross_subdomain_sitemap_urls(cross_subdomain, _TEAM_PAGE_KEYWORDS, 2, True, _TEAM_PAGE_KEYWORDS)
    combined = list(dict.fromkeys((*cross_subdomain, *cross_subdomain_sitemap, *sitemap_urls)))
    if combined:
        return combined[:2]

    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for link in soup.select("a[href]"):
        label = f"{link.get_text(' ', strip=True)} {link['href']}".lower()
        if any(word in label for word in _TEAM_PAGE_KEYWORDS):
            url = urljoin(base_url, link["href"])
            if urlsplit(url).netloc == urlsplit(base_url).netloc and url not in urls:
                urls.append(url)
    for path in _TEAM_PAGE_PATHS:
        url = urljoin(base_url, path)
        if url not in urls:
            urls.append(url)
    return urls[:2]


_NAME_PATTERN = re.compile(r"\b[A-Z][a-zA-Z'-]+(?:\s[A-Z][a-zA-Z'-]+){1,2}\b")


def _nearest_name(excerpt: str, title_term: str) -> str | None:
    """Best-effort candidate name closest to the matched title within a short
    excerpt. Deliberately conservative - a capitalised-word-sequence guess,
    not a claim; the raw excerpt always ships alongside it in the finding so
    a human can verify or dismiss it, same evidence discipline as
    text_excerpt() elsewhere in this module. None, not a fabricated guess,
    when nothing name-shaped is nearby.
    """
    title_index = excerpt.lower().find(title_term.lower())
    if title_index < 0:
        return None
    title_end = title_index + len(title_term)
    candidates: list[tuple[int, str]] = []
    for match in _NAME_PATTERN.finditer(excerpt):
        match_end = match.start() + len(match.group())
        # Proper interval overlap, not just "starts inside the title span":
        # a capitalised run immediately before the title (e.g. "Our Head" right
        # before "Head of Engineering") can share the title's own first word
        # without its *start* falling inside the title span - it must still
        # be excluded, or the shared word gets mistaken for part of a name.
        if match.start() < title_end and match_end > title_index:
            continue
        # A capitalised run often starts at a sentence boundary ("Meet Jane
        # Doe, our CTO..." greedily matches "Meet Jane Doe") - the last two
        # words of the run, right before the comma/title that triggered the
        # match, are the more reliable name guess than the whole run.
        words = match.group().split()
        name = " ".join(words[-2:])
        if name.lower() == title_term.lower():
            continue
        name_start = match.start() + len(match.group()) - len(name)
        candidates.append((name_start, name))
    if not candidates:
        return None
    return min(candidates, key=lambda item: abs(item[0] - title_index))[1]


def contact_finding(text: str, source_url: str, contact_titles: tuple[str, ...]) -> Finding | None:
    """A named contact only if the company's own team/about page actually
    presents one - never guessed, never fetched from a third party. Stops at
    the first contact_titles match found on the page (one contact is enough
    for a cold-outreach starting point, not an exhaustive org chart).
    """
    lower = text.lower()
    for title_term in contact_titles:
        # Word-boundary match, not a raw substring check: short acronyms like
        # "CTO"/"COO" are real substrings of common unrelated words ("Director"
        # and "doctor" both literally contain "cto") - the same false-positive
        # class matching_roles() already guards against for role terms.
        if not re.search(rf"\b{re.escape(title_term.lower())}\b", lower):
            continue
        excerpt = text_excerpt(text, title_term)
        name = _nearest_name(excerpt, title_term)
        return Finding(
            kind="team_contact_signal",
            evidence=f'"{title_term}" found on this page, near the text: "{excerpt}"',
            source_url=source_url,
            confidence="medium" if name else "low",
            suggestion=(f"Possible contact: {name} ({title_term}) - verify from the excerpt before reaching out; this is a best-effort match, not confirmed."
                        if name else f"A {title_term} is mentioned on this page, but no nearby name could be confidently identified - open the page directly to find who holds the role."),
        )
    return None


# Confirmed real false positives at count==1: a public library homepage
# scored "fashion/retail" on the single word "collection" (a library
# collection, not a clothing one); an energy company scored "fashion/retail"
# on the single word "shop". A single incidental keyword hit isn't enough
# to assert a sector as fact in a report a human is meant to trust - below
# this floor, infer_sector() reports "unknown" instead of guessing.
_SECTOR_MIN_MATCHES = 2


def infer_sector(text: str) -> str:
    lower = text.lower()
    best = "unknown"
    best_count = 0
    for sector, terms in SECTORS.items():
        count = sum(term in lower for term in terms)
        if count > best_count:
            best, best_count = sector, count
    if best_count < _SECTOR_MIN_MATCHES:
        return "unknown"
    return best


def matching_roles(text: str, role_terms: tuple[str, ...]) -> list[str]:
    """Whole-word match, plural allowed. Raw substring matching previously let
    "software engineer" match inside "software engineering" - a different
    word (a degree name, not a role) that happens to share a prefix.
    Allowing a trailing "s" keeps legitimate plurals ("engineers") matching.
    """
    return list(dict.fromkeys(
        term for term in role_terms
        if re.search(rf"\b{re.escape(term.lower())}s?\b", text.lower())
    ))


# Australia-only for now since it's the only country exercised by real runs so
# far; extend with more countries' state names as they come up, not by building a general geo database up front.
_AU_STATE_NAMES = {
    "vic": "victoria", "nsw": "new south wales", "qld": "queensland",
    "wa": "western australia", "sa": "south australia", "tas": "tasmania",
    "act": "australian capital territory", "nt": "northern territory",
}
# ccTLD fallback for when a page never spells out the country word itself
# (common - many small sites just say "Melbourne", relying on the .com.au
# domain to imply the rest). Same reasoning as above: add countries as real
# runs need them, not speculatively.
_COUNTRY_TLDS = {"australia": ("au",)}


def _state_variants(state: str) -> tuple[str, ...]:
    lower = state.strip().lower()
    variants = {lower}
    full = _AU_STATE_NAMES.get(lower)
    if full:
        variants.add(full)
    else:
        abbreviation = next((abbr for abbr, name in _AU_STATE_NAMES.items() if name == lower), None)
        if abbreviation:
            variants.add(abbreviation)
    return tuple(variants)


def _country_satisfied(lower_text: str, country: str, domain: str) -> bool:
    lower_country = country.strip().lower()
    if lower_country in lower_text:
        return True
    tlds = _COUNTRY_TLDS.get(lower_country)
    if not tlds:
        return False
    hostname = urlsplit(domain).hostname or ""
    return any(hostname.endswith(f".{tld}") for tld in tlds)


def location_is_verified(text: str, request: ResearchRequest, domain: str = "") -> bool:
    """City must appear literally; state accepts its known abbreviation or full
    name; country accepts either the literal word or a matching ccTLD on the
    fetched domain (e.g. .com.au implying Australia) as a fallback.
    """
    if not request.city.strip():
        return False
    lower = text.lower()
    if request.city.lower() not in lower:
        return False
    if not any(variant in lower for variant in _state_variants(request.state)):
        return False
    return _country_satisfied(lower, request.country, domain)


# Literally quoted from itnetworks.com.au's own homepage (deliberately not extended with synonyms like
# "managed service provider" or "IT consulting" that weren't directly quoted
# this session; confirmed-only, per the project's own evidence discipline.
_MSP_LANGUAGE_TERMS = ("managed it support", "it help desk", "virtual cio")

# Literally quoted from Culture Amp's careers page in the same session - a
# confirmed hit here overrides the MSP flag below, so an incidental MSP
# mention on an otherwise real engineering-team site doesn't get flagged.
_ENGINEERING_LANGUAGE_TERMS = ("engineering blog", "engineering team")


def _looks_like_msp(text: str) -> bool:
    lower = text.lower()
    return any(term in lower for term in _MSP_LANGUAGE_TERMS) and not any(
        term in lower for term in _ENGINEERING_LANGUAGE_TERMS
    )


def hidden_need_finding(
    result: CompanyResult,
    source_url: str,
    source_text: str,
    profile: ResearchProfile,
    contact: Finding | None = None,
) -> Finding | None:
    """A cautious "no matching role, but maybe still worth asking" finding -
    reframed by two things the pipeline already knows but previously ignored:
    whether a team_contact_signal was found for this same company (closes the
    "shouldn't it check for a CTO first?" gap), and whether the homepage's own
    language reads like an IT-services/MSP provider rather than a product
    engineering team (RESEARCH.md 2026-08-06). Deliberately never suppresses
    the finding either way - a confirmed real counter-example (HotDoc, a
    genuine target company with zero engineering language on its own careers
    page) means a hard exclusion would drop real prospects, so this only ever
    adjusts wording, never hides a company from the report.
    """
    if not source_text:
        return None
    parts = []
    if _looks_like_msp(source_text):
        parts.append(
            "This site's own language reads like an IT-services/MSP provider serving other "
            "businesses (\"managed IT support\"/\"IT help desk\"/\"virtual CIO\"-style phrasing), "
            "not a company running its own product engineering team - treat this as a weaker lead."
        )
    if contact is not None:
        parts.append(
            f"{profile.opportunity_prompt} A likely technical contact was already found on "
            "this site (see the team contact finding below) - consider directing the question to them specifically."
        )
    else:
        parts.append(
            "No evidence of a dedicated technical or leadership role was found on this site - "
            f"confirm one exists before assuming a {profile.label.lower()} need here. {profile.opportunity_prompt}"
        )
    return Finding(
        kind="potential_role_related_need",
        evidence=f"The company site contains public business information consistent with the {result.sector} sector; no matching {profile.label} role was found in scanned pages.",
        source_url=source_url,
        confidence="low",
        suggestion=" ".join(parts),
    )


def extract_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.title.get_text(strip=True) if soup.title else ""


TITLE_ARTIFACT_PATTERNS = ("(copy)", "(draft)")


def title_artifact_finding(title_text: str, source_url: str) -> Finding | None:
    """Flag leftover editorial artifacts in a homepage <title>, e.g. "(Copy)"."""
    stripped = title_text.strip()
    if not stripped:
        return None
    lower = stripped.lower()
    is_artifact = (
        any(pattern in lower for pattern in TITLE_ARTIFACT_PATTERNS)
        or lower.startswith("untitled")
        or lower.endswith("- copy")
    )
    if not is_artifact:
        return None
    return Finding(
        kind="seo_metadata_gap",
        evidence=f'Homepage <title> reads "{stripped}", an apparent editorial leftover.',
        source_url=source_url,
        confidence="high",
        suggestion="Mention this small, easily fixed detail as a low-pressure, evidence-based conversation opener; do not assume anything else about the site's maintenance.",
    )


# Plural forms deliberately, not singular "career"/"job" - a real jobs page
# URL says "/careers" or "/jobs", but "career-development" or "career-advice"
# (student/general career-info content, not a vacancy listing) would also
# match a bare "career" substring. Confirmed real false positive: an
# education-sector "students/career-development" page previously counted as
# an advertised role.
_JOB_PAGE_KEYWORDS = ("careers", "jobs", "vacancies", "hiring", "recruitment")


def _is_job_page(url: str) -> bool:
    lower = url.lower()
    return any(word in lower for word in _JOB_PAGE_KEYWORDS)


# Confirmed live this session against the two reference sites that were still reachable: 
# thesocialstudio.org (Shopify) and corporate2contract.com (Squarespace) - 
# both fingerprints are literal substrings found directly on the fetched homepage HTML, 
# no extra fetchneeded. BigCommerce's previously-confirmed reference (bettermerch.com.au)
# is now behind Cloudflare bot protection and couldn't be re-verified this
# session, so it's deliberately left out rather than shipped from a stale, unconfirmed pattern
_PLATFORM_MARKERS = (
    ("Shopify", "cdn.shopify.com"),
    ("Squarespace", "this is squarespace"),
)


def detect_platform_signal(source_url: str, html: str) -> Finding | None:
    """A templated e-commerce platform, detected from a confirmed literal
    fingerprint already present in the fetched homepage HTML - a real signal
    for whether a company is a plausible custom-dev target, not a claim
    about whether they need anything.
    """
    lower = html.lower()
    for platform, marker in _PLATFORM_MARKERS:
        if marker in lower:
            return Finding(
                kind="platform_detected",
                evidence=f'Homepage HTML contains a confirmed {platform} fingerprint ("{marker}").',
                source_url=source_url,
                confidence="high",
                suggestion=(f"Runs on {platform}, a templated platform - may indicate limited in-house "
                            "custom engineering capacity; treat this as a hypothesis to ask about, not a certainty."),
            )
    return None


def analyse_company(domain: str, request: ResearchRequest, fetch: Fetch = fetch_page, profile: ResearchProfile | None = None) -> CompanyResult:
    """Research one supplied company domain without using external discovery sources."""
    profile = profile or profile_by_id(request.profile)
    homepage = fetch(domain)
    name = urlsplit(domain).netloc
    result = CompanyResult(domain=domain, name=name)
    if not homepage.ok:
        result.limitations.append(f"Homepage not fetched: {homepage.error}")
        return result

    home_text = page_text(homepage.html or "")
    result.sector = infer_sector(home_text)
    result.location_checked = request.location_required
    if request.location_required:
        result.location_verified = location_is_verified(home_text, request, homepage.final_url or domain)
        if not result.location_verified:
            result.limitations.append("Requested city/state/country was not verified on the fetched homepage.")

    # Our own guessed fallback paths (profile.fallback_paths) must never be
    # reported as "a broken link on this site" if they 404 - that's our
    # guess being wrong, not evidence about the company. Only a URL that
    # came from the company's own sitemap or an actual <a href> on their
    # homepage (i.e. not an exact match to one of our guesses) counts.
    guessed_paths = {urljoin(homepage.final_url or domain, path) for path in profile.fallback_paths}
    broken_links: list[Finding] = []

    all_terms = tuple(dict.fromkeys((*profile.role_terms, *request.roles)))
    for url in evidence_urls(homepage.final_url or domain, homepage.html or "", profile, boost_terms=all_terms):
        page = fetch(url)
        if not page.ok:
            # A response with a successful status that merely isn't HTML
            # (e.g. an image the sitemap/homepage linked to) loaded fine -
            # it's not evidence of a dead link, just not usable as page text.
            genuinely_broken = page.status is None or page.status >= 400
            if genuinely_broken and url not in guessed_paths:
                broken_links.append(Finding(
                    kind="broken_link_signal",
                    evidence=f"A link found via this site's own sitemap or homepage failed to load: {url} ({page.error}).",
                    source_url=url,
                    confidence="low",
                    suggestion="Confirm this is a genuine dead link (not a transient fetch issue) before mentioning it - a real broken link on a public site is a small, low-pressure, evidence-based opener.",
                ))
            continue
        text = page_text(page.html or "")
        roles = matching_roles(text, all_terms)
        if roles:
            is_job_page = _is_job_page(page.final_url or url)
            result.findings.append(Finding(
                kind="advertised_role_signal" if is_job_page else "case_study_role_signal",
                evidence=text_excerpt(text, roles[0]),
                source_url=page.final_url or url,
                confidence="high" if any(role.lower() in text.lower() for role in request.roles) else "medium",
                suggestion=("Review the role scope and propose a narrowly scoped way to reduce the stated workload or delivery risk."
                            if is_job_page else "Use this work example to ask how the capability is delivered and maintained; do not assume an open role."),
            ))

    # Discovered before the hidden-need decision below (not just appended
    # after it) so hidden_need_finding() can reframe its suggestion around
    # whether a real technical contact was already found here.
    contact = None
    if profile.contact_titles:
        for url in team_page_urls(homepage.final_url or domain, homepage.html or ""):
            page = fetch(url)
            if not page.ok:
                continue
            contact = contact_finding(page_text(page.html or ""), page.final_url or url, profile.contact_titles)
            if contact:
                break

    if not result.findings:
        potential = hidden_need_finding(result, homepage.final_url or domain, home_text, profile, contact=contact)
        if potential:
            result.findings.append(potential)

    title_finding = title_artifact_finding(extract_title(homepage.html or ""), homepage.final_url or domain)
    if title_finding:
        result.findings.append(title_finding)

    if contact:
        result.findings.append(contact)

    location_ok = result.location_verified or not request.location_required
    if location_ok and result.findings and result.findings[0].confidence != "low":
        result.status = "eligible"

    # Both purely additive, after eligibility is already decided: neither a
    # broken link nor a detected platform should be able to flip a company's
    # status or push a low-confidence note into result.findings[0]'s slot.
    result.findings.extend(broken_links)
    platform_finding = detect_platform_signal(homepage.final_url or domain, homepage.html or "")
    if platform_finding:
        result.findings.append(platform_finding)
    return result


def run_research(
    request: ResearchRequest,
    domains: list[str],
    fetch: Fetch = fetch_page,
    profile: ResearchProfile | None = None,
    max_workers: int = 1,
) -> ResearchReport:
    """Research each domain's own site. Companies are researched across up to
    max_workers in parallel; each company's own fetch sequence (homepage, then
    its evidence pages) stays sequential — this parallelizes across different
    companies' sites, never bursts one target (see docs/KNOWN_GAPS.md #7).
    Default of 1 keeps the CLI's small supplied-domain-list path sequential
    and deterministic; the web app opts into a higher value.
    """
    profile = profile or profile_by_id(request.profile)
    worker = functools.partial(analyse_company, request=request, fetch=fetch, profile=profile)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        companies = list(executor.map(worker, domains))
    return ResearchReport(request=request, companies=companies)
