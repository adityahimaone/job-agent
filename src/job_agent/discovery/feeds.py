"""Fast structured-feed discovery ("fast lane").

This module fetches public JSON/HTML feeds concurrently and normalises them
into :class:`~job_agent.platforms.base.JobPosting` objects. It is deliberately
dependency-light (stdlib + httpx + PyYAML) and has **no** browser dependency,
so it is the cheapest way to discover jobs before falling back to the
human-supervised Ego Lite lane or the Playwright fallback.

Supported feed kinds (``FeedSpec.kind``):

``remotive``, ``remoteok``, ``arbeitnow``
    Free public JSON job-board APIs. ``FeedSpec.url`` is the full endpoint.
``greenhouse``, ``lever``, ``ashby``
    Public ATS job-board APIs. ``FeedSpec.url`` is the company board *token*
    (or a full endpoint URL); these are disabled by default until a real token
    is configured.
``linkedin_guest``
    The public "seeMoreJobPostings" HTML fragment, parsed with regexes only
    (no external HTML parser). Often rate-limited; failures are non-fatal.
``muse``
    Import-only source for jobs found by Muse (see
    ``job_agent.discovery.muse``). No endpoint is fetched; the parser
    returns ``[]`` and jobs arrive via ``job-agent muse import``.

Design rules:

* One shared :class:`httpx.Client` (timeouts, ``follow_redirects=True``, a
  polite descriptive User-Agent) is reused across all worker threads.
* :meth:`FeedHub.discover` runs enabled feeds in a ``ThreadPoolExecutor``,
  then applies :func:`job_agent.matching.filters.apply_filters`, dedupes by
  ``(external_id, platform)`` (falling back to a URL hash), caps at ``limit``
  and stamps ``source`` on every posting.
* A single feed failing (network error, bad JSON, HTTP error) NEVER aborts the
  run: the error is recorded on the :class:`FeedResult`, a warning is logged,
  and the other feeds continue. With no network at all, ``discover`` returns
  ``[]``.
"""

from __future__ import annotations

import hashlib
import html as _html
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
import yaml

from job_agent.db.models import Platform
from job_agent.matching.filters import apply_filters
from job_agent.platforms.base import JobPosting
from job_agent.utils.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_USER_AGENT = "job-agent/0.1 (structured-feed-discovery; polite client)"

DEFAULT_REMOTIVE = "https://remotive.com/api/remote-jobs"
DEFAULT_REMOTEOK = "https://remoteok.com/api"
DEFAULT_ARBEITNOW = "https://www.arbeitnow.com/api/job-board-api"
DEFAULT_LINKEDIN_GUEST = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
)

_KIND_PLATFORM: dict[str, Platform] = {
    "remotive": Platform.REMOTIVE,
    "remoteok": Platform.REMOTEOK,
    "arbeitnow": Platform.ARBEITNOW,
    "greenhouse": Platform.GREENHOUSE,
    "lever": Platform.LEVER,
    "ashby": Platform.ASHBY,
    "linkedin_guest": Platform.LINKEDIN,
    "muse": Platform.MUSE,
}

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


@dataclass
class FeedResult:
    """Outcome of a single feed fetch (diagnostics for the last run)."""

    source: str
    postings: list[JobPosting] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str = ""


@dataclass
class FeedSpec:
    """Configuration for one structured feed.

    ``url`` is either the full endpoint (for the JSON/list feeds and LinkedIn
    guest) or an ATS board token / full endpoint (for greenhouse/lever/ashby).
    ``platform`` optionally overrides the platform inferred from ``kind``;
    an unknown string maps to :attr:`Platform.OTHER`.
    """

    name: str
    kind: str  # remotive | remoteok | arbeitnow | greenhouse | lever | ashby | linkedin_guest
    url: str = ""
    enabled: bool = True
    platform: str = ""


# ---------------------------------------------------------------------------
# Platform resolution
# ---------------------------------------------------------------------------


