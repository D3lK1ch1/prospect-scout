"""Command-line entry point for Prospect Scout.

Run with:  python -m scout

Unit 1 (fetcher): `audit` politely fetches the page (robots.txt honored)
and prints proof it arrived — status, final URL, size, and page title.
The actual checks land in Unit 2.
"""

import argparse
import sys

from bs4 import BeautifulSoup

from scout.fetcher import fetch_page
from scout.models import ResearchRequest
from scout.reporting import write_report
from scout.research import read_domains, run_research
from scout.profiles import ResearchProfile, custom_profile, load_profiles, profile_by_id


def cmd_audit(url: str) -> int:
    """Handle `audit <url>`. Returns a process exit code."""
    # Minimal sanity check so a typo gives a clear message, not a crash later.
    if not (url.startswith("http://") or url.startswith("https://")):
        print(f"'{url}' doesn't look like a URL. Include http:// or https://")
        return 1

    print(f"Fetching {url} ...")
    result = fetch_page(url)

    if not result.ok:
        detail = f" (final URL: {result.final_url})" if result.final_url else ""
        print(f"Could not fetch: {result.error}{detail}")
        return 1

    soup = BeautifulSoup(result.html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else "(no <title> tag)"

    print(f"Fetched {result.final_url}  [{result.status}, {len(result.html):,} chars]")
    print(f"Page title: {title}")
    print("Fetch layer works. Checks arrive in Unit 2.")
    return 0


def _prompt(label: str, input_fn=input, required: bool = True) -> str:
    value = input_fn(f"{label}: ").strip()
    if required and not value:
        raise ValueError(f"{label.lower()} is required")
    return value


def _terms(value: str) -> tuple[str, ...]:
    return tuple(term.strip() for term in value.split(",") if term.strip())


def choose_profile(input_fn=input) -> ResearchProfile:
    profiles = load_profiles()
    print("Choose a focus:")
    for number, profile in enumerate(profiles, 1):
        print(f"  {number}. {profile.label}")
    print("  C. Custom")
    choice = _prompt("Focus number or C", input_fn).lower()
    if choice == "c":
        return custom_profile(
            _prompt("Custom focus name", input_fn),
            _terms(_prompt("Role terms to look for (comma-separated)", input_fn)),
            _terms(_prompt("Relevant page labels or URL words (comma-separated)", input_fn)),
            _prompt("Cautious opportunity prompt", input_fn),
        )
    try:
        return profiles[int(choice) - 1]
    except (ValueError, IndexError) as exc:
        raise ValueError("choose a displayed focus number or C for Custom") from exc


def interactive_request(input_fn=input) -> tuple[ResearchRequest, str, ResearchProfile]:
    """Ask for the essential research choices in a terminal-friendly wizard."""
    print("Prospect Scout research setup")
    print("The chosen focus controls which roles and company pages are scanned.")
    city = _prompt("City", input_fn)
    state = _prompt("State / region", input_fn)
    country = _prompt("Country", input_fn)
    profile = choose_profile(input_fn)
    role_input = _prompt("Specific roles/interests (comma-separated; Enter to use focus terms)", input_fn, required=False)
    roles = _terms(role_input) or profile.role_terms
    domains = _prompt("Path to a text file of company domains/URLs (one per line)", input_fn)
    return ResearchRequest(city=city, state=state, country=country, roles=roles, profile=profile.id), domains, profile


def cmd_research(args: argparse.Namespace, input_fn=input) -> int:
    try:
        if args.interactive:
            request, domains_file, profile = interactive_request(input_fn)
        else:
            if args.profile == "custom":
                profile = custom_profile(args.profile_name or "Custom", tuple(args.role), tuple(args.page_term), args.opportunity_prompt or "Ask a cautious, evidence-led discovery question; do not assume an open role.")
            else:
                profile = profile_by_id(args.profile)
            request = ResearchRequest(args.city, args.state, args.country, tuple(args.role), profile.id)
            domains_file = args.domains
        domains = read_domains(domains_file)
    except (OSError, ValueError) as exc:
        print(f"Research setup failed: {exc}")
        return 2

    if not domains:
        print("Research setup failed: the domains file has no valid company domains.")
        return 2
    if len(domains) > args.limit:
        domains = domains[:args.limit]
        print(f"Limiting this run to {args.limit} supplied domains.")

    report = run_research(request, domains, profile=profile)
    markdown, json_file = write_report(report, args.output)
    eligible = sum(company.status == "eligible" for company in report.companies)
    print(f"Saved {len(report.companies)} company results ({eligible} eligible) to {markdown} and {json_file}.")
    print("A company is eligible only with a verified location and an evidence-backed active role.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scout",
        description="Prospect Scout — private website audit tool.",
    )
    subparsers = parser.add_subparsers(dest="command")

    audit = subparsers.add_parser("audit", help="Audit a single company URL.")
    audit.add_argument("url", help="The site to audit, e.g. https://example.com")

    research = subparsers.add_parser("research", help="Research supplied company domains and write a local report.")
    research.add_argument("--interactive", action="store_true", help="Ask for location, roles, and domain-list file.")
    research.add_argument("--city", help="Target city")
    research.add_argument("--state", help="Target state or region")
    research.add_argument("--country", help="Target country")
    research.add_argument("--role", action="append", default=[], help="Role or technical interest; repeat as needed")
    research.add_argument("--profile", default="technology", help="Focus profile ID, or custom (default: technology)")
    research.add_argument("--profile-name", help="Display name for --profile custom")
    research.add_argument("--page-term", action="append", default=[], help="Page label/URL word for --profile custom; repeat as needed")
    research.add_argument("--opportunity-prompt", help="Cautious suggested-next-step text for --profile custom")
    research.add_argument("--domains", help="Text file containing one company domain/URL per line")
    research.add_argument("--limit", type=int, default=20, help="Maximum supplied domains to inspect (default: 20)")
    research.add_argument("--output", default="reports/prospect-scout-report.md", help="Local Markdown report path")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    supplied = sys.argv[1:] if argv is None else argv
    # Launching without arguments is the intended friendly entry point.
    args = parser.parse_args(["research", "--interactive"] if not supplied else supplied)

    if args.command == "audit":
        return cmd_audit(args.url)

    if args.command == "research":
        if not args.interactive and not all((args.city, args.state, args.country, args.role, args.domains)):
            parser.error("research needs --interactive, or --city --state --country --role and --domains")
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        return cmd_research(args)

    # argparse guarantees a valid command, so we never reach here.
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
