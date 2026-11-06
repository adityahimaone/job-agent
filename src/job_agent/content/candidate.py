"""Human-editable Markdown candidate knowledge base.

The profile is a single Markdown file: optional YAML front matter for scalar and
structured data, followed by human-readable sections that are parsed into lists.
Parsing is deliberately tolerant -- missing sections, missing front matter, empty
files, and ``-``/``*`` bullets are all handled without raising.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from job_agent.utils.logging import get_logger

log = get_logger(__name__)

# Front-matter keys that map directly onto CandidateProfile fields. Any *other*
# front-matter key (e.g. ``education``) is exposed verbatim as a template variable
# through ``as_context``.
_CORE_KEYS = {
    "name",
    "email",
    "location",
    "timezone",
    "headline",
    "years_experience",
    "links",
    "skills",
    "roles",
    "projects",
    "facts",
    "constraints",
    "do_not_claim",
}

_SECTION_HEADING = re.compile(r"^\s*#{2}\s+(.+?)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.+)$")


def _parse_front_matter(text: str) -> tuple[dict, str]:
    """Return ``(front_matter_dict, remaining_body)``.

    Front matter is a leading ``---`` fenced YAML block. If it is absent or
    invalid YAML, an empty dict is returned and the whole text is the body.
    """
    fm: dict = {}
    body = text
    stripped = text.lstrip()
    if stripped.startswith("---"):
        lines = stripped.splitlines()
        closing = None
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                closing = i
                break
        if closing is not None:
            fm_text = "\n".join(lines[1:closing])
            body = "\n".join(lines[closing + 1 :])
            try:
                loaded = yaml.safe_load(fm_text) or {}
                if isinstance(loaded, dict):
                    fm = loaded
            except Exception:  # invalid YAML is treated as "no front matter"
                fm = {}
    return fm, body


def _split_sections(body: str) -> dict[str, str]:
    """Split on level-2 ``## `` headings into ``{lowercase_heading: text}``.

    ``### `` (level 3) lines are content, not sections, so role/project headers
    survive intact.
    """
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in (body or "").splitlines():
        match = _SECTION_HEADING.match(line)
        if match:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = match.group(1).strip().lower()
            buf = []
        else:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def _bullets(section: str) -> list[str]:
    """Extract ``- `` / ``* `` bullet items from a section."""
    out: list[str] = []
    for line in (section or "").splitlines():
        match = _BULLET.match(line)
        if match:
            item = match.group(1).strip()
            if item:
                out.append(item)
    return out


def _parse_roles(section: str) -> list[dict]:
    """Parse an ``## Experience`` section.

    Format (one role per ``###`` header, bullets beneath)::

        ### Title | Company | Dates | Location
        - Did a thing
        - Shipped another thing
    """
    roles: list[dict] = []
    current: dict | None = None
    for line in (section or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            if current:
                roles.append(current)
            parts = [p.strip() for p in stripped[4:].split("|")]

            def get(i: int) -> str:
                return parts[i] if i < len(parts) else ""

            current = {
                "title": get(0),
                "company": get(1),
                "dates": get(2),
                "location": get(3),
                "bullets": [],
            }
        elif current is not None:
            match = _BULLET.match(line)
            if match:
                current["bullets"].append(match.group(1).strip())
    if current:
        roles.append(current)
    return roles


def _parse_projects(section: str) -> list[dict]:
    """Parse a ``## Projects`` section.

    Format::

        ### Name | Stack | URL
        A short blurb line (optional).
    """
    projects: list[dict] = []
    current: dict | None = None
    for line in (section or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            if current:
                projects.append(current)
            parts = [p.strip() for p in stripped[4:].split("|")]

            def get(i: int) -> str:
                return parts[i] if i < len(parts) else ""

            url = get(2)
            if url in ("-", "--", "—"):
                url = ""
            current = {"name": get(0), "stack": get(1), "url": url, "blurb": ""}
        elif current is not None and stripped and not stripped.startswith("#"):
            match = _BULLET.match(line)
            text_line = match.group(1).strip() if match else stripped
            current["blurb"] = (current["blurb"] + " " + text_line).strip()
    if current:
        projects.append(current)
    return projects


# -- tolerant coercion helpers -------------------------------------------------


def _as_str_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


def _as_dict(value: Any) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _as_skills(value: Any) -> dict[str, list[str]]:
    if isinstance(value, dict):
        return {str(k): _as_str_list(v) for k, v in value.items()}
    return {}


def _as_obj_list(value: Any) -> list[dict]:
    if isinstance(value, list):
        return [dict(x) for x in value if isinstance(x, dict)]
    return []


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_roles(roles: list[dict]) -> list[dict]:
    out: list[dict] = []
    for role in roles:
        r = dict(role)
        r.setdefault("title", "")
        r.setdefault("company", "")
        r.setdefault("dates", "")
        r.setdefault("location", "")
        r.setdefault("bullets", [])
        if not isinstance(r["bullets"], list):
            r["bullets"] = _as_str_list(r["bullets"])
        out.append(r)
    return out


def _normalize_projects(projects: list[dict]) -> list[dict]:
    out: list[dict] = []
    for project in projects:
        p = dict(project)
        p.setdefault("name", "")
        p.setdefault("stack", "")
        p.setdefault("url", "")
        p.setdefault("blurb", "")
        out.append(p)
    return out


@dataclass
class CandidateProfile:
    """A grounded, human-editable candidate knowledge base."""

    name: str = "Candidate"
    email: str = ""
    location: str = ""
    timezone: str = ""
    links: dict[str, str] = field(default_factory=dict)
    headline: str = ""
    years_experience: int = 0
    facts: list[str] = field(default_factory=list)  # verified one-line claims
    skills: dict[str, list[str]] = field(default_factory=dict)
    roles: list[dict] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    constraints: dict[str, object] = field(default_factory=dict)
    do_not_claim: list[str] = field(default_factory=list)
    raw_markdown: str = ""

    def as_context(self) -> dict:
        """Return a flat dict for Jinja rendering.

        Core fields are normalised so templates never hit a missing variable
        (e.g. ``candidate.constraints.availability`` always resolves). Extra
        front-matter keys (``education``, etc.) are merged verbatim, so anything
        a human adds to the front matter becomes a usable template variable.
        """
        links: dict[str, str] = {"portfolio": "", "github": "", "linkedin": ""}
        links.update(_as_str_dict(self.links))

        constraints: dict[str, object] = {
            "remote_only": True,
            "employment_types": [],
            "hours_per_week": None,
            "min_hourly_rate": 0,
            "currency": "USD",
            "timezone_alignment": "",
            "availability": "part-time, remote",
        }
        constraints.update(_as_dict(self.constraints))

        skills_flat: list[str] = []
        for items in (self.skills or {}).values():
            if isinstance(items, (list, tuple)):
                skills_flat.extend(str(x) for x in items)

        ctx: dict[str, object] = {
            "name": self.name,
            "email": self.email,
            "location": self.location,
            "timezone": self.timezone,
            "links": links,
            "headline": self.headline,
            "years_experience": self.years_experience,
            "years_experience_label": f"{self.years_experience}+ years",
            "facts": list(self.facts or []),
            "facts_text": self.grounded_facts_text(),
            "skills": _as_skills(self.skills),
            "skills_flat": skills_flat,
            "roles": _normalize_roles(list(self.roles or [])),
            "projects": _normalize_projects(list(self.projects or [])),
            "constraints": constraints,
            "do_not_claim": list(self.do_not_claim or []),
            # Sensible default so the Education section renders even on a bare
            # profile; front matter may override this via the extras merge below.
            "education": [],
        }

        fm, _ = _parse_front_matter(self.raw_markdown or "")
        for key, value in fm.items():
            if key not in _CORE_KEYS:
                ctx[key] = value
        return ctx

    def grounded_facts_text(self) -> str:
        """Verified facts as a bulleted block (for AI prompts / evidence)."""
        return "\n".join(f"- {fact}" for fact in (self.facts or []))

    @property
    def version(self) -> str:
        """Short content hash of the source Markdown (first 12 sha1 chars)."""
        return hashlib.sha1((self.raw_markdown or "").encode("utf-8")).hexdigest()[:12]


def _build(raw: str) -> CandidateProfile:
    fm, body = _parse_front_matter(raw)
    sections = _split_sections(body)

    # Front matter may carry structured ``roles``/``projects``/``facts`` lists;
    # otherwise the human-editable Markdown sections are the source of truth.
    roles = _as_obj_list(fm.get("roles")) or _parse_roles(
        sections.get("experience", "")
    )
    projects = _as_obj_list(fm.get("projects")) or _parse_projects(
        sections.get("projects", "")
    )
    facts = _as_str_list(fm.get("facts")) or _bullets(
        sections.get("verified facts", "")
    )
    do_not_claim = _as_str_list(fm.get("do_not_claim")) or _bullets(
        sections.get("do not claim", "")
    )

    return CandidateProfile(
        name=str(fm.get("name") or "Candidate"),
        email=str(fm.get("email") or ""),
        location=str(fm.get("location") or ""),
        timezone=str(fm.get("timezone") or ""),
        links=_as_str_dict(fm.get("links")),
        headline=str(fm.get("headline") or ""),
        years_experience=_to_int(fm.get("years_experience")),
        facts=facts,
        skills=_as_skills(fm.get("skills")),
        roles=roles,
        projects=projects,
        constraints=_as_dict(fm.get("constraints")),
        do_not_claim=do_not_claim,
        raw_markdown=raw,
    )


def load_candidate_profile(path: str | Path) -> CandidateProfile:
    """Load a candidate profile; never raise.

    A missing or unreadable file yields a minimal empty profile with
    ``name="Candidate"`` so the app works on first run.
    """
    p = Path(path)
    if not p.is_file():
        log.warning("candidate_profile_missing", path=str(p))
        return CandidateProfile(name="Candidate")
    try:
        raw = p.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("candidate_profile_unreadable", path=str(p), error=str(exc))
        return CandidateProfile(name="Candidate")
    try:
        return _build(raw)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("candidate_profile_parse_failed", path=str(p), error=str(exc))
        return CandidateProfile(name="Candidate", raw_markdown=raw)
