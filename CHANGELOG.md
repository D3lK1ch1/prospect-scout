# Changelog

Notable changes to this project. Dated, not version-numbered — nothing's
been tagged or released yet.

## 2026-08-11 — Web app narrowed to the technology profile

### Changed

- `scout/webapp.py` — removed the focus-profile dropdown from the web form;
  every web-app search now runs as the `technology` profile. `run_research_form()`
  is unchanged and still accepts any `profile_id` directly, so it stays fully
  testable against other profiles. The CLI (`python -m scout`) is unaffected
  — it still offers every profile in `scout/profiles.json`, including Custom.
- `README.md` updated to match.

### Fixed

- `tests/test_webapp.py` — replaced `test_index_lists_every_profile`
  (asserted the old, now-wrong behavior) with `test_index_is_technology_only`
  (asserts non-technology profile labels are absent from the index page).

## 2026-08-11 — ATS job-board lookup adapter (built, deliberately left unwired)

### Added

- `scout/ats_discovery.py` — public Greenhouse/Lever job-board JSON lookup
  (`find_ats_jobs()`). Verifies/enriches a company name or domain already in
  hand, the same role ABR plays for business-registration evidence — not a
  discovery adapter.
- `tests/test_ats_discovery.py` — fixture-based coverage, no live network calls.

Deliberately not wired into `run_research()`. Recorded decision
(`docs/SESSION_NOTE.md`): "deprioritized, not rejected" — Greenhouse/
Lever-hosted companies skew toward funded/scaled employers, the opposite of
the informal/small-company profile this project prioritizes.

## 2026-08-11 — Platform-detection and broken-link signals

### Added

- Platform-detection signal (`scout/research.py`, `detect_platform_signal()`)
  — flags Shopify/Squarespace via confirmed literal fingerprints in fetched
  homepage HTML (`cdn.shopify.com`, "this is Squarespace"), high confidence.
  Closes `docs/KNOWN_GAPS.md` #5. BigCommerce's previously-confirmed
  reference site is now behind Cloudflare bot protection and was left out
  rather than shipped from an unconfirmed pattern.
- Broken-link signal (`scout/research.py`) — a career/case-study/blog URL
  sourced from the company's own sitemap or homepage links that fails to
  fetch is now surfaced as low-confidence evidence. Deliberately excludes
  this project's own guessed `fallback_paths` from the check, since a wrong
  guess by this tool isn't evidence about the company's site.
- `_KIND_POINTERS` in `scout/outreach.py` gained entries for both new finding
  kinds (`broken_link_signal`, `platform_detected`).

Both signals are purely additive — neither can flip a company's eligibility
status or take the `findings[0]` slot from a real role match.

## 2026-08-11 — Research log and build-log case study

### Added

- `RESEARCH.md` — four dated research sessions: Melbourne/VIC blind-discovery
  mechanisms (AAGE, career fairs, government registers, ASX, industry
  bodies), OSM tag-mapping plus a second non-overlapping coverage-gap pass,
  MSP/IT-support noise in the technology profile (role/page-term language,
  technical-leadership signals, cold-outreach framing) with a BHP addendum,
  and a Ferocia case check (team-page absence, CTO discoverability, a
  private-event search blind spot).
- `docs/case-study.html` — a self-contained build-log/case-file page
  documenting this project's own evidence discipline: a live sample finding,
  a dated build log, three real bugs found by running the tool against real
  companies, the evidence contract, and an open ledger of known limitations.

This entry, plus the three above it, catch this file up to code and research
that was already sitting in the working tree uncommitted.

## 2026-08-07 — profile-aware discovery, team-contact extraction, evidence-grounded outreach guidance

### Added

- Profile-aware OSM discovery (`scout/osm_discovery.py`) — `_PROFILE_TAGS`
  replaces one fixed, unfiltered `office=*` query with a bounded, per-profile
  OSM tag set (e.g. `technology` → `office=it`/`office=research`/
  `office=engineer`/`shop=computer`), so the selected focus profile now
  actually changes which candidates OSM returns, not just which pages get
  scanned afterward.
- Team/leadership contact extraction (`scout/research.py`) —
  `team_page_urls()`, `contact_finding()`, `_nearest_name()` find a named
  contact (e.g. "CTO: Jane Doe") only when a company's own team/about page
  actually publishes one. New `contact_titles` field per profile in
  `scout/profiles.json`.
- Deterministic outreach guidance (`scout/outreach.py`,
  `suggest_outreach_points()`) — surfaces the specific evidence and source
  URL for one finding plus tactical framing (open with the concrete
  reference, ask one small question, never claim you can help before a
  conversation has happened); deliberately not a composed, send-ready
  message. Wired into both `scout/reporting.py`'s Markdown report and
  `scout/webapp.py`'s results page.
- Expanded `technology` profile role/page terms (`scout/profiles.json`,
  `scout/profiles.py`) — cloud engineer/AI engineer/ML engineer/systems
  engineer role terms, engineering blog/engineering team page terms.
- Sector-confidence floor (`scout/research.py`, `infer_sector()`) — requires
  at least 2 keyword hits before asserting a sector; reports "unknown"
  instead of guessing on a single incidental match.
- Contact-aware and MSP-language-aware `hidden_need_finding()`
  (`scout/research.py`) — its suggestion now reflects whether a technical
  contact was found on the same site, and flags confirmed IT-services/MSP
  language separately from genuine engineering signals, without ever
  excluding a company from the report.

### Fixed

- Word-boundary matching for sitemap-based team/leadership-page discovery
  (`scout/sitemap.py`) — fixes a confirmed false positive where the "team"
  keyword matched inside an unrelated "microsoft-teams" blog URL.

## 2026-07-31

### Added

- Web app (`scout/webapp.py`) — search by city/state/country and a focus
  profile, no domain list required; renders full results inline (evidence,
  source link, confidence, suggestion) ranked highest-priority first.
- CLI (`scout/__main__.py`) — interactive research wizard and a
  non-interactive `research`/`audit` command, working from a supplied
  domain list.
- OpenStreetMap-based company discovery (`scout/osm_discovery.py`) —
  geocodes a location via Nominatim, then queries Overpass for nearby
  offices, libraries, and research institutes with a `website` tag.
- Deterministic evidence pipeline (`scout/research.py`, `scout/sitemap.py`)
  — sitemap-first discovery of career/case-study/blog pages (falling back
  to homepage links), whole-word role matching, sector classification,
  location verification.
- Ranking (`scout/ranking.py`) — eligible before needs-review, then
  location-verified and evidence strength, with the reasons shown per
  company rather than an opaque score.
- Local Markdown/JSON reporting (`scout/reporting.py`).
- Preset and custom research profiles (`scout/profiles.json`,
  `scout/profiles.py`).
- Polite fetcher (`scout/fetcher.py`) — honours `robots.txt`, identifies
  itself honestly, times out safely, skips login-gated/disallowed content.
- Test suite (`tests/`) — fixture/fake-based, no live network calls, across
  the fetcher, OSM discovery, ranking, research, sitemap, and webapp.
- Project documentation (`docs/MVP_SPEC.md`, `docs/SEARCH_PROVIDER_SPEC.md`)
  — product boundary, evidence contract, and the OSM search-provider
  adapter design.