def _resolve_platform_str(value: str) -> Platform:
    if not value:
        return Platform.OTHER
    v = value.strip()
    for p in Platform:
        if p.name.lower() == v.lower() or p.value.lower() == v.lower():
            return p
    return Platform.OTHER


def _platform_for(spec: FeedSpec) -> Platform:
    if spec.platform:
        return _resolve_platform_str(spec.platform)
    return _KIND_PLATFORM.get(spec.kind, Platform.OTHER)


# ---------------------------------------------------------------------------
# Text / normalisation helpers
# ---------------------------------------------------------------------------


def _hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", "ignore")).hexdigest()[:16]


def _strip_html(fragment: Any) -> str:
    if not fragment:
        return ""
    s = str(fragment)
    s = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>", " ", s, flags=re.IGNORECASE | re.DOTALL
    )
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _clean(value: str) -> str:
    return _strip_html(value)


def _norm_employment(value: Any) -> str:
    """Normalise a structured employment-type string (e.g. 'FullTime')."""
    if not value:
        return ""
    v = re.sub(r"[\s_-]+", "", str(value).lower())
    return {
        "fulltime": "full_time",
        "permanent": "full_time",
        "employee": "full_time",
        "parttime": "part_time",
        "parttimer": "part_time",
        "contract": "contract",
        "contractor": "contract",
        "freelance": "contract",
        "temporary": "contract",
        "temp": "contract",
        "intern": "internship",
        "internship": "internship",
        "coop": "internship",
    }.get(v, "")


def _infer_employment_type(text: str, explicit: str = "") -> str:
    """Infer employment type from text/tags. Structured ``explicit`` wins."""
    norm = _norm_employment(explicit)
    if norm:
        return norm
    t = text.lower()
    if re.search(r"part[-\s]?time|\bparttime\b", t):
        return "part_time"
    if re.search(r"\bcontract\b|\bfreelance\b|\bcontractor\b", t):
        return "contract"
    if re.search(r"\bintern(ship)?\b|\bco-?op\b", t):
        return "internship"
    return "full_time"


def _infer_remote_policy(text: str, remote_flag: bool) -> str:
    t = text.lower()
    if "hybrid" in t:
        return "hybrid"
    if (
        remote_flag
        or "remote" in t
        or "anywhere" in t
        or "worldwide" in t
        or "distributed" in t
        or "work from home" in t
        or "wfh" in t
    ):
        return "remote"
    if re.search(r"on[-\s]?site|in[-\s]?office", t):
        return "onsite"
    return ""


_TZ_TOKEN = re.compile(r"\b(GMT|UTC)\s*([+-]\d{1,2}(?::\d{2})?)\b", re.IGNORECASE)
_TZ_NAMED = re.compile(
    r"\b(PST|PDT|EST|EDT|CST|CDT|MST|MDT|CET|CEST|BST|IST|JST|WIB|WITA|WIT|SGT|AEST)\b",
    re.IGNORECASE,
)


def _infer_timezone(text: str) -> str:
    m = _TZ_TOKEN.search(text)
    if m:
        return f"{m.group(1).upper()}{m.group(2)}"
    m = _TZ_NAMED.search(text)
    return m.group(1).upper() if m else ""


def _parse_salary(text: str) -> tuple[str, str]:
    """Return (currency, salary_period) hints from a salary string."""
    if not text:
        return ("", "")
    t = text.lower()
    currency = ""
    if "$" in text or "usd" in t or "dollar" in t:
        currency = "USD"
    elif "€" in text or "eur" in t:
        currency = "EUR"
    elif "£" in text or "gbp" in t:
        currency = "GBP"
    elif "idr" in t or re.search(r"\brp\.?\s?\d", t):
        currency = "IDR"
    elif "sgd" in t or "s$" in t:
        currency = "SGD"
    period = ""
    if re.search(r"/\s*hr\b|/\s*hour\b|per\s*hour|hourly|/\s*h\b", t):
        period = "hour"
    elif re.search(r"/\s*yr\b|/\s*year\b|per\s*year|annual|annum|yearly", t):
        period = "year"
    elif "month" in t or "/mo" in t:
        period = "month"
    elif "week" in t or "/wk" in t:
        period = "week"
    elif "day" in t or "daily" in t:
        period = "day"
    return (currency, period)


