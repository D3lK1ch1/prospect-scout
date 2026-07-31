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


def infer_sector(text: str) -> str:
    lower = text.lower()
    best = "unknown"
    best_count = 0
    for sector, terms in SECTORS.items():
        count = sum(term in lower for term in terms)
        if count > best_count:
            best, best_count = sector, count
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


def hidden_need_finding(result: CompanyResult, source_url: str, source_text: str, profile: ResearchProfile) -> Finding | None:
    if not source_text:
        return None
    return Finding(
        kind="potential_role_related_need",
        evidence=f"The company site contains public business information consistent with the {result.sector} sector; no matching {profile.label} role was found in scanned pages.",
        source_url=source_url,
        confidence="low",
        suggestion=profile.opportunity_prompt,
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

    if not result.findings:
        potential = hidden_need_finding(result, homepage.final_url or domain, home_text, profile)
        if potential:
            result.findings.append(potential)

    title_finding = title_artifact_finding(extract_title(homepage.html or ""), homepage.final_url or domain)
    if title_finding:
        result.findings.append(title_finding)

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
