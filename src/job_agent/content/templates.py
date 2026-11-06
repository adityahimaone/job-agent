"""Human-editable Jinja2 Markdown templates with deterministic fallbacks.

Templates live as ``<name>.md`` files in a directory (default
``config/templates``). Rendering is strictly offline: no AI and no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import ChainableUndefined, Environment, StrictUndefined

from job_agent.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_TEMPLATES_DIR = Path("config/templates")

# Two environments: strict for normal rendering (surfaces missing variables),
# lenient for the safe fallback (missing variables become empty strings).
# trim_blocks/lstrip_blocks keep Markdown lists compact (a block tag on its own
# line no longer injects a stray blank line).
_ENV_KW = dict(
    autoescape=False,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)
_STRICT_ENV = Environment(undefined=StrictUndefined, **_ENV_KW)
_LENIENT_ENV = Environment(undefined=ChainableUndefined, **_ENV_KW)

# How to edit header is a Jinja comment, so it never appears in output.
_HOWTO = "{# How to edit: plain Markdown + Jinja. See docs/TEMPLATES.md for all variables. #}\n"

_DEFAULT_TEXTS: dict[str, str] = {
    "cover_letter": _HOWTO
    + "{{ greeting }}\n\n"
    + "I'm {{ candidate.name }}, a {{ candidate.headline }} applying for the "
    + "{{ job.title }} role at {{ job.company }}. My relevant skills include "
    + "{{ matched_skills|join(', ') }}.\n\n"
    + "Relevant experience:\n{{ evidence|join('\\n') }}\n\n"
    + "{{ signoff }}\n",
    "email_apply": _HOWTO
    + "Subject: {{ job.title }} at {{ job.company }} — {{ candidate.name }}\n\n"
    + "{{ greeting }}\n\n"
    + "I'm reaching out about the {{ job.title }} role at {{ job.company }}. "
    + "I'm a {{ candidate.headline }}; highlights: {{ evidence|join(' ') }}\n\n"
    + "{{ signoff }}\n",
    "connection_note": _HOWTO
    + "Hi — I'm {{ candidate.name }}, a {{ candidate.headline }}. I saw the "
    + "{{ job.title }} opening at {{ job.company }} and would love to connect. "
    + "Open to a quick chat!\n",
    "gig_profile": _HOWTO
    + "# {{ candidate.name }} — {{ candidate.headline }}\n\n"
    + "{{ candidate.headline }} with {{ candidate.years_experience }}+ years in coding, "
    + "design systems, and AI workflow automation. Exploring {{ job.title }} work at "
    + "{{ job.company }} and similar expert networks. Skills: {{ matched_skills|join(', ') }}.\n",
    "resume_master": _HOWTO
    + "# {{ candidate.name }}\n\n"
    + "{{ candidate.headline }} · {{ candidate.location }} · {{ candidate.email }}\n\n"
    + "<!-- Prepared for {{ job.title }} at {{ job.company }} -->\n\n"
    + "## Summary\n\n{{ candidate.headline }} with {{ candidate.years_experience }}+ years "
    + "of experience.\n\n"
    + "## Experience\n"
    + "{% for role in candidate.roles %}\n### {{ role.title }} | {{ role.company }} | "
    + "{{ role.dates }}\n{% for b in role.bullets %}- {{ b }}\n{% endfor %}{% endfor %}\n\n"
    + "## Projects\n"
    + "{% for p in candidate.projects %}\n- {{ p.name }}{% if p.stack %} — {{ p.stack }}{% endif %}"
    + "{% endfor %}\n\n"
    + "## Skills\n{{ candidate.skills_flat|join(', ') }}\n\n"
    + "## Education\n{% for e in candidate.education %}- {{ e }}\n{% endfor %}\n",
    "candidate_profile": (
        "---\n"
        "name: Candidate\n"
        'email: ""\n'
        'headline: ""\n'
        "years_experience: 0\n"
        "---\n\n"
        "## Verified facts\n\n"
        "## Experience\n\n"
        "## Projects\n\n"
        "## Do not claim\n"
    ),
}

_GENERIC_FALLBACK = (
    _HOWTO + "{{ candidate.name }} — {{ job.title }} at {{ job.company }}\n"
)


def default_template_text(name: str) -> str:
    """Return the built-in fallback text for *name* (or a generic one)."""
    key = str(name).strip().removesuffix(".md")
    return _DEFAULT_TEXTS.get(key, _GENERIC_FALLBACK)


def _safe_name(name: str) -> str:
    """Strip anything that could escape the templates directory."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", str(name).strip().removesuffix(".md"))
    return cleaned or "document"


@dataclass
class TemplateSet:
    """A directory of ``<name>.md`` Jinja templates."""

    directory: Path

    def __post_init__(self) -> None:
        # Accept a plain string as well as a Path.
        self.directory = Path(self.directory)

    def path(self, name: str) -> Path:
        return self.directory / f"{_safe_name(name)}.md"

    def has(self, name: str) -> bool:
        """True if a file or a built-in fallback is available for *name*."""
        return (
            self.path(name).is_file()
            or str(name).strip().removesuffix(".md") in _DEFAULT_TEXTS
        )

    def _read(self, name: str) -> str:
        p = self.path(name)
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8")
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("template_unreadable", template=name, error=str(exc))
        return default_template_text(name)

    def render(self, name: str, **ctx) -> str:
        """Render ``<name>.md``; never raise.

        StrictUndefined is used first so a missing variable is detected. On any
        error we re-render the same text with ChainableUndefined (missing values
        become empty), and as a last resort use the built-in fallback text.
        Every path is deterministic and requires no AI.
        """
        text = self._read(name)
        try:
            return _STRICT_ENV.from_string(text).render(**ctx)
        except Exception as exc:
            log.warning("template_strict_render_failed", template=name, error=str(exc))
        try:
            return _LENIENT_ENV.from_string(text).render(**ctx)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("template_lenient_render_failed", template=name, error=str(exc))
        try:
            return _LENIENT_ENV.from_string(default_template_text(name)).render(**ctx)
        except Exception:  # pragma: no cover - last-resort, must not raise
            return default_template_text(name)


def load_template_set(directory: str | Path) -> TemplateSet:
    """Load a template set. A missing directory is fine (falls back to built-ins)."""
    return TemplateSet(directory=Path(directory))