_HOURS_WEEK = re.compile(
    r"(\d{1,2}(?:\.\d+)?)\s*(?:hours?|hrs?|h)\s*(?:/|per)?\s*(?:week|wk|w)\b",
    re.IGNORECASE,
)


def _infer_hours_per_week(text: str) -> float | None:
    m = _HOURS_WEEK.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _requirements(tags: list[str], description: str) -> str:
    tag_str = ", ".join(tags[:12])
    desc = (description or "").strip()
    if len(desc) > 240:
        desc = desc[:240].rsplit(" ", 1)[0] + "…"
    parts = [p for p in (tag_str, desc) if p]
    return " | ".join(parts)[:500]


def _normalize(platform: Platform, raw: dict[str, Any]) -> JobPosting:
    """Map a parser's raw job dict into a fully-populated JobPosting."""
    title = str(raw.get("title") or "").strip()
    company = str(raw.get("company") or "").strip()
    location = str(raw.get("location") or "").strip()
    description = _strip_html(raw.get("description") or "")
    url = str(raw.get("url") or "").strip()
    salary = raw.get("salary")
    salary = str(salary).strip() if salary not in (None, "") else ""
    tags = [str(t).strip() for t in (raw.get("tags") or []) if t]
    tags_text = " ".join(tags)
    explicit_et = str(raw.get("job_type") or "").strip()

    short_text = " ".join(
        [title, tags_text, explicit_et, location, salary, description[:300]]
    ).lower()
    employment_type = _infer_employment_type(short_text, explicit_et)
    remote_flag = bool(raw.get("remote"))
    remote_policy = _infer_remote_policy(short_text, remote_flag)
    timezone = _infer_timezone(short_text)
    cur, per = _parse_salary(salary) if salary else ("", "")
    currency = str(raw.get("currency") or cur or "")
    salary_period = str(raw.get("salary_period") or per or "")
    hours = _infer_hours_per_week(short_text)
    requirements = _requirements(tags, description)
    extra = dict(raw.get("extra") or {})

    return JobPosting(
        external_id=str(raw.get("external_id") or "").strip(),
        platform=platform,
        title=title,
        company=company,
        location=location,
        description=description,
        url=url,
        salary=salary or None,
        easy_apply=bool(raw.get("easy_apply")),
        remote=bool(remote_flag or remote_policy == "remote"),
        employment_type=employment_type,
        remote_policy=remote_policy,
        timezone=timezone,
        hours_per_week=hours,
        currency=currency,
        salary_period=salary_period,
        requirements=requirements,
        source=str(raw.get("source") or ""),
        extra=extra,
    )


# ---------------------------------------------------------------------------
# Per-feed parsers
# ---------------------------------------------------------------------------

Parser = Callable[
    [httpx.Client, FeedSpec, list[str], list[str], float], list[JobPosting]
]


def _board_url(spec: FeedSpec, template: str) -> str:
    """Return a full ATS endpoint from a board token or a full URL."""
    raw = (spec.url or "").strip()
    if raw.startswith(("http://", "https://")):
        return raw
    if not raw:
        raise ValueError(
            f"{spec.kind} board token missing (set FeedSpec.url to a board slug or full URL)"
        )
    return template.format(token=raw)


