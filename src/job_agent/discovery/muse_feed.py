"""Muse fusion lane, LinkedIn timeline/feed edition: import jobs shared as
LinkedIn feed posts into job-agent.

LinkedIn's formal job search misses a large share of real openings: hiring
managers and companies post roles as *timeline posts* ("We're hiring a
Frontend Engineer…", "Looking for a React dev to join our team…") with no
formal LinkedIn job posting behind them. There is no public feed API for a
signed-in timeline, and job-agent cannot call Muse's browser directly — so
this lane uses the same contract as the rest of the Muse fusion lane:

    Muse reads Aditya's LinkedIn timeline/feed in the browser
      -> writes a feed file (JSON/CSV, one row per post)
      -> ``job-agent muse import-feed <file>`` parses, extracts, dedups,
         scores and queues, exactly like ``muse import``

Input rows are *posts*, not jobs. A post only becomes a job when it
carries a hiring signal AND an extractable frontend role (title + company);
anything else is reported as skipped (``non_job`` / ``incomplete``) and
never stored. Imported jobs are stamped ``platform=muse`` /
``source="muse-feed"`` so they stay distinguishable from formal-posting
imports (``source="muse"``), deduped against the DB (same external id, and
cross-platform title+company), scored with the existing JEV matcher — the
thin-description floor applies, since most feed posts are one-liners — and
decided with ``decide_job(..., require_approval=True)``. Best outcome is
QUEUED. Import never applies, never sends a connection request, and never
sends a message.

Connecting to a job poster uses the app's existing grounded connection
template (``config/templates/connection_note.md``, rendered by the content
generator — see ``render_feed_connection_note`` / ``muse feed-note``).
Rendering a note is preparation only: nothing is sent, and the note must be
approved by Aditya word-for-word before anyone clicks Connect in a browser.
The programmatic outreach in ``platforms/linkedin/outreach.py``
(``LinkedInOutreach.send_connection_request``) is NOT wired into this lane.

No new DB schema is needed: ``jobs.source`` already exists, and
``Platform.MUSE`` is a plain string value in SQLite (no CHECK constraint
on the live ``jobs`` table).
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from job_agent.db.models import Job, Platform
from job_agent.db.repository import MatchResultRepository
from job_agent.discovery.muse import import_muse_jobs
from job_agent.platforms.base import JobPosting
from job_agent.utils.logging import get_logger

log = get_logger(__name__)

FEED_SOURCE = "muse-feed"
FEED_PLATFORM = Platform.MUSE

# Accept these (case-insensitive) column / key spellings in feed files.
_FIELD_ALIASES: dict[str, str] = {
    "post_url": "post_url",
    "post_link": "post_url",
    "url": "post_url",
    "link": "post_url",
    "post_text": "post_text",
    "text": "post_text",
    "content": "post_text",
    "body": "post_text",
    "author": "author",
    "poster": "author",
    "posted_by": "author",
    "author_url": "author_url",
    "author_profile": "author_url",
    "author_company": "author_company",
    "company": "company",
    "company_name": "company",
    "employer": "company",
    "title": "title",
    "role": "title",
    "job_title": "title",
    "location": "location",
    "apply_url": "apply_url",
    "job_url": "apply_url",
    "description": "description",
    "job_description": "description",
    "salary": "salary",
    "employment_type": "employment_type",
    "job_type": "employment_type",
    "type": "employment_type",
    "remote": "remote",
    "posted_at": "posted_at",
    "date": "posted_at",
    "external_id": "external_id",
    "id": "external_id",
    "tags": "tags",
    "skills": "tags",
}

# A post only counts as a job post when it carries one of these signals.
_HIRING_SIGNALS = re.compile(
    r"\b(hiring|we'?re hiring|we are hiring|now hiring|looking for|seeking"
    r"|job opening|open role|open position|vacancy|vacancies"
    r"|join (our|the) team|apply now|we'?re looking|open to work\b)"
    r"|\b(hiring|vacancy)\b",
    re.IGNORECASE,
)

# Auto-extracted titles must look like a frontend/design role. Explicit
# title+company rows bypass this check — Muse already judged those.
_FRONTEND_ROLE = re.compile(
    r"\b(frontend|front-end|front end|react|design engineer|design systems"
    r"|ui engineer|ui/ux|ux engineer|web developer|software engineer"
    r"|javascript|typescript|next\.?js|vue|angular)\b",
    re.IGNORECASE,
)

# A job title is a short run of Capitalized words ("Senior Frontend
# Engineer", "React + Next.js Engineer"). Requiring capitals keeps the
# capture from swallowing the preamble ("We're hiring a …") or the tail
# ("… to build intuitive interfaces"). A word may contain one internal
# dot ("Next.js") but never a trailing one — otherwise "Engineer.
# Intellias" would glue the next sentence onto the title.
_TITLE_WORD = r"[A-Z][A-Za-z0-9/+'\-]*(?:\.[A-Za-z0-9/+'\-]+)?"
_TITLE_SEQ = (
    rf"(?:{_TITLE_WORD})"
    rf"(?:\s+(?:{_TITLE_WORD}|and|&|\+|/)){{0,5}}"
)

# Location tokens recognised in post text when no explicit location is given.
_LOCATION_TOKENS = (
    "Remote",
    "Singapore",
    "Jakarta",
    "Kuala Lumpur",
    "Malaysia",
    "Indonesia",
    "Bangkok",
    "Vietnam",
    "Philippines",
    "Remote-first",
    "Hybrid",
)

_URL_RE = re.compile(r"https?://[^\s)>\]]+")


def _hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", "ignore")).hexdigest()[:16]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _clean_title(title: str) -> str:
    """Trim a regex-captured title down to the role words.

    Captures are greedy, so "Frontend Developer to build intuitive user
    interfaces" must become "Frontend Developer": keep the leading run of
    Title-Case words (plus connectors like ``&`` / ``+`` / ``and``) and
    stop at the first lowercase word that is not a connector.
    """
    title = _clean(title)
    title = re.split(r"\s+\(", title)[0]
    title = re.split(r"\s+[|\-–—]\s+", title)[0]
    words: list[str] = []
    connectors = {"and", "&", "of", "for", "+", "/"}
    for raw_word in title.split():
        word = raw_word.strip(",.;:·•")
        if not word:
            continue
        if word[0].isupper() or word[0].isdigit() or word in connectors:
            words.append(word)
        elif word.lower() in connectors:
            words.append(word)
        else:
            break
    return _clean(" ".join(words))


def _clean_company(company: str) -> str:
    """Trim a regex-captured company name at the first sentence end."""
    company = _clean(company)
    company = re.split(r"\.\s+[A-Z]", company)[0]
    company = re.split(r"\s+\(", company)[0]
    company = re.split(r"\s+[|\-–—]\s+", company)[0]
    return company.strip(" -–—:·•,.")


def classify_post(text: str) -> str:
    """``"job"`` when the post text carries a hiring signal, else ``"non_job"``."""
    return "job" if _HIRING_SIGNALS.search(text or "") else "non_job"


def _extract_location(text: str) -> str:
    lowered = (text or "").lower()
    for token in _LOCATION_TOKENS:
        if token.lower() in lowered:
            return token
    return ""


def _extract_apply_url(text: str, post_url: str) -> str:
    """First URL in the post text that is not the post itself, else the post URL."""
    for url in _URL_RE.findall(text or ""):
        cleaned = url.rstrip(".,")
        if cleaned != post_url and "linkedin.com/posts" not in cleaned:
            return cleaned
    return post_url


def extract_job_details(text: str) -> dict[str, str]:
    """Best-effort extraction of title / company / location / apply URL.

    Returns a dict that may be partial (missing title or company). The
    caller decides whether that is enough to import: a post needs both a
    title and a company, and an auto-extracted title must be a frontend
    role (see ``_FRONTEND_ROLE``).
    """
    flat = _clean(text)
    title = ""
    company = ""

    patterns = [
        # "HIRING – Frontend Developer" / "HIRING: React Developer"
        re.compile(rf"\b(?i:hiring|vacancy)\s*[:\-–—|]\s*(?-i:({_TITLE_SEQ}))"),
        # "Frontend Engineer at Acme" / "React Developer at Tokopedia"
        re.compile(rf"\b({_TITLE_SEQ})\s+at\s+([A-Z][A-Za-z0-9 &.'\-]{{2,50}})"),
        # "hiring a Frontend Engineer" / "looking for an experienced
        # Senior Frontend Engineer" — lowercase filler words (a, an,
        # experienced, talented, …) may sit between signal and title.
        # The title itself stays case-sensitive (``(?-i:…)``) so the
        # capture cannot swallow the preamble or the tail.
        re.compile(
            rf"\b(?i:hiring|looking for|seeking|searching for)\s+"
            rf"(?:(?:a|an|our|for)\s+|[a-z][a-z'’\-]*\s+){{0,4}}?"
            rf"(?-i:({_TITLE_SEQ}))"
        ),
        # "Role: Frontend Engineer" / "Position: React Developer"
        re.compile(rf"\b(?i:role|position|opening)\s*[:\-]\s*(?-i:({_TITLE_SEQ}))"),
        # "join our team as a Frontend Engineer"
        re.compile(
            rf"\b(?i:join)\s+(?:us|our team)\s+as\s+(?:a\s+|an\s+)?"
            rf"(?-i:({_TITLE_SEQ}))"
        ),
    ]
    for pattern in patterns:
        match = pattern.search(flat)
        if not match:
            continue
        candidate = _clean_title(match.group(1))
        if not candidate or len(candidate) < 3:
            continue
        title = candidate
        if len(match.groups()) >= 2 and match.group(2):
            company = _clean_company(match.group(2))
        break

    if title and not company:
        at_match = re.search(r"\bat\s+([A-Z][A-Za-z0-9 &.'\-]{2,50})", flat)
        if at_match:
            company = _clean_company(at_match.group(1))

    return {
        "title": title,
        "company": company,
        "location": _extract_location(flat),
        "apply_url": _extract_apply_url(flat, ""),
    }


def feed_external_id(raw: dict[str, Any]) -> str:
    """Stable, namespaced external id for a feed-shared job.

    ``muse-feed:<given-id>`` when the input supplies one, otherwise
    ``muse-feed:<sha1(post_url)>`` / ``muse-feed:<sha1(title|company|author)>``.
    The ``muse-feed:`` prefix keeps feed jobs in their own dedup namespace
    even when the formal lane found the same posting under another id.
    """
    given = str(raw.get("external_id") or raw.get("id") or "").strip()
    if given:
        return given if given.startswith("muse-feed:") else f"muse-feed:{given}"
    post_url = str(raw.get("post_url") or "").strip()
    if post_url:
        return f"muse-feed:{_hash(post_url)}"
    title = str(raw.get("title") or "").strip().lower()
    company = str(raw.get("company") or "").strip().lower()
    author = str(raw.get("author") or "").strip().lower()
    return f"muse-feed:{_hash(f'{title}|{company}|{author}')}"


def _coerce_raw(row: dict[str, Any]) -> dict[str, Any]:
    """Map aliased / case-insensitive keys to canonical field names."""
    out: dict[str, Any] = {}
    for key, value in (row or {}).items():
        if key is None:
            continue
        canon = _FIELD_ALIASES.get(str(key).strip().lower())
        if canon and canon not in out:
            out[canon] = value
    return out


def feed_post_to_posting(raw: dict[str, Any]) -> JobPosting:
    """Normalise one LinkedIn feed post into a JobPosting.

    Raises ``ValueError`` with a plain reason when the post is not a job
    post, has no extractable job details, or lacks title/company — the
    caller records that row as skipped instead of aborting the import.
    Explicit ``title``/``company`` fields win over extraction; otherwise
    the title/company are extracted from the post text.
    """
    from job_agent.discovery.feeds import _normalize

    row = _coerce_raw(raw)
    text = str(row.get("post_text") or "").strip()
    if not text:
        raise ValueError("empty post text")
    if classify_post(text) != "job":
        raise ValueError("not a job post (no hiring signal)")

    extracted = extract_job_details(text)
    title = str(row.get("title") or "").strip() or extracted["title"]
    company = (
        str(row.get("company") or "").strip()
        or str(row.get("author_company") or "").strip()
        or extracted["company"]
    )
    explicit = bool(str(row.get("title") or "").strip() and company)
    if title and not explicit and not _FRONTEND_ROLE.search(title):
        raise ValueError(f"extracted title is not a frontend role: {title!r}")
    if not title or not company:
        raise ValueError("no job details extractable (title and company required)")

    location = str(row.get("location") or "").strip() or extracted["location"]
    apply_url = str(row.get("apply_url") or "").strip() or extracted["apply_url"]
    post_url = str(row.get("post_url") or "").strip()
    url = apply_url or post_url
    description = str(row.get("description") or "").strip() or text
    tags = row.get("tags")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    elif isinstance(tags, list):
        tags = [str(t).strip() for t in tags if str(t).strip()]
    else:
        tags = []
    extra: dict[str, Any] = {"origin_platform": "linkedin_feed"}
    if post_url:
        extra["post_url"] = post_url
    author = str(row.get("author") or "").strip()
    if author:
        extra["post_author"] = author
    posted_at = str(row.get("posted_at") or "").strip()
    if posted_at:
        extra["posted_at"] = posted_at
    return _normalize(
        FEED_PLATFORM,
        {
            "external_id": feed_external_id(row),
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "url": url,
            "salary": row.get("salary") or "",
            "tags": tags,
            "job_type": row.get("employment_type") or "",
            "remote": bool(row.get("remote"))
            if isinstance(row.get("remote"), bool)
            else str(row.get("remote") or "").strip().lower()
            in ("true", "yes", "remote", "1"),
            "source": FEED_SOURCE,
            "extra": extra,
        },
    )


def parse_feed_file(
    path: str | Path,
) -> tuple[list[JobPosting], list[str], dict[str, int]]:
    """Parse a LinkedIn feed file (JSON or CSV) into postings.

    JSON accepts a list, ``{"posts": [...]}`` (or ``{"jobs": [...]}`` for
    symmetry with the fusion lane), or a single post object. CSV accepts
    the aliased headers in :data:`_FIELD_ALIASES`.
    Returns ``(postings, errors, feed_stats)`` — non-job and incomplete
    posts are reported in ``errors`` / ``feed_stats`` and never fatal.
    ``feed_stats``: ``posts`` (rows seen), ``non_job``, ``incomplete``.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"LinkedIn feed file not found: {p}")
    if p.suffix.lower() == ".csv":
        with p.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    elif p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("posts"), list):
            rows = data["posts"]
        elif isinstance(data, dict) and isinstance(data.get("jobs"), list):
            rows = data["jobs"]
        elif isinstance(data, dict):
            rows = [data]
        elif isinstance(data, list):
            rows = data
        else:
            raise ValueError('JSON must be a post object, a list, or {"posts": [...]}')
    else:
        raise ValueError(f"Unsupported feed file type: {p.suffix} (use .json or .csv)")

    stats: dict[str, int] = {"posts": len(rows), "non_job": 0, "incomplete": 0}
    postings: list[JobPosting] = []
    errors: list[str] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"row {i}: not an object")
            stats["incomplete"] += 1
            continue
        try:
            postings.append(feed_post_to_posting(row))
        except ValueError as exc:
            msg = str(exc)
            if "not a job post" in msg:
                stats["non_job"] += 1
            else:
                stats["incomplete"] += 1
            errors.append(f"row {i}: {msg}")
    return postings, errors, stats


