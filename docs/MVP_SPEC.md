# Prospect Scout MVP specification

## Purpose

Given a location and technical interests, identify publicly visible companies that are plausible prospects, collect **verifiable** case-study and technical-need signals, and create a local report for human review before outreach.

This is a research aid, not a lead-generation guarantee: a search can return zero eligible companies when public sources lack evidence. It must never invent a company, case study, problem, or contact detail.

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

- Job-board/LinkedIn scraping, authenticated sources, or robots-disallowed sites.
- Automatic outreach, contact discovery, CRM integration, bulk/background crawling, or AI-generated claims without source evidence.
- A guarantee of finding every company in an area.

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
CLI wizard / non-interactive command
        -> ResearchRequest (location + target roles)
        -> selected preset/custom research profile
        -> supplied domain-list adapter
        -> polite fetcher -> homepage + up to five career URLs
        -> deterministic scanner (role, sector, location evidence)
        -> eligibility decision + cautious opportunity hypothesis
        -> local Markdown + JSON report
```

Profiles are data, stored in `scout/profiles.json`, not scanner branches. A profile defines role terms, relevant page labels/URL words, bounded fallback paths, and a cautious opportunity prompt. The Custom UI option creates the same profile shape for one run. The domain-list adapter is intentionally the only discovery adapter today. A future search-provider adapter may add candidates, but must yield the same normalised domain records and may not bypass provider terms or robots controls.

## Delivery slices

1. **Foundation (complete):** URL validation and polite single-page HTML fetcher.
2. **Research model (complete):** typed records, local JSON/Markdown writer, fixtures, and interactive/non-interactive CLI validation.
3. **Domain-list input adapter (complete):** one URL/domain per line, normalisation, and deduplication.
4. **Career-first evidence (complete):** bounded careers-page discovery, technology case-study scanning, deterministic role matching, location check, sector classification, and eligibility decisions.
5. **Next:** terms-compliant location-aware search-provider adapter, stronger page diagnostics, case-study-specific signals, and ranking across verified candidates.

Each slice must be usable independently and must not turn lack of data into a positive result.

## Acceptance test plan

| Slice | Must prove |
| --- | --- |
| Foundation | Invalid URLs fail clearly; robots denial prevents a page fetch; HTTP/non-HTML failures retain diagnostics; valid HTML reaches CLI output. |
| Research model | City, state, country, and one or more interests are required; invalid limits/outputs fail before network work. |
| Source adapters | Fixture candidates map to a stable schema; duplicate domains collapse; provider errors become report limitations. |
| Evidence | A fixture cannot become eligible without location citation and relevant evidence; every finding has URL, time, evidence, confidence, and suggestion. |
| Reporting | JSON and Markdown contain identical findings; zero eligible results is a valid report with an explicit limitation; paths stay local. |
| Etiquette | Disallowed, login-gated, and unsupported pages are skipped and reported; tests make no live external requests. |

The executable tests cover the Foundation, local research model, domain-list adapter, career-first evidence, and report writer. The remaining provider and case-study work should be built fixture-first before live-provider integration.
