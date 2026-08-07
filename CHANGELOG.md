# Changelog

Notable changes to this project. Dated, not version-numbered — nothing's
been tagged or released yet.

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