def import_feed_jobs(
    session,
    postings: list[JobPosting],
    profile: dict,
    settings,
    *,
    dry_run: bool = False,
    score: bool = True,
    matcher=None,
) -> dict[str, Any]:
    """Import feed-shared postings via the Muse fusion import.

    This is a thin wrapper over :func:`job_agent.discovery.muse.import_muse_jobs`:
    the same batch/DB/cross-platform dedup, the same JEV scoring (the
    thin-description floor applies — most feed posts are one-liners), and
    the same ``decide_job(..., require_approval=True)`` — best outcome
    QUEUED, never auto-apply, never apply. ``feed_stats`` pass-through
    keys (``posts`` / ``non_job`` / ``incomplete``) are merged into the
    returned stats so the CLI can report the whole funnel.
    """
    stats = import_muse_jobs(
        session,
        postings,
        profile,
        settings,
        dry_run=dry_run,
        score=score,
        matcher=matcher,
    )
    return stats


def render_feed_connection_note(
    job: Job,
    profile: dict,
    session,
    *,
    candidate_path: str | Path = "config/templates/candidate_profile.md",
    templates_dir: str | Path = "config/templates",
) -> tuple[str, list[str]]:
    """Render the grounded LinkedIn connection note for a feed-found job.

    Uses the app's existing template (``config/templates/connection_note.md``,
    rendered by the content generator from ``candidate_profile.md`` — the
    same template the ``content preview`` flow and the dashboard use) with
    the job's stored matched skills when a match result exists.

    Returns ``(note, grounding_warnings)``. Preparation only: the caller
    must show the note to Aditya for approval and must NOT send it — this
    function never touches LinkedIn, and ``LinkedInOutreach`` is not
    involved. The note is capped at 300 characters (LinkedIn's limit).
    """
    from job_agent.content.candidate import load_candidate_profile
    from job_agent.content.generator import generate_documents
    from job_agent.content.templates import load_template_set
    from job_agent.discovery.muse import _posting_from_job

    matched_skills: list[str] = []
    result = MatchResultRepository(session).get_by_job_id(job.id)
    if result is not None and result.matched_skills:
        try:
            matched_skills = json.loads(result.matched_skills)
        except (ValueError, TypeError):
            matched_skills = []
    candidate = load_candidate_profile(candidate_path)
    docs = generate_documents(
        posting=_posting_from_job(job),
        profile=profile,
        candidate=candidate,
        template_set=load_template_set(templates_dir),
        matched_skills=matched_skills,
    )
    return docs.connection_note.strip()[:300], list(docs.warnings)
