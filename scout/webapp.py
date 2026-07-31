"""Local web UI: location-driven research, as one form and one results page.

Wraps run_research()/write_report() exactly as __main__.py's cmd_research() does;
no new business logic lives here. Candidate companies come from the OSM adapter
(discover_domains) rather than a supplied domain-list file — city/state/country
are enough to run a search.
"""

from __future__ import annotations

import html
from collections.abc import Callable

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse

from scout.fetcher import FetchResult, fetch_page
from scout.models import CompanyResult, Finding, ResearchRequest
from scout.osm_discovery import discover_domains
from scout.profiles import load_profiles, profile_by_id
from scout.ranking import rank_companies, rank_reason
from scout.reporting import write_report
from scout.research import run_research

Fetch = Callable[[str], FetchResult]
Discover = Callable[[str, str, str], list[str]]

# OSM discovery can return well over this many candidates in a dense city;
# researching all of them still fetches each company's own site sequentially,
# so this is also the practical cap on one run's total request volume.
DEFAULT_LIMIT = 250
# Companies are researched across this many in parallel (each company's own
# fetches stay sequential/polite) so a limit of 250 finishes in minutes, not
# tens of minutes. See scout/research.py:run_research and docs/KNOWN_GAPS.md #7.
WEB_CONCURRENCY = 10

app = FastAPI(title="Prospect Scout")

_STYLE = """
body { font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }
.company { border: 1px solid #ccc; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
.status-eligible { color: #0a7d2c; }
.status-needs_review { color: #8a6d00; }
.status-rejected { color: #b00020; }
.rank-reason { color: #555; font-size: 0.9rem; }
.finding { border-left: 3px solid #ddd; padding-left: 0.75rem; margin: 0.75rem 0; }
.finding p { margin: 0.25rem 0; }
.limitations { color: #8a6d00; font-size: 0.9rem; }
"""


def render_form(error: str | None = None) -> str:
    options = "\n".join(f'<option value="{html.escape(p.id)}">{html.escape(p.label)}</option>' for p in load_profiles())
    error_html = f'<p style="color:#b00020">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html>
<head><title>Prospect Scout</title><style>{_STYLE}</style></head>
<body>
<h1>Prospect Scout research setup</h1>
{error_html}
<form method="post" action="/research">
  <p><label>City <input name="city" required></label></p>
  <p><label>State/region <input name="state" required></label></p>
  <p><label>Country <input name="country" required></label></p>
  <p><label>Focus profile <select name="profile">{options}</select></label></p>
  <p><label>Roles/interests (comma-separated; blank uses the focus's own terms)
    <input name="roles"></label></p>
  <p><button type="submit">Run research</button></p>
</form>
</body>
</html>"""


def _render_finding(finding: Finding) -> str:
    url = html.escape(finding.source_url)
    return f"""<div class="finding">
      <p><strong>{html.escape(finding.kind.replace('_', ' ').title())}</strong> &middot; confidence: {html.escape(finding.confidence)}</p>
      <p>{html.escape(finding.evidence)}</p>
      <p><a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a></p>
      <p><em>Suggested next step:</em> {html.escape(finding.suggestion)}</p>
    </div>"""


def _render_company_card(company: CompanyResult) -> str:
    findings_html = "\n".join(_render_finding(finding) for finding in company.findings) or "<p><em>No findings.</em></p>"
    limitations_html = "".join(f"<li>{html.escape(limitation)}</li>" for limitation in company.limitations)
    limitations_block = f'<ul class="limitations">{limitations_html}</ul>' if limitations_html else ""
    return f"""<section class="company">
  <h2>{html.escape(company.name)} <span class="status-{html.escape(company.status)}">{html.escape(company.status)}</span></h2>
  <p>Sector: {html.escape(company.sector)} &middot; Location verified: {"yes" if company.location_verified else "no"}</p>
  <p class="rank-reason">Why ranked here: {html.escape(rank_reason(company))}</p>
  {findings_html}
  {limitations_block}
</section>"""


def render_results(companies: list[CompanyResult], markdown_path, json_path) -> str:
    eligible = sum(company.status == "eligible" for company in companies)
    cards = "\n".join(_render_company_card(company) for company in companies) or "<p>No companies produced a result.</p>"
    return f"""<!doctype html>
<html>
<head><title>Prospect Scout results</title><style>{_STYLE}</style></head>
<body>
<h1>Results</h1>
<p>{len(companies)} companies checked, {eligible} eligible. Highest-priority companies are listed first — see "Why ranked here" on each.</p>
{cards}
<p>Full report also saved locally: {html.escape(str(markdown_path))} / {html.escape(str(json_path))}</p>
<p><a href="/">Run another search</a></p>
</body>
</html>"""


def run_research_form(
    city: str,
    state: str,
    country: str,
    profile_id: str,
    roles_input: str,
    fetch: Fetch = fetch_page,
    discover: Discover = discover_domains,
    limit: int = DEFAULT_LIMIT,
    output: str = "reports/webapp-report.md",
    max_workers: int = WEB_CONCURRENCY,
) -> str:
    """Pure orchestration for one submitted form: validate, discover, research, rank, render.

    Kept separate from the route handler below so tests can call it directly
    with fakes, the same way tests/test_research.py tests analyse_company.
    """
    try:
        profile = profile_by_id(profile_id)
        role_terms = tuple(term.strip() for term in roles_input.split(",") if term.strip()) or profile.role_terms
        request = ResearchRequest(city=city, state=state, country=country, roles=role_terms, profile=profile.id)
    except ValueError as exc:
        return render_form(error=str(exc))

    domains = discover(city, state, country)
    if not domains:
        return render_form(error="No businesses with a public website were found near that location via OpenStreetMap. Try a nearby larger town, or double-check the spelling.")

    if len(domains) > limit:
        domains = domains[:limit]

    report = run_research(request, domains, fetch=fetch, profile=profile, max_workers=max_workers)
    report.companies = rank_companies(report.companies)
    markdown_path, json_path = write_report(report, output)
    return render_results(report.companies, markdown_path, json_path)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return render_form()


@app.post("/research", response_class=HTMLResponse)
def research(
    city: str = Form(...),
    state: str = Form(...),
    country: str = Form(...),
    profile: str = Form("technology"),
    roles: str = Form(""),
) -> str:
    return run_research_form(city, state, country, profile, roles)
