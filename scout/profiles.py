"""Configurable research profiles used by the scanner and terminal wizard."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResearchProfile:
    id: str
    label: str
    role_terms: tuple[str, ...]
    page_terms: tuple[str, ...]
    fallback_paths: tuple[str, ...]
    opportunity_prompt: str
    # Titles worth naming if the company's own team/about page publishes one
    # (see research.py:contact_finding) - empty means the contacts feature is
    # silently a no-op for this profile, never a guess. Optional/defaulted so
    # existing profile data and the Custom UI path (no contacts UI yet) keep
    # working unchanged.
    contact_titles: tuple[str, ...] = ()


def _profile(data: dict) -> ResearchProfile:
    return ResearchProfile(
        id=data["id"], label=data["label"], role_terms=tuple(data["role_terms"]),
        page_terms=tuple(data["page_terms"]), fallback_paths=tuple(data["fallback_paths"]),
        opportunity_prompt=data["opportunity_prompt"],
        contact_titles=tuple(data.get("contact_titles", [])),
    )


def load_profiles() -> list[ResearchProfile]:
    path = Path(__file__).with_name("profiles.json")
    return [_profile(item) for item in json.loads(path.read_text(encoding="utf-8"))]


def profile_by_id(profile_id: str) -> ResearchProfile:
    for profile in load_profiles():
        if profile.id == profile_id:
            return profile
    choices = ", ".join(profile.id for profile in load_profiles())
    raise ValueError(f"unknown profile '{profile_id}'. Choose one of: {choices}, custom")


def custom_profile(label: str, role_terms: tuple[str, ...], page_terms: tuple[str, ...], opportunity_prompt: str) -> ResearchProfile:
    if not label.strip() or not role_terms or not page_terms or not opportunity_prompt.strip():
        raise ValueError("custom profile needs a name, role terms, page types, and opportunity prompt")
    return ResearchProfile(
        id="custom", label=label.strip(), role_terms=role_terms,
        page_terms=page_terms, fallback_paths=(), opportunity_prompt=opportunity_prompt.strip(),
    )