def _parse_remotive(
    client: httpx.Client,
    spec: FeedSpec,
    queries: list[str],
    locations: list[str],
    timeout: float,
) -> list[JobPosting]:
    url = spec.url or DEFAULT_REMOTIVE
    params = {"search": queries[0]} if queries and queries[0] else None
    resp = client.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    jobs = data.get("jobs") or [] if isinstance(data, dict) else []
    platform = _platform_for(spec)
    out: list[JobPosting] = []
    for j in jobs:
        if not isinstance(j, dict):
            continue
        eid = str(j.get("id") or j.get("url") or _hash(str(j.get("title") or "")))
        out.append(
            _normalize(
                platform,
                {
                    "external_id": eid,
                    "title": j.get("title") or "",
                    "company": j.get("company_name") or "",
                    "location": j.get("candidate_required_location") or "",
                    "description": j.get("description") or "",
                    "url": j.get("url") or "",
                    "salary": j.get("salary") or "",
                    "tags": j.get("tags") or [],
                    "job_type": j.get("job_type") or "",
                    "remote": True,
                    "source": spec.name,
                    "extra": {"publication_date": j.get("publication_date") or ""},
                },
            )
        )
    return out


def _parse_remoteok(
    client: httpx.Client,
    spec: FeedSpec,
    queries: list[str],
    locations: list[str],
    timeout: float,
) -> list[JobPosting]:
    url = spec.url or DEFAULT_REMOTEOK
    resp = client.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        data = data.get("data") or data.get("jobs") or []
    platform = _platform_for(spec)
    out: list[JobPosting] = []
    for j in data or []:
        if not isinstance(j, dict):
            continue
        title = j.get("position") or j.get("title")
        if not title:  # skip the leading "legal"/metadata object
            continue
        smin, smax = j.get("salary_min"), j.get("salary_max")
        salary = ""
        if smin is not None or smax is not None:
            lo = f"{smin:g}" if isinstance(smin, (int, float)) else str(smin or "")
            hi = f"{smax:g}" if isinstance(smax, (int, float)) else str(smax or "")
            salary = f"${lo}-{hi}" if lo and hi else f"${lo or hi}"
        eid = str(j.get("id") or j.get("slug") or j.get("url") or _hash(str(title)))
        out.append(
            _normalize(
                platform,
                {
                    "external_id": eid,
                    "title": title,
                    "company": j.get("company") or "",
                    "location": j.get("location") or "",
                    "description": j.get("description") or "",
                    "url": j.get("url") or j.get("apply_url") or "",
                    "salary": salary,
                    "tags": j.get("tags") or [],
                    "remote": True,
                    "currency": str(j.get("currency") or "USD"),
                    "salary_period": "year"
                    if (smin is not None or smax is not None)
                    else "",
                    "source": spec.name,
                    "extra": {"publication_date": j.get("date") or ""},
                },
            )
        )
    return out


def _parse_arbeitnow(
    client: httpx.Client,
    spec: FeedSpec,
    queries: list[str],
    locations: list[str],
    timeout: float,
) -> list[JobPosting]:
    url = spec.url or DEFAULT_ARBEITNOW
    resp = client.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    jobs = (
        (data.get("data") or data.get("jobs") or []) if isinstance(data, dict) else []
    )
    platform = _platform_for(spec)
    out: list[JobPosting] = []
    for j in jobs or []:
        if not isinstance(j, dict):
            continue
        tags = list(j.get("tags") or [])
        job_types = list(j.get("job_types") or [])
        eid = str(j.get("slug") or j.get("url") or _hash(str(j.get("title") or "")))
        out.append(
            _normalize(
                platform,
                {
                    "external_id": eid,
                    "title": j.get("title") or "",
                    "company": j.get("company_name") or "",
                    "location": j.get("location") or "",
                    "description": j.get("description") or "",
                    "url": j.get("url") or "",
                    "tags": tags + job_types,
                    "job_type": job_types[0] if job_types else "",
                    "remote": bool(j.get("remote")),
                    "source": spec.name,
                    "extra": {"created_at": j.get("created_at") or ""},
                },
            )
        )
    return out


