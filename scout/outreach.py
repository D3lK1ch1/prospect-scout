"""Deterministic outreach guidance - not a drafted message.

No AI model call, no invented content, no third-party enrichment, and
deliberately no composed sentence-level copy - no greeting, no closing, no
send-ready paragraph. That's a different thing from being generic, though:
this still pulls in the specific evidence and source URL for the company at
hand, because "what to put in the email" has to mean *this* company's real
finding, not a category-level writing tip. What it adds on top of the
`evidence`/`source_url`/`suggestion` fields already shown separately in the
report is framing - how to use that specific evidence (open with it, ask one
small question, never claim you can help before a conversation has
happened) - grounded in real cold-outreach advice fetched in RESEARCH.md's
2026-08-06 session. Matches README.md's "any outreach is written and sent by
you by hand": personalized raw material, not a message to copy-paste.
"""

from __future__ import annotations

from scout.models import Finding

# team_contact_signal is contact metadata, not a value-proposition finding -
# no outreach angle applies to it alone, so it's deliberately absent here.
_KIND_POINTERS = {
    "advertised_role_signal": "Mention the specific role at {source_url}",
    "case_study_role_signal": "Mention the specific work described at {source_url}",
    "seo_metadata_gap": "Mention the specific detail found at {source_url}",
    "broken_link_signal": "Mention the specific broken link at {source_url}",
    "platform_detected": "Mention the specific platform detail found at {source_url}",
}

_KIND_FRAMING = (
    "then ask one small, specific question about it - "
    "not a claim that you can help before any conversation has happened."
)


def suggest_outreach_points(finding: Finding) -> str | None:
    """What to put in your own outreach for this specific finding - the real
    evidence and source, plus how to frame it. Not a drafted message: no
    greeting, no closing, nothing send-ready. None for finding kinds this
    doesn't apply to (e.g. team_contact_signal).
    """
    if finding.kind == "potential_role_related_need":
        # finding.suggestion here is already contact/MSP-aware and specific
        # to this company (see hidden_need_finding()) - only the framing tip
        # is added, not a repeat of the evidence sentence (which is
        # hidden_need_finding()'s own meta-commentary, not real site content -
        # see the earlier confirmed bug this avoided).
        return f"{finding.suggestion} Keep this one tentative - lead with genuine curiosity, not a claim that a need exists."
    pointer_template = _KIND_POINTERS.get(finding.kind)
    if pointer_template is None:
        return None
    pointer = pointer_template.format(source_url=finding.source_url)
    return f'{pointer}: "{finding.evidence}" - {_KIND_FRAMING}'
