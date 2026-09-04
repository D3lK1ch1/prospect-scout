# Changelog

Notable changes to this project. Dated, not version-numbered — nothing's
been tagged or released yet.

## 2026-09-04 — Two confirmed evidence-extraction bugs fixed

### Fixed

- `scout/research.py:page_text()` — a theme's mobile off-canvas menu built as
  `<div role="navigation">` (not a real `<nav>` element) escaped the existing
  tag-name-only chrome stripping entirely. Confirmed real leak on
  `ideabox.com.au`: the same site-wide nav text ("Artificial Intelligence
  Developers Australia", "IoT Solutions", ... "About") matched a requested
  role term on every page, producing an identical, uninformative
  `case_study_role_signal` excerpt for three different case-study URLs
  regardless of each page's real content. `page_text()` now also decomposes
  any element carrying `role="navigation"`, alongside the existing tag-name
  and skip-link-class stripping. `tests/test_research.py` gained
  `test_strips_div_based_nav_marked_only_by_aria_role`, modeled directly on
  the confirmed HTML shape.
- `scout/research.py:analyse_company()` — location verification only ever
  checked the homepage's own text so a company whose real office address sits on a `/careers` or `/locations` page instead of `/` was wrongly downgraded to `needs_review` even with the address already sitting in already-fetched HTML. `location_is_verified()` is now also checked against every evidence page and team page already fetched in
  the same run — no new network calls, no change to the literal city/state/country matching itself, just applied to more of the real evidence. The "not verified" limitation is now only recorded once every fetched page has been checked, and its wording no longer implies the homepage was the only page consulted. Two new tests in `tests/test_research.py` cover both the recovered-on-a-later-page case and the still-genuinely-unverified case.

### Changed

- `README.md` — restored three pieces of content dropped by an unrelated
  2026-08-29 commit (`b224317`): the link to `docs/MVP_SPEC.md`, the
  "provider-agnostic by design" line, and the entire "Privacy and etiquette"
  section. Also documented the `/inspect` specific-company search mode for
  the first time — it's been live in `scout/webapp.py` since the
  widespread/specific-company split shipped, but the README never mentioned
  it existed.

154/154 tests pass.

## 2026-09-03 — Crawl-delay honoured, sector-aware "technical" caveat, more specific cold-email framing

### Added

- `scout/fetcher.py` — `robots.txt`'s declared `Crawl-delay` is now read and
  honoured, not just its allow/disallow rules. Per-host "last request sent
  at" timestamps are lock-guarded (`_LAST_REQUEST_AT`, `_LAST_REQUEST_LOCK`)
  so two companies' fetches running concurrently under `run_research()`'s
  worker pool (`docs/KNOWN_GAPS.md` #7) can't both slip through the same
  host's delay window at once. `tests/test_fetcher.py` gained coverage for
  the wait itself and for concurrent callers being serialized correctly.
- `scout/research.py:hidden_need_finding()` — a `professional services`
  sector now gets its own caveat: "technical" in that sector's own job ads
  and copy usually means tax/audit/domain depth, not software skill, and the
  finding says so before treating any such mention as tech-hiring evidence.
  Confirmed live against a real Melbourne accounting firm (BG Private) —
  five of its own pages used "technical" this way — see `docs/case-study.html`,
  2026-09-02 entry.

### Changed

- `scout/outreach.py:suggest_outreach_points()` — the `potential_role_related_need`
  branch no longer gives the same framing regardless of whether a contact
  was found. With a contact, it still repeats `hidden_need_finding()`'s
  contact-aware suggestion plus a "keep this tentative" note. With no
  contact and no confirmed role, it now gives real cold-email guidance
  instead of repeating the report's own "Suggested next step" verbatim: a
  small ask (a short call or coffee chat) rather than a job ask, sourced
  from cold email tips.
- `docs/MVP_SPEC.md` — restructured as the canonical boundary/constraints
  document (a `## Constraints` section now states the rules that apply
  everywhere, not just to the MVP boundary; `## Current status` replaces the
  old, now-stale `## Delivery slices` list and points to `CHANGELOG.md` for
  full history instead of duplicating it). `scout/osm_discovery.py`'s
  `_PROFILE_TAGS` comment corrected to stop citing `RESEARCH.md`'s
  2026-08-04 section for tag choices that were actually added later.

151/151 tests pass (with `protego` installed — see `requirements.txt`).

## 2026-08-29 — Broken-link false positive fixed, Overpass batched, technology tag list corrected

### Fixed

- `scout/research.py:analyse_company()` — `broken_link_signal` was firing on
  any non-HTML fetch response, even a successful one (e.g. a sitemap-linked
  image). Confirmed false positive on two real domains (`accelit.com.au`,
  `ideabox.com.au`) — both loaded fine (200), they just weren't HTML. Now
  only a genuine failure (no response, or a 4xx/5xx status) counts as
  broken. Added `test_a_sitemap_sourced_image_link_is_not_flagged_as_broken`
  to `tests/test_research.py`, modeled directly on the confirmed case.

### Changed

- `scout/osm_discovery.py:query_overpass()` — batches the technology
  profile's tag pairs into groups of `_BATCH_SIZE = 4` per Overpass request
  instead of one request per tag pair, cutting a technology-profile search
  from 16 sequential requests to 4. Batch size is live-trial-confirmed
  against the exact Melbourne bbox the code resolves (2026-08-29): 4 tag
  pairs (16 clauses) succeeded in 14.7s with real margin; 8 succeeded but at
  24.0s, too close to the 25s timeout to trust; 12 and 16 both timed out
  outright, consistent with the original 68-clause failure (2026-08-21).
  This is the confirmed middle ground between that single-mega-query
  failure and the one-request-per-tag-pair fix that followed it —
  parallelizing the requests instead was considered and rejected
  (`docs/SESSION_NOTE_1.md`, 2026-08-21: Overpass's shared public instance
  is the real constraint, not round-trip count).
- `scout/osm_discovery.py:_PROFILE_TAGS["technology"]` — dropped
  `office=software` (confirmed 0 Melbourne hits per RESEARCH.md; a company
  already self-tagged as software doesn't need the profile's separate
  "hidden tech team inside a generic office" goal either). Corrected the
  module comment, which wrongly attributed all 15 tags to one RESEARCH.md
  session — only 4 are from there; the other 10 generic office tags
  (`company`, `financial`, `consulting`, etc.) were added later for the
  BHP/Case-03 reasoning documented in `docs/case-study.html`, and that
  reasoning wasn't written down anywhere in code until now.
- `tests/test_osm_discovery.py` updated for the new batch counts and tag
  list; the "one failing request doesn't blank the others" test now
  exercises the technology profile's multiple batches instead of the
  3-tag-pair fallback set, which no longer produces more than one batch.

144/144 tests pass.

## 2026-08-21 — Sitemap discovery now uses Protego's `.sitemaps` property

### Changed

- `scout/sitemap.py:_sitemaps_from_robots()` — replaced hand-rolled
  `Sitemap:` line parsing with Protego's `.sitemaps` property, the same
  RFC 9309-compliant parser `scout/fetcher.py:robots_allows()` already uses
  for the Protego swap (`docs/SESSION_NOTE_1.md`, 2026-08-13), instead of a
  second, separately-maintained parse of the same `robots.txt` file.
  Behavior preserved exactly: empty list when no sitemap is declared,
  order-preserving dedup on the returned URLs. All 142 tests pass unchanged.

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