def _parse_greenhouse(
    client: httpx.Client,
    spec: FeedSpec,
    queries: list[str],
    locations: list[str],
    timeout: float,
) -> list[JobPosting]:
    url = _board_url(
        spec, "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    )
    resp = client.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    jobs = data.get("jobs") or [] if isinstance(data, dict) else []
    platform = _platform_for(spec)
    out: list[JobPosting] = []
    for j in jobs:
        if not isinstance(j, dict):
            continue
        loc = j.get("location") or {}
        loc_name = loc.get("name") if isinstance(loc, dict) else str(loc)
        eid = str(
            j.get("id") or j.get("absolute_url") or _hash(str(j.get("title") or ""))
        )
        out.append(
            _normalize(
                platform,
                {
                    "external_id": eid,
                    "title": j.get("title") or "",
                    "company": spec.name,
                    "location": loc_name or "",
                    "description": j.get("content") or "",
                    "url": j.get("absolute_url") or "",
                    "source": spec.name,
                    "extra": {"updated_at": j.get("updated_at") or ""},
                },
            )
        )
    return out


def _parse_lever(
    client: httpx.Client,
    spec: FeedSpec,
    queries: list[str],
    locations: list[str],
    timeout: float,
) -> list[JobPosting]:
    url = _board_url(spec, "https://api.lever.co/v0/postings/{token}?mode=json")
    resp = client.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        data = data.get("postings") or data.get("data") or []
    platform = _platform_for(spec)
    out: list[JobPosting] = []
    for j in data or []:
        if not isinstance(j, dict):
            continue
        cats = j.get("categories") or {}
        cats = cats if isinstance(cats, dict) else {}
        eid = str(j.get("id") or j.get("hostedUrl") or _hash(str(j.get("text") or "")))
        out.append(
            _normalize(
                platform,
                {
                    "external_id": eid,
                    "title": j.get("text") or "",
                    "company": spec.name,
                    "location": cats.get("location") or "",
                    "description": j.get("description") or "",
                    "url": j.get("hostedUrl") or j.get("applyUrl") or "",
                    "job_type": cats.get("commitment") or "",
                    "source": spec.name,
                    "extra": {"created_at": j.get("createdAt") or ""},
                },
            )
        )
    return out


def _parse_ashby(
    client: httpx.Client,
    spec: FeedSpec,
    queries: list[str],
    locations: list[str],
    timeout: float,
) -> list[JobPosting]:
    url = _board_url(spec, "https://api.ashbyhq.com/posting-api/job-board/{token}")
    resp = client.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    jobs = data.get("jobs") or [] if isinstance(data, dict) else []
    platform = _platform_for(spec)
    out: list[JobPosting] = []
    for j in jobs:
        if not isinstance(j, dict):
            continue
        eid = str(
            j.get("jobUrl") or j.get("applyUrl") or _hash(str(j.get("title") or ""))
        )
        out.append(
            _normalize(
                platform,
                {
                    "external_id": eid,
                    "title": j.get("title") or "",
                    "company": spec.name,
                    "location": j.get("location") or "",
                    "description": j.get("descriptionPlain")
                    or j.get("descriptionHtml")
                    or "",
                    "url": j.get("jobUrl") or j.get("applyUrl") or "",
                    "job_type": j.get("employmentType") or "",
                    "remote": bool(j.get("isRemote")),
                    "source": spec.name,
                    "extra": {"published_at": j.get("publishedAt") or ""},
                },
            )
        )
    return out


_LI_LI = re.compile(r"<li\b[^>]*>(.*?)</li>", re.IGNORECASE | re.DOTALL)


def _re_first(pattern: str, block: str) -> str:
    m = re.search(pattern, block, re.IGNORECASE | re.DOTALL)
    return _clean(m.group(1)) if m else ""


def _li_job_id(link: str) -> str:
    if not link:
        return ""
    m = (
        re.search(r"/jobs/view/[^/?#]*?(\d{6,})", link)
        or re.search(r"currentJobId=(\d+)", link)
        or re.search(r"/jobs/view/(\d+)", link)
    )
    return m.group(1) if m else ""


def _parse_linkedin_guest(
    client: httpx.Client,
    spec: FeedSpec,
    queries: list[str],
    locations: list[str],
    timeout: float,
) -> list[JobPosting]:
    base = spec.url or DEFAULT_LINKEDIN_GUEST
    qs = [q for q in dict.fromkeys([q for q in (queries or []) if q])][:5] or [""]
    loc = (locations or [""])[0]
    platform = _platform_for(spec)
    out: list[JobPosting] = []
    seen: set[str] = set()
    for q in qs:
        params = {"keywords": q, "location": loc, "start": 0}
        resp = client.get(base, params=params, timeout=timeout)
        resp.raise_for_status()
        for block in _LI_LI.findall(resp.text or ""):
            link = _re_first(r'class="base-card__full-link"[^>]*href="([^"]+)"', block)
            title = _re_first(r'class="base-search-card__title"[^>]*>([^<]+)', block)
            if not title:
                title = _re_first(r"base-search-card__title[^>]*>\s*([^<]+)", block)
            company = _re_first(
                r"base-search-card__subtitle[^>]*>\s*(?:<a[^>]*>)?([^<]+)", block
            )
            location = _re_first(r'job-search-card__location"[^>]*>([^<]+)', block)
            date = _re_first(r'datetime="([^"]+)"', block)
            if not (title or link):
                continue
            eid = _li_job_id(link) or _hash(link or title)
            if eid in seen:
                continue
            seen.add(eid)
            if link.startswith("/"):
                link = "https://www.linkedin.com" + link
            out.append(
                _normalize(
                    platform,
                    {
                        "external_id": eid,
                        "title": title,
                        "company": company,
                        "location": location,
                        "description": "",
                        "url": link,
                        "source": spec.name,
                        "extra": {"publication_date": date or ""},
                    },
                )
            )
    return out


def _parse_muse(
    client: httpx.Client,
    spec: FeedSpec,
    queries: list[str],
    locations: list[str],
    timeout: float,
) -> list[JobPosting]:
    """Import-only source: Muse jobs arrive via ``job-agent muse import``.

    There is no endpoint to fetch, so discovery yields nothing here.
    Returning [] (instead of raising for an unknown kind) keeps a cron
    feed run green while the muse source stays visible in listings.
    """
    return []


_PARSERS: dict[str, Parser] = {
    "muse": _parse_muse,
    "remotive": _parse_remotive,
    "remoteok": _parse_remoteok,
    "arbeitnow": _parse_arbeitnow,
    "greenhouse": _parse_greenhouse,
    "lever": _parse_lever,
    "ashby": _parse_ashby,
    "linkedin_guest": _parse_linkedin_guest,
}

# ---------------------------------------------------------------------------
# Spec loading / defaults
# ---------------------------------------------------------------------------


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "y", "on"):
            return True
        if v in ("0", "false", "no", "n", "off"):
            return False
    return default


