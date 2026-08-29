# Prospect Scout

A private, locally run research aid for finding evidence-backed company prospects and suggesting technical work worth investigating. Results stay local and the tool never contacts companies.

**Current status:** two ways to run a search.

- **Web app** (`scout/webapp.py`) — give it a city, state, country, and optional role terms; it finds candidate companies itself via OpenStreetMap (geocode the location, then query nearby offices, libraries, and research institutes), scans each one's own sitemap for case-study/blog/career pages (falling back to scanning the homepage's own links if no sitemap exists), and shows full results in the browser — every finding's evidence, source link, confidence, and suggestion, ranked highest-priority first with the reasons why. No domain list required. Scoped to the technology profile only, to keep this entry point focused. Researches up to 250 candidates per run, across companies concurrently (each individual company is still fetched politely/sequentially) so a wide run finishes in minutes rather than tens of minutes.
- **Command line** (`python -m scout`) — the original workflow: still offers every profile in `scout/profiles.json` (including a custom one), but you supply a text file of company domains yourself rather than the tool discovering them.

Both paths share the same scanning logic: career-page role matching, sector-aware cautious hidden-need ideas where no matching role is found, and a deterministic check for leftover editorial artifacts in a homepage's `<title>` tag (e.g. `"(Copy)"`, `"Untitled"`). Both write local Markdown/JSON reports.

**Known limitation, worth reading before relying on the web app's search:** OpenStreetMap doesn't have every company mapped — it's confirmed to miss real, currently-hiring businesses, including some large ones. It's a genuinely useful *widening* of what you'd find by hand, not a guarantee of completeness. A second discovery adapter (Australian Business Register data) is in design to help close that gap.

## Setup

From the `prospect-scout` folder:

```bash
python -m venv .venv
source .venv/Scripts/activate   # Git Bash on Windows
# or: .venv\Scripts\activate    # PowerShell / cmd
pip install -r requirements.txt
```

## Usage

### Web app (search by location, no domain list needed)

```bash
uvicorn scout.webapp:app --reload
```

Then open `http://127.0.0.1:8000/` in a browser. Fill in city, state, country, and optional role terms, and submit — the technology profile is applied automatically. It geocodes the location, finds nearby candidates, researches up to 250 of them, and shows a results page ranked highest-priority first (eligible before needs-review, location-verified, evidence strength — see "Why ranked here" on each company), with every finding's evidence, source link, confidence, and suggestion shown inline — plus a link to the saved Markdown/JSON report if you want the raw file. Each company involves several polite, sequential fetches to that company's own site; companies are researched several at a time (not one after another) to keep a wide run's total time reasonable.

If no businesses turn up for a location, you'll get a plain message back, not an error — that's an honest "nothing found," not a crash.

### Command line

```bash
python -m scout audit https://example.com
```

For the research wizard, run:

```bash
python -m scout
```

It will prompt for your location, a focus profile, optional specific roles/interests, and a text file of company domains (one per line). Preset profiles cover technology, marketing, retail/operations, and business/administration. Choose **Custom** to provide your own role terms, relevant page labels/URL words, and cautious opportunity prompt—no Python edit required. For example:

```text
https://company-one.example
company-two.example
```

The non-interactive equivalent is:

```bash
python -m scout research --city Melbourne --state VIC --country Australia \
  --role "web developer" --role "technical support" \
  --domains candidates.txt --output reports/melbourne.md
```

The command line always needs a supplied domain list; only the web app discovers companies on its own.

### How company pages get scanned

The selected profile, rather than a hardcoded sector rule, controls which company pages and role terms are scanned. For each company, the tool first checks whether it publishes a sitemap (declared in `robots.txt`, or guessed at common paths) and, if so, uses it to find case-study, blog, and career pages directly — no sitemap is treated as neutral, not a red flag, since plenty of legitimate small or hand-built sites never add one. If no sitemap exists or nothing relevant is found there, it falls back to scanning the links on the company's own homepage.

A matching role on a careers/vacancies page is an active signal; a matching term in a case study is evidence of relevant work, not proof of an open vacancy. Opportunity hypotheses remain low confidence until a human validates them. Nothing in this pipeline calls an AI model — every finding is deterministic and traceable to a specific fetched page.

Profiles live in [scout/profiles.json](scout/profiles.json). You can add or amend preset profiles there, or use Custom for a one-off run.

## Tests

The current offline tests require no additional framework and make no live network calls:

```bash
python -m unittest discover -s tests -v
```
