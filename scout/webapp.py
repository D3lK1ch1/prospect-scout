"""Local web UI: location-driven research, as one form and one results page.

Wraps run_research()/write_report() exactly as __main__.py's cmd_research() does;
no new business logic lives here. Candidate companies come from the OSM adapter
(discover_domains) rather than a supplied domain-list file — city/state/country
are enough to run a search.

Scoped to the `technology` profile only — the other profiles in
scout/profiles.json (marketing, retail_operations, business_admin) exist for
the CLI's --profile flag but aren't offered here, to keep the one entry point
this project is actively developing focused. run_research_form() still takes
profile_id as a parameter so it stays directly testable against any profile.
"""

from __future__ import annotations

import html
from collections.abc import Callable

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse

from scout.fetcher import FetchResult, fetch_page
from scout.models import CompanyResult, Finding, ResearchReport, ResearchRequest
from scout.osm_discovery import discover_domains
from scout.outreach import suggest_outreach_points
from scout.profiles import profile_by_id
from scout.ranking import rank_companies, rank_reason
from scout.reporting import write_report
from scout.research import analyse_company, run_research

Fetch = Callable[[str], FetchResult]
Discover = Callable[[str, str, str, str | None], list[str]]

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


_MODE_NAV = '<p><strong>Widespread search</strong> &middot; <a href="/inspect">Specific company</a></p>'
_MODE_NAV_INSPECT = '<p><a href="/">Widespread search</a> &middot; <strong>Specific company</strong></p>'


def render_form(error: str | None = None) -> str:
    error_html = f'<p style="color:#b00020">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html>
<head><title>Prospect Scout</title><style>{_STYLE}</style></head>
<body>
<h1>Prospect Scout research setup</h1>
{_MODE_NAV}
<p>Focus: Technology and digital delivery roles.</p>
{error_html}
<form method="post" action="/research">
  <p><label>City <input name="city" required></label></p>
  <p><label>State/region <input name="state" required></label></p>
  <p><label>Country <input name="country" required></label></p>
  <p><label>Roles/interests (comma-separated; blank uses the default technology terms)
    <input name="roles"></label></p>
  <p><button type="submit">Run research</button></p>
</form>
</body>
</html>"""


def render_inspect_form(error: str | None = None) -> str:
    error_html = f'<p style="color:#b00020">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html>
<head><title>Prospect Scout — specific company</title><style>{_STYLE}</style></head>
<body>
<h1>Prospect Scout — specific company</h1>
{_MODE_NAV_INSPECT}
<p>Already know the company? This skips location discovery and the location check entirely, and scans that company's own site directly for career, team, and case-study pages.</p>
{error_html}
<form method="post" action="/inspect">
  <p><label>Company URL <input name="domain" placeholder="https://example.com" required></label></p>
  <p><label>Roles/interests (comma-separated; blank uses the default technology terms)
    <input name="roles"></label></p>
  <p><button type="submit">Inspect this company</button></p>
</form>
</body>
</html>"""


def _render_finding(finding: Finding) -> str:
    url = html.escape(finding.source_url)
    points = suggest_outreach_points(finding)
    outreach_html = f'<p><em>Outreach angle:</em> {html.escape(points)}</p>' if points else ""
    return f"""<div class="finding">
      <p><strong>{html.escape(finding.kind.replace('_', ' ').title())}</strong> &middot; confidence: {html.escape(finding.confidence)}</p>
      <p>{html.escape(finding.evidence)}</p>
      <p><a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a></p>
      <p><em>Suggested next step:</em> {html.escape(finding.suggestion)}</p>
      {outreach_html}
    </div>"""


def _render_company_card(company: CompanyResult) -> str:
    findings_html = "\n".join(_render_finding(finding) for finding in company.findings) or "<p><em>No findings.</em></p>"
    limitations_html = "".join(f"<li>{html.escape(limitation)}</li>" for limitation in company.limitations)
    limitations_block = f'<ul class="limitations">{limitations_html}</ul>' if limitations_html else ""
    location_text = ("yes" if company.location_verified else "no") if company.location_checked else "skipped (specific-company mode)"
    return f"""<section class="company">
  <h2>{html.escape(company.name)} <span class="status-{html.escape(company.status)}">{html.escape(company.status)}</span></h2>
  <p>Sector: {html.escape(company.sector)} &middot; Location verified: {location_text}</p>
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

    domains = discover(city, state, country, profile.id)
    if not domains:
        return render_form(error="No businesses with a public website were found near that location via OpenStreetMap. Try a nearby larger town, or double-check the spelling.")

    if len(domains) > limit:
        domains = domains[:limit]

    report = run_research(request, domains, fetch=fetch, profile=profile, max_workers=max_workers)
    report.companies = rank_companies(report.companies)
    markdown_path, json_path = write_report(report, output)
    return render_results(report.companies, markdown_path, json_path)


def run_inspect_form(
    domain: str,
    roles_input: str,
    profile_id: str = "technology",
    fetch: Fetch = fetch_page,
    output: str = "reports/webapp-inspect-report.md",
) -> str:
    """Pure orchestration for one submitted specific-company form: no OSM
    discovery, no location check - straight to analyse_company() on the one
    domain the user already picked. Kept separate from the route handler so
    tests can call it directly with fakes, same as run_research_form.
    """
    domain = domain.strip()
    if not (domain.startswith("http://") or domain.startswith("https://")):
        return render_inspect_form(error="Enter a full company URL, including http:// or https://.")

    try:
        profile = profile_by_id(profile_id)
        role_terms = tuple(term.strip() for term in roles_input.split(",") if term.strip()) or profile.role_terms
        request = ResearchRequest(city="", state="", country="", roles=role_terms, profile=profile.id, location_required=False)
    except ValueError as exc:
        return render_inspect_form(error=str(exc))

    company = analyse_company(domain, request, fetch=fetch, profile=profile)
    markdown_path, json_path = write_report(ResearchReport(request=request, companies=[company]), output)
    return render_results([company], markdown_path, json_path)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return render_form()


@app.post("/research", response_class=HTMLResponse)
def research(
    city: str = Form(...),
    state: str = Form(...),
    country: str = Form(...),
    roles: str = Form(""),
) -> str:
    return run_research_form(city, state, country, "technology", roles)


@app.get("/inspect", response_class=HTMLResponse)
def inspect_index() -> str:
    return render_inspect_form()


@app.post("/inspect", response_class=HTMLResponse)
def inspect(
    domain: str = Form(...),
    roles: str = Form(""),
) -> str:
    return run_inspect_form(domain, roles)