def default_feed_specs() -> list[FeedSpec]:
    """Built-in feed definitions (used when no YAML config is present)."""
    return [
        FeedSpec(
            name="remotive", kind="remotive", url=DEFAULT_REMOTIVE, platform="remotive"
        ),
        FeedSpec(
            name="remoteok", kind="remoteok", url=DEFAULT_REMOTEOK, platform="remoteok"
        ),
        FeedSpec(
            name="arbeitnow",
            kind="arbeitnow",
            url=DEFAULT_ARBEITNOW,
            platform="arbeitnow",
        ),
        FeedSpec(
            name="linkedin_guest",
            kind="linkedin_guest",
            url=DEFAULT_LINKEDIN_GUEST,
            platform="linkedin",
        ),
        # ATS boards require a real token; disabled until configured.
        FeedSpec(
            name="greenhouse",
            kind="greenhouse",
            url="",
            enabled=False,
            platform="greenhouse",
        ),
        FeedSpec(name="lever", kind="lever", url="", enabled=False, platform="lever"),
        FeedSpec(name="ashby", kind="ashby", url="", enabled=False, platform="ashby"),
    ]


def load_feed_specs(path: str | Path) -> list[FeedSpec]:
    """Load a ``feeds:`` list from YAML; tolerant of a missing/invalid file.

    * Missing file (or a file without a ``feeds`` key) -> :func:`default_feed_specs`.
    * A present but empty ``feeds: []`` -> ``[]`` (intentionally disables feeds).
    * Malformed entries are skipped rather than raising.
    """
    p = Path(path)
    if not p.exists():
        return default_feed_specs()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log.warning("feed_spec_load_failed", path=str(p), error=str(exc)[:200])
        return default_feed_specs()
    if not isinstance(data, dict) or data.get("feeds") is None:
        return default_feed_specs()
    specs: list[FeedSpec] = []
    for item in data.get("feeds") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if not kind:
            continue
        specs.append(
            FeedSpec(
                name=str(item.get("name") or kind).strip(),
                kind=kind,
                url=str(item.get("url") or "").strip(),
                enabled=_as_bool(item.get("enabled"), True),
                platform=str(item.get("platform") or "").strip(),
            )
        )
    return specs


