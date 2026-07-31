# Search-provider adapter — spec draft (slice 5)

Status: **shipped 2026-07-26.** All four build-order units below are built
(`scout/osm_discovery.py`, wired into `run_research()`, `scout/webapp.py`,
and the results-page polish). All three open questions are answered. Kept
here as the historical record and as the adapter-shape template that
`docs/ABR_DISCOVERY_SPEC.md` copies for the second provider — not a live
spec to keep re-reading.

Provider decision (2026-07-19): **OpenStreetMap Overpass API**, geocoded via
Nominatim. Free, no signup/API key, deterministic (not AI), and it satisfies
the session-note constraint of not requiring a paid or hardcoded provider.
`SEARCH_PROVIDER=osm|none`, default `none` (today's domain-list behaviour
unchanged unless osm is explicitly selected).

## Purpose

Add automatic city-wide company discovery via a configured, terms-compliant
search provider, as an *addition to* — not a replacement for — the existing
domain-list adapter (`scout/research.py: read_domains`).

## Constraints (from docs/SESSION_NOTE.md)

- No hardcoded provider; provider is chosen via env/config, not code.
- No credentials in the repo (`.env` only; already gitignored).
- A no-search / local-only mode must keep working with zero config: today's
  domain-list workflow is the fallback, always available.
- This spec covers discovery only, not summarization. If an AI summary step
  is added later, it must send only minimal cited evidence, never full pages.

## What it does

- A new adapter behind the same output contract as `read_domains()`: given a
  location + interest, returns a list of normalised domains matching the
  evidence contract in `docs/MVP_SPEC.md`.
- Provider selection via an env var, e.g. `SEARCH_PROVIDER=none|<name>`,
  defaulting to `none`.
- Each provider integration is a thin adapter: normalised query params in,
  normalised domain list out. No provider-specific logic leaks into
  `research.py`.
- Provider errors, missing config, or rate limits become report
  `limitations`, not crashes — matching the existing fetcher error pattern.

## What it does not do

- Does not call any AI/LLM model.
- Does not bypass `robots.txt` or a provider's terms of service.
- Does not replace the domain-list adapter; both coexist, domain-list stays
  default when no provider is configured.
- Does not persist provider responses beyond the run unless a later caching
  slice adds it.

## Done looks like

- `SEARCH_PROVIDER` unset or `none`: behavior is identical to today; existing
  13 tests keep passing unmodified.
- A fixture-based fake provider has full unit test coverage before any live
  provider is wired in (fixture-first, per `docs/MVP_SPEC.md`'s closing line).
- README documents how to configure a real provider later, without one being
  required to use the tool.

## Open questions for Delia

1. ~~Which real provider first?~~ Answered: OpenStreetMap Overpass, see above.
2. ~~Provider selection: `.env`, CLI flag, or both?~~ Answered by what
   actually got built: `.env`/env var only, surfaced through the web app's
   form. The CLI intentionally stayed domain-list-only (README: "The command
   line always needs a supplied domain list; only the web app discovers
   companies on its own") — not an oversight, a kept boundary.
3. ~~Fixture-first?~~ Answered: yes — Unit 1 below is fixture-tested only,
   no live Overpass/Nominatim calls in the test suite (matches the etiquette
   acceptance test: "tests make no live external requests").

## OSM adapter mechanics

1. Geocode `city, state, country` via Nominatim to a bounding area.
2. Query Overpass for business nodes/ways in that area matching the
   profile's relevant categories, requesting the `website` /
   `contact:website` tag.
3. Keep only results that have a website tag — no tag, no domain, nothing
   to fetch. That's a `limitations` entry, not a fabricated candidate.
4. Normalise surviving results through the same `normalise_domain()` used
   for the manual domain-list path, so both inputs converge on one shape.
5. Respect Overpass/Nominatim usage policy: identifying user agent, rate
   limiting, no bulk re-querying — same politeness ethos as the existing
   `robots.txt`-honouring fetcher.

## Build order (proposed, one unit at a time)

- **Unit 1 — OSM adapter, backend only.** Pure functions: geocode + Overpass
  query + parse-to-domain-list. Fixture-based unit tests (recorded
  Overpass/Nominatim JSON responses as fixtures), no live network calls in
  tests, no UI, no CLI wiring yet. This is the part that actually answers
  "can it search all over" — worth proving on its own before any UI exists.
- **Unit 2 — wire into `run_research()`.** When no domain file is given,
  use the OSM adapter instead. Testable non-interactively first
  (`--search-city-wide` or similar), still no web UI.
- **Unit 3 — local web page.** A small local server (FastAPI) with one form
  (location, profile, role/interest, domain-list-vs-search-all-over toggle)
  and one results page, calling the exact same functions from Units 1–2 —
  no new business logic, just an HTTP wrapper around what already works.
- **Unit 4 — polish.** Profile dropdown sourced from `scout/profiles.json`,
  styling, download links for the JSON/MD report. Lowest priority.

CLI stays as-is; the web page is an additional entry point, not a
replacement, since the core logic is already decoupled from `__main__.py`.
