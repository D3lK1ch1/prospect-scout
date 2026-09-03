# Prospect Scout MVP specification

Canonical for current boundary, constraints, and status. If another doc
(README, a session note, a draft spec) appears to say something different
about what's currently true or allowed, this file wins.

## Purpose

Given a location and technical interests, identify publicly visible companies that are plausible prospects, collect **verifiable** case-study and technical-need signals, and create a local report for human review before outreach.

This is a research aid, not a lead-generation guarantee: a search can return zero eligible companies when public sources lack evidence. It must never invent a company, case study, problem, or contact detail.

## Constraints

These apply everywhere in the project, not just to the MVP boundary below.

- **No hardcoded or paid search/AI provider.** Every external provider (search, AI) is opt-in via configuration, never required, never embedded with credentials. A no-provider/local-only mode always works with zero config.
- **No AI-drafted outreach, no AI-generated claims without source evidence.** Every finding is deterministic and traceable to a specific fetched page. Reports surface evidence and a suggested next step; the appeal itself is written by the human, by hand.
- **`robots.txt` is honoured, never bypassed.** No access-control bypass, no login walls, no CAPTCHA defeat. Sites that block the fetcher are recorded as unreachable, not worked around.
- **No bulk/background crawling, no contact discovery, no CRM integration, no automatic outreach.** The tool never emails or contacts a company; it produces a local report for human review.
- **Runs locally, shares nothing.** No telemetry, no remote storage of results.

## MVP boundary

The first releasable version accepts all three location fields and at least one technical-interest phrase:

```text
scout research --city Melbourne --state VIC --country Australia \
  --interest "website performance" --interest accessibility \
  --limit 20 --output reports/melbourne.md
```

It produces one Markdown report and a machine-readable JSON companion. Each candidate is `eligible`, `needs_review`, or `rejected`; only eligible candidates appear in recommendations. Running `python -m scout` launches the same workflow interactively.

### Included

1. Discover candidates from a configured, legitimate search provider or imported company-domain list. Providers are adapters: no credentials or terms are embedded in the app.
2. Verify location from a company-controlled page or trustworthy public business/profile page, retaining URL and excerpt.
3. Fetch only public pages; honour `robots.txt`, use an honest user agent, time out safely, and skip login walls and disallowed content.
4. Find company-owned case studies/customer stories, project pages, careers, engineering/blog, and relevant site pages.
5. Extract deterministic, evidence-backed signals: page performance/loading clues, accessibility-markup gaps, broken links or error responses, SEO/indexing metadata gaps, outdated web-stack clues, and interest-matching case-study/job language.
6. Rank evidence and suggest a conservative next step. Suggestions name supporting evidence and frame unproven root causes as hypotheses.
7. Save results locally. Never email, submit forms, enrich personal data, or bypass access controls.

### Excluded from the MVP

- Job-board/LinkedIn scraping specifically
- A guarantee of finding every company in an area.

(See Constraints above for the rules that apply everywhere, not just to this boundary.)

## Data and evidence contract

Every finding in JSON and Markdown contains:

| Field | Requirement |
| --- | --- |
| `company_name`, `domain` | Identified from the company site or source record. |
| `location` | City, state/region, country, source URL, and excerpt. |
| `source_url` | Canonical URL after redirects. |
| `retrieved_at` | ISO-8601 UTC timestamp. |
| `signal_type` | A defined deterministic signal family. |
| `evidence` | Short observed text, response fact, or measurement. |
| `confidence` | `high`, `medium`, or `low`, with a reason. |
| `suggestion` | Specific next action tied to evidence. |

If a page cannot be fetched or has too little evidence, record that limitation instead of making a finding. Deduplicate domains while retaining all contributing URLs.

## Eligibility and ranking

`eligible` requires a verified location match, at least one company-controlled or trustworthy source, and one relevant citeable signal. `needs_review` means location or relevance is ambiguous. Reject location mismatches, no-evidence cases, robots denials, and duplicates.

Rank eligible companies by location confidence, requested-interest relevance, evidence strength, and dated-signal recency. Display component reasons rather than an opaque score.

## Architecture

```text
CLI wizard / non-interactive command      Web app (city/state/country + role terms)
        -> ResearchRequest (location + target roles)
        -> selected preset/custom research profile
        -> discovery adapter:
             - supplied domain-list adapter (CLI, always available)
             - OpenStreetMap adapter (web app; Nominatim geocode + Overpass candidates)
        -> polite fetcher -> homepage + up to five career URLs
             (sitemap-first discovery, homepage-link fallback)
        -> deterministic scanner (role, sector, location evidence)
        -> eligibility decision + cautious opportunity hypothesis
        -> local Markdown + JSON report
```

Profiles are data, stored in `scout/profiles.json`, not scanner branches. A profile defines role terms, relevant page labels/URL words, bounded fallback paths, and a cautious opportunity prompt. The Custom UI option creates the same profile shape for one run. A future search-provider adapter may add candidates, but must yield the same normalised domain records and may not bypass provider terms or robots controls.

## Current status

Full dated history of what's shipped lives in [CHANGELOG.md](../CHANGELOG.md) — this section states the current boundary only, not a second change log.

- Foundation, research model, domain-list adapter, and career-first evidence scanning: shipped and tested.
- OpenStreetMap discovery adapter (city/state/country -> candidate companies, no domain list needed): shipped, wired into both the CLI and the web app.
- Second discovery adapter (Australian Business Register): drafted, deliberately paused
- AI-drafted outreach button: drafted, not started - for potential agentification for more specific details per company
- Public web-app deployment: out of scope until the SSRF-allowlist and rate-limiting gaps 

## Acceptance test plan

| Area | Must prove |
| --- | --- |
| Foundation | Invalid URLs fail clearly; robots denial prevents a page fetch; HTTP/non-HTML failures retain diagnostics; valid HTML reaches CLI output. |
| Research model | City, state, country, and one or more interests are required; invalid limits/outputs fail before network work. |
| Source adapters | Fixture candidates map to a stable schema; duplicate domains collapse; provider errors become report limitations. |
| Evidence | A fixture cannot become eligible without location citation and relevant evidence; every finding has URL, time, evidence, confidence, and suggestion. |
| Reporting | JSON and Markdown contain identical findings; zero eligible results is a valid report with an explicit limitation; paths stay local. |
| Etiquette | Disallowed, login-gated, and unsupported pages are skipped and reported; tests make no live external requests. |

The executable tests cover the Foundation, local research model, domain-list adapter, career-first evidence, and report writer. The remaining provider and case-study work should be built fixture-first before live-provider integration.