# ---------------------------------------------------------------------------
# FeedHub
# ---------------------------------------------------------------------------


class FeedHub:
    """Concurrent structured-feed discovery hub."""

    def __init__(
        self,
        specs: list[FeedSpec] | None = None,
        *,
        timeout: float = 12.0,
        max_workers: int = 6,
    ) -> None:
        self.specs: list[FeedSpec] = (
            list(specs) if specs is not None else default_feed_specs()
        )
        self.timeout = float(timeout)
        self.max_workers = max(1, int(max_workers))
        self._results: list[FeedResult] = []
        self._client = httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

    def results(self) -> list[FeedResult]:
        """Diagnostics for the last :meth:`discover` call."""
        return list(self._results)

    def close(self) -> None:
        """Release the shared HTTP client (optional; safe to call twice)."""
        try:
            self._client.close()
        except Exception:
            pass

    def _run(
        self, spec: FeedSpec, queries: list[str], locations: list[str]
    ) -> FeedResult:
        start = time.monotonic()
        try:
            parser = _PARSERS.get(spec.kind)
            if parser is None:
                raise ValueError(f"unknown feed kind: {spec.kind!r}")
            items = parser(self._client, spec, queries, locations, self.timeout)
            for p in items:
                if not getattr(p, "source", ""):
                    p.source = spec.name
                if spec.platform:
                    p.platform = _resolve_platform_str(spec.platform)
            elapsed = int((time.monotonic() - start) * 1000)
            return FeedResult(source=spec.name, postings=items, elapsed_ms=elapsed)
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            msg = str(exc)[:300]
            log.warning("feed_failed", feed=spec.name, kind=spec.kind, error=msg[:200])
            return FeedResult(
                source=spec.name, postings=[], elapsed_ms=elapsed, error=msg
            )

    @staticmethod
    def _dedupe(postings: list[JobPosting]) -> list[JobPosting]:
        seen: set[tuple[str, str]] = set()
        out: list[JobPosting] = []
        for p in postings:
            plat = p.platform.value if hasattr(p.platform, "value") else str(p.platform)
            eid = (getattr(p, "external_id", "") or "").strip()
            key = (eid, plat) if eid else ("url:" + _hash(p.url or p.title or ""), plat)
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        return out

    def discover(
        self,
        queries: list[str],
        locations: list[str] | None = None,
        *,
        limit: int = 100,
        filters: dict | None = None,
    ) -> list[JobPosting]:
        """Discover postings from all enabled feeds concurrently.

        Always returns a list; a failing feed only produces a warning and is
        recorded in :meth:`results`. With no network, every feed fails and the
        return value is ``[]``.
        """
        self._results = []
        enabled = [s for s in self.specs if getattr(s, "enabled", True)]
        if not enabled:
            return []
        queries = [q for q in (queries or []) if q]
        locations = [loc for loc in (locations or []) if loc]

        collected: list[JobPosting] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = [
                ex.submit(self._run, spec, queries, locations) for spec in enabled
            ]
            for fut in as_completed(futures):
                result = fut.result()  # _run never raises
                self._results.append(result)
                collected.extend(result.postings)

        kept = apply_filters(collected, filters).kept
        kept = self._dedupe(kept)
        if limit and limit > 0:
            kept = kept[: int(limit)]
        return kept
