"""Deterministic, offline extraction of job requirements.

No AI key and no network are required. Heuristics use regex/keyword matching for
employment type, remote policy, timezone hints, seniority, and skill keywords,
plus a best-effort split of "must have" vs "nice to have" sections. An optional
AI client may *summarise* the posting, but the result is always wrapped in a
deterministic fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from job_agent.utils.logging import get_logger

log = get_logger(__name__)

_MAX_DESCRIPTION = 12000

# (canonical name, regex). Word boundaries avoid false positives such as "Go"
# matching "Google" or "AI" matching "email".
_SKILL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("React", re.compile(r"\breact(?:\.js)?\b", re.I)),
    ("TypeScript", re.compile(r"\btypescript\b", re.I)),
    ("JavaScript", re.compile(r"\bjavascript\b|\bes6\b", re.I)),
    ("Next.js", re.compile(r"\bnext\.js\b|\bnextjs\b", re.I)),
    ("Node.js", re.compile(r"\bnode\.js\b|\bnodejs\b", re.I)),
    ("Vue", re.compile(r"\bvue(?:\.js)?\b", re.I)),
    ("Angular", re.compile(r"\bangular\b", re.I)),
    ("Svelte", re.compile(r"\bsvelte\b", re.I)),
    ("HTML", re.compile(r"\bhtml5?\b", re.I)),
    ("CSS", re.compile(r"\bcss3?\b", re.I)),
    ("Tailwind CSS", re.compile(r"\btailwind(?:\s?css)?\b", re.I)),
    ("Sass", re.compile(r"\bsass\b|\bscss\b", re.I)),
    ("design systems", re.compile(r"\bdesign systems?\b", re.I)),
    ("accessibility", re.compile(r"\baccessibility\b|\ba11y\b|\bwcag\b", re.I)),
    ("performance", re.compile(r"\bperformance\b|\bweb vitals\b", re.I)),
    ("SSR", re.compile(r"\bssr\b|\bserver[\s-]?side rendering\b", re.I)),
    ("Zustand", re.compile(r"\bzustand\b", re.I)),
    ("React Query", re.compile(r"\breact query\b|\btanstack query\b", re.I)),
    ("Redux", re.compile(r"\bredux\b", re.I)),
    ("GraphQL", re.compile(r"\bgraphql\b", re.I)),
    ("REST APIs", re.compile(r"\brest(?:ful)?\s?apis?\b", re.I)),
    ("tRPC", re.compile(r"\btrpc\b", re.I)),
    ("Prisma", re.compile(r"\bprisma\b", re.I)),
    ("PostgreSQL", re.compile(r"\bpostgres(?:ql)?\b", re.I)),
    ("MySQL", re.compile(r"\bmysql\b", re.I)),
    ("SQLite", re.compile(r"\bsqlite\b", re.I)),
    ("MongoDB", re.compile(r"\bmongo\s?db\b|\bmongodb\b", re.I)),
    # Case-sensitive on purpose: "\bgo\b" would match the common verb "go".
    # Require "Go" to be listed/terminated so "Go beyond" is not a false hit.
    ("Go", re.compile(r"\bGolang\b|\bGo\b(?=\s*[,.)/]|\s+(?:and|or)\b|\s*$)", re.M)),
    ("Python", re.compile(r"\bpython\b", re.I)),
    ("Docker", re.compile(r"\bdocker\b", re.I)),
    ("Kubernetes", re.compile(r"\bkubernetes\b|\bk8s\b", re.I)),
    ("Nginx", re.compile(r"\bnginx\b", re.I)),
    ("AWS", re.compile(r"\baws\b|\bamazon web services\b", re.I)),
    ("CI/CD", re.compile(r"\bci\s?/\s?cd\b|\bgithub actions\b", re.I)),
    ("Playwright", re.compile(r"\bplaywright\b|\bcypress\b", re.I)),
    ("testing", re.compile(r"\bunit tests?\b|\btesting\b|\bjest\b", re.I)),
    ("Figma", re.compile(r"\bfigma\b", re.I)),
    ("GSAP/Motion", re.compile(r"\bgsap\b|\bframer motion\b|\bmotion\b", re.I)),
    ("Git", re.compile(r"\bgit\b|\bgithub\b", re.I)),
    ("LLM/AI", re.compile(r"\bllm[s]?\b|\bai\b|\bgenai\b|\bmachine learning\b", re.I)),
    ("Agile", re.compile(r"\bagile\b|\bscrum\b", re.I)),
]
_SKILL_BY_NAME = dict(_SKILL_PATTERNS)

_MUST_MARKERS = [
    "must have",
    "must-have",
    "required",
    "requirements",
    "qualifications",
    "you'll need",
    "you will need",
    "what you bring",
]
_NICE_MARKERS = [
    "nice to have",
    "nice-to-have",
    "preferred",
    "bonus",
    "a plus",
    "good to have",
    "desirable",
    "nice to haves",
]

_EMPLOYMENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("part_time", re.compile(r"\bpart[\s-]?time\b", re.I)),
    ("full_time", re.compile(r"\bfull[\s-]?time\b", re.I)),
    ("contract", re.compile(r"\bcontract(?:or|ing|[\s-]to[\s-]hire)?\b", re.I)),
    ("freelance", re.compile(r"\bfreelance\b|\bfreelancer\b", re.I)),
    ("internship", re.compile(r"\binternship\b|\bintern\b", re.I)),
]

_SENIORITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("principal", re.compile(r"\bprincipal\b", re.I)),
    ("staff", re.compile(r"\bstaff\b", re.I)),
    ("lead", re.compile(r"\blead\b|\bteam lead\b|\btech lead\b", re.I)),
    ("senior", re.compile(r"\bsenior\b|\bsr\.?\b", re.I)),
    ("mid", re.compile(r"\bmid[\s-]?level\b|\bmid\b", re.I)),
    ("junior", re.compile(r"\bjunior\b|\bjr\.?\b", re.I)),
    ("entry", re.compile(r"\bentry[\s-]?level\b", re.I)),
]

_TZ_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bGMT\s*[+-]?\d{1,2}\b", re.I),
    re.compile(r"\bUTC\s*[+-]?\d{1,2}\b", re.I),
    re.compile(r"\bAPAC\b|\bEMEA\b|\bLATAM\b", re.I),
    re.compile(r"\bPST\b|\bPDT\b|\bEST\b|\bEDT\b|\bCET\b|\bBST\b|\bJST\b", re.I),
    re.compile(r"\b[A-Za-z]+\s+time\s*zone\b", re.I),
    re.compile(r"\btime\s*zone\s*(?:alignment|overlap|requirements?)\b", re.I),
]


def _find_earliest(text: str, markers: list[str]) -> int:
    """Index of the earliest marker in *text*, or -1."""
    low = text.lower()
    start = -1
    for marker in markers:
        pos = low.find(marker)
        if pos != -1 and (start == -1 or pos < start):
            start = pos
    return start


def _window(text: str, markers: list[str]) -> str:
    """Return the slice (original case) starting at the earliest marker, or ''."""
    start = _find_earliest(text, markers)
    return text[start:] if start >= 0 else ""


@dataclass
class JobRequirements:
    must_have: list[str] = field(default_factory=list)
    nice_to_have: list[str] = field(default_factory=list)
    employment_types: list[str] = field(default_factory=list)
    remote_policy: str = ""
    timezone: str = ""
    seniority: str = ""
    summary: str = ""

    def as_context(self) -> dict:
        return {
            "must_have": list(self.must_have),
            "nice_to_have": list(self.nice_to_have),
            "employment_types": list(self.employment_types),
            "remote_policy": self.remote_policy,
            "timezone": self.timezone,
            "seniority": self.seniority,
            "summary": self.summary,
            "must_have_text": ", ".join(self.must_have),
            "nice_to_have_text": ", ".join(self.nice_to_have),
        }


def _extract(description: str, title: str, ai_client) -> JobRequirements:
    desc = str(description or "")[:_MAX_DESCRIPTION]
    title = str(title or "")
    raw = f"{title}\n{desc}"  # original case (needed for case-sensitive "Go")
    low = raw.lower()

    detected = [name for name, pattern in _SKILL_PATTERNS if pattern.search(raw)]

    nice_start = _find_earliest(desc, _NICE_MARKERS)
    nice_window = desc[nice_start:] if nice_start >= 0 else ""
    nice_to_have = [
        name for name in detected if _SKILL_BY_NAME[name].search(nice_window)
    ]
    must_have = [name for name in detected if name not in nice_to_have]

    # The "must/required" section ends where a "nice to have" section begins,
    # so a skill mentioned only as a bonus is not pulled back into must_have.
    must_start = _find_earliest(desc, _MUST_MARKERS)
    if must_start >= 0:
        must_end = nice_start if nice_start > must_start else len(desc)
        must_window = desc[must_start:must_end]
        section = [
            name for name in detected if _SKILL_BY_NAME[name].search(must_window)
        ]
        must_have = section + [name for name in must_have if name not in section]

    employment_types = [
        label for label, pattern in _EMPLOYMENT_PATTERNS if pattern.search(low)
    ]

    if re.search(r"\bhybrid\b", low):
        remote_policy = "hybrid"
    elif re.search(r"\bremote\b|\bwork from home\b|\bwfh\b|\bdistributed\b", low):
        remote_policy = "remote"
    elif re.search(r"\bon[\s-]?site\b|\bin[\s-]?office\b|\bon[\s-]?premise", low):
        remote_policy = "onsite"
    else:
        remote_policy = ""

    tz_hints: list[str] = []
    for pattern in _TZ_PATTERNS:
        for match in pattern.findall(desc):
            token = (match if isinstance(match, str) else "".join(match)).strip()
            token = re.sub(r"\s+", " ", token).upper()
            if token and token not in tz_hints:
                tz_hints.append(token)
    timezone = ", ".join(tz_hints[:6])

    seniority = ""
    for label, pattern in _SENIORITY_PATTERNS:
        if pattern.search(low):
            seniority = label
            break

    cleaned = re.sub(r"\s+", " ", desc).strip()
    deterministic_summary = cleaned[:300]
    if len(cleaned) > 300:
        deterministic_summary = deterministic_summary.rstrip() + "…"
    summary = deterministic_summary

    # Optional AI summary -- always guarded, deterministic fallback remains.
    if ai_client is not None:
        try:
            prompt = (
                "Summarize this job posting in two plain sentences (no markdown):\n\n"
                + desc[:2000]
            )
            result = ai_client.complete(
                prompt, system="You summarize job postings concisely."
            )
            if isinstance(result, str) and result.strip():
                summary = result.strip()
        except Exception as exc:
            log.warning("requirements_ai_summarize_failed", error=str(exc))

    return JobRequirements(
        must_have=must_have,
        nice_to_have=nice_to_have,
        employment_types=employment_types,
        remote_policy=remote_policy,
        timezone=timezone,
        seniority=seniority,
        summary=summary,
    )


def extract_requirements(
    description: str, *, title: str = "", ai_client=None
) -> JobRequirements:
    """Extract requirements. Never raises; caps the description at 12000 chars."""
    try:
        return _extract(description, title, ai_client)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("requirements_extraction_failed", error=str(exc))
        return JobRequirements(
            summary=re.sub(r"\s+", " ", str(description or ""))[:300]
        )
