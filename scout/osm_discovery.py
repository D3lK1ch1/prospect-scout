"""OSM-based city-wide company discovery: Nominatim geocoding + Overpass business search.

An addition to, not a replacement for, the domain-list adapter in research.py —
both produce the same normalised-domain-list shape; this one starts from a
location instead of a supplied file. See docs/SEARCH_PROVIDER_SPEC.md.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from scout.research import normalise_domain

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Identify honestly, matching fetcher.py's USER_AGENT convention.
USER_AGENT = "ProspectScout/0.1 (local personal-use company discovery; run by hand)"
TIMEOUT_SECONDS = 25.0

# Nominatim's usage policy caps automated use at one request per second;
# Overpass has no published limit this strict, but the same pacing is safe
# for both and keeps this module's politeness story simple.
_MIN_REQUEST_INTERVAL_SECONDS = 1.1
_last_request_at = 0.0


@dataclass(frozen=True)
class BoundingBox:
    south: float
    west: float
    north: float
    east: float


def _throttle() -> None:
    global _last_request_at
    wait = _last_request_at + _MIN_REQUEST_INTERVAL_SECONDS - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def geocode_area(city: str, state: str, country: str) -> BoundingBox | None:
    """Resolve a location to a bounding box via Nominatim. None on no match or error."""
    _throttle()
    try:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS) as client:
            response = client.get(NOMINATIM_URL, params={"q": f"{city}, {state}, {country}", "format": "json", "limit": 1})
    except httpx.HTTPError:
        return None

    if response.status_code >= 400:
        return None

    results = response.json()
    if not results:
        return None

    try:
        south, north, west, east = results[0]["boundingbox"]
        return BoundingBox(south=float(south), west=float(west), north=float(north), east=float(east))
    except (KeyError, TypeError, ValueError):
        return None


# Per-profile OSM tag narrowing, confirmed against real OSM tag documentation
# and live Melbourne Overpass counts (see RESEARCH.md, 2026-08-04 section) -
# each tuple replaces the old one-size-fits-all `office=*` wildcard for that
# profile with a bounded set of tag values that actually fit its role terms.
# Ruled out during that research and deliberately not included: `advertising=*`
# (tags billboards/signage, not agencies), `craft=*` (dominated by solo
# tradespeople), `office=government` (a judgment call against the project's
# stated preference for informal/small companies over large/formal ones).
_PROFILE_TAGS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "technology": (
        ("office", "it"),
        ("office", "research"),
        ("office", "engineer"),
        ("shop", "computer"),
        ("office", "company"),
        ("office", "financial"),
        ("office", "consulting"),
        ("office", "association"),
        ("office", "insurance"),
        ("office", "telecommunication"),
        ("office", "software"),
        ("office", "law"),
        ("office", "accountant"),
        ("office", "architect"),
        ("office", "design"),
    )
}
# amenity=university deliberately excluded: real hits too, but sitemaps too large for this pipeline.
_UNIVERSAL_TAGS: tuple[tuple[str, str | None], ...] = (
    ("amenity", "library"),
    ("amenity", "research_institute"),
)
# No profile, or a profile id this module doesn't recognise: today's original
# behaviour, an unfiltered `office=*` wildcard plus the two amenities -
# nothing regresses for a caller that doesn't pass a profile id.
_FALLBACK_TAGS: tuple[tuple[str, str | None], ...] = (("office", None),) + _UNIVERSAL_TAGS


def _candidate_tags(profile_id: str | None) -> tuple[tuple[str, str | None], ...]:
    if profile_id is None or profile_id not in _PROFILE_TAGS:
        return _FALLBACK_TAGS
    return _PROFILE_TAGS[profile_id] + _UNIVERSAL_TAGS


def query_overpass(bbox: BoundingBox, profile_id: str | None = None) -> list[dict]:
    """Query Overpass for businesses matching the profile's OSM tags (or the
    unfiltered office=* fallback when no profile is given) with a website tag
    inside bbox. [] on error, one query per tag pair.
    """
    box = f"{bbox.south},{bbox.west},{bbox.north},{bbox.east}"
    elements: list[dict] = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS) as client:
        for key, value in _candidate_tags(profile_id):
            tag_filter = f'["{key}"]' if value is None else f'["{key}"="{value}"]'
            clauses = [
                f'{element_type}{tag_filter}["{website_tag}"]({box});'
                for element_type in ("node", "way")
                for website_tag in ("website", "contact:website")
            ]
            query = f"[out:json][timeout:25];({''.join(clauses)});out center;"
            _throttle()
            try:
                response = client.post(OVERPASS_URL, content=query, headers={"Content-Type": "text/plain"})
            except httpx.HTTPError:
                continue
            if response.status_code >= 400:
                continue
            elements.extend(response.json().get("elements", []))
    return elements


def parse_to_domains(elements: list[dict]) -> list[str]:
    """Extract normalised, deduplicated domains from Overpass elements' website tags."""
    found: list[str] = []
    seen: set[str] = set()
    for element in elements:
        tags = element.get("tags", {})
        website = tags.get("website") or tags.get("contact:website")
        if not website:
            continue
        domain = normalise_domain(website)
        if domain and domain not in seen:
            seen.add(domain)
            found.append(domain)
    return found


def discover_domains(city: str, state: str, country: str, profile_id: str | None = None) -> list[str]:
    """Same output contract as research.read_domains(): a normalised domain list, from a location instead of a file."""
    bbox = geocode_area(city, state, country)
    if bbox is None:
        return []
    return parse_to_domains(query_overpass(bbox, profile_id))
