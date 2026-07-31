# Changelog

Notable changes to this project. Dated, not version-numbered — nothing's
been tagged or released yet.

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
