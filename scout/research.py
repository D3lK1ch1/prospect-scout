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
from scout.sitemap import discover_sitemap_pages

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


def text_excerpt(text: str, phrase: str, width: int = 180) -> str:
    index = text.lower().find(phrase.lower())
    if index < 0:
        return text[:width].strip()
    return text[max(0, index - 60): index + len(phrase) + 100].strip()


_CHROME_TAGS = ("nav", "header", "footer", "script", "style", "noscript")


def page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_CHROME_TAGS):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def evidence_urls(base_url: str, html: str, profile: ResearchProfile) -> list[str]:
    """Discover a bounded set of pages relevant to the selected profile.

    Tries sitemap-based discovery first (finds the right pages without
    guessing which nav link leads where); falls back to scanning the
    homepage's own links when no sitemap exists or nothing matched.
    """
    sitemap_urls = discover_sitemap_pages(base_url)
    if sitemap_urls:
        return sitemap_urls[:5]

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
    """Up to 2 candidate team/about/leadership page URLs: sitemap first (same
    mechanism discover_sitemap_pages() already uses for career/case-study
    pages, just with team-page keywords), homepage-link scan as fallback.
    Bounded to 2 - this only needs one real hit, not exhaustive coverage.
    """
    sitemap_urls = discover_sitemap_pages(base_url, keywords=_TEAM_PAGE_KEYWORDS, limit=2, whole_word=True)
    if sitemap_urls:
        return sitemap_urls

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
# far; extend with more countries' state names as they come up (see
# docs/KNOWN_GAPS.md), not by building a general geo database up front.
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
    lower = text.lower()
    if request.city.lower() not in lower:
        return False
    if not any(variant in lower for variant in _state_variants(request.state)):
        return False
    return _country_satisfied(lower, request.country, domain)


# Literally quoted from itnetworks.com.au's own homepage (RESEARCH.md
# 2026-08-06, finding (a)(2)) - deliberately not extended with synonyms like
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
# an advertised role. See docs/KNOWN_GAPS.md.
_JOB_PAGE_KEYWORDS = ("careers", "jobs", "vacancies", "hiring", "recruitment")


def _is_job_page(url: str) -> bool:
    lower = url.lower()
    return any(word in lower for word in _JOB_PAGE_KEYWORDS)


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
    result.location_verified = location_is_verified(home_text, request, homepage.final_url or domain)
    if not result.location_verified:
        result.limitations.append("Requested city/state/country was not verified on the fetched homepage.")

    all_terms = tuple(dict.fromkeys((*profile.role_terms, *request.roles)))
    for url in evidence_urls(homepage.final_url or domain, homepage.html or "", profile):
        page = fetch(url)
        if not page.ok:
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

    if result.location_verified and result.findings and result.findings[0].confidence != "low":
        result.status = "eligible"
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
