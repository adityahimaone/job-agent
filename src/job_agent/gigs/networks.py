"""AI-training / expert-network gig sources (Alignerr-style lane).

.. important::

   **Gigs are a different workflow from job applications.** Onboarding is
   ``account + assessment`` (create an account, pass a qualification/assessment,
   then get activated on a project). There is **no** resume, cover letter,
   easy-apply or ATS form to send. Pay is hourly / per-task, availability is
   country-dependent, and you should NEVER send a CV or cover letter to a gig
   network.

This module only *discovers* listings. :mod:`job_agent.gigs.service` handles
eligibility, idempotent persistence and spreadsheet rows.

Currently the only live public source is **Alignerr** (https://www.alignerr.com/jobs).
Outlier, DataAnnotation and Mercor are deliberate stubs: they have no stable
public feed and need manual account configuration, so they return ``[]`` with a
warning rather than inventing openings.
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from job_agent.utils.logging import get_logger

log = get_logger(__name__)

_USER_AGENT = "job-agent/0.1 (gig-discovery; polite client)"
ALIGNERR_URL = "https://www.alignerr.com/jobs"

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


@dataclass
class GigListing:
    external_id: str
    network: str
    title: str
    track: str = ""
    description: str = ""
    url: str = ""
    location: str = "Remote"
    remote: bool = True
    pay_min: float | None = None
    pay_max: float | None = None
    currency: str = "USD"
    pay_period: str = "hour"
    employment_type: str = "part_time"
    eligibility: str = ""


@runtime_checkable
class GigNetwork(Protocol):
    """A source of AI-training gig listings."""

    name: str

    def fetch(self, *, timeout: float = 12.0) -> list[GigListing]: ...


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_JOB_LINK = re.compile(r"/jobs/(" + _UUID + r")", re.IGNORECASE)

_PAY_RANGE = re.compile(
    r"\$\s*(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)\s*"
    r"(?:/\s*(?:hr|hour|h)\b|\s*(?:per|/)\s*(?:hr|hour)\b)?",
    re.IGNORECASE,
)
_PAY_SINGLE = re.compile(r"\$\s*(\d+(?:\.\d+)?)\s*/\s*(?:hr|hour|h)\b", re.IGNORECASE)

_CODING = (
    "developer",
    "software",
    "coding",
    "engineer",
    "frontend",
    "front-end",
    "backend",
    "back-end",
    "fullstack",
    "full stack",
    "devops",
    "data scientist",
    "machine learning",
    "ml engineer",
    "data engineer",
    "code",
    "coder",
    "react",
    "typescript",
    "javascript",
    "web developer",
    "ui engineer",
    "design system",
)
_AUDIO = (
    "voice",
    "audio",
    "transcription",
    "transcribe",
    "speech",
    "linguist",
    "linguistic",
    "translator",
    "translation",
    "transcript",
    "pronunciation",
    "accent",
    "dubbing",
    "narration",
)

_BLOCK_TAG = re.compile(
    r"</?(?:p|div|li|h[1-6]|br|section|article|span|a|tr|td|ul|ol|header|footer)\b[^>]*>",
    re.IGNORECASE,
)
_HEADING = re.compile(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", re.IGNORECASE | re.DOTALL)

_TITLE_KEYS = ("title", "name", "position", "role", "jobTitle", "heading")
_LINK_KEYS = ("url", "applyUrl", "jobUrl", "link", "path", "slug", "@id", "apply_url")
_DESC_KEYS = (
    "description",
    "summary",
    "snippet",
    "content",
    "descriptionPlain",
    "text",
)


def _hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", "ignore")).hexdigest()[:16]


def _to_text(fragment: Any) -> str:
    """Coerce an HTML fragment into readable, line-separated plain text."""
    if not fragment:
        return ""
    s = _BLOCK_TAG.sub("\n", str(fragment))
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def _classify_track(title: str) -> str:
    """``coding`` for dev/software/coding/engineer, ``audio`` for voice/audio,
    ``general`` otherwise."""
    t = (title or "").lower()
    if any(k in t for k in _CODING):
        return "coding"
    if any(k in t for k in _AUDIO):
        return "audio"
    return "general"


def _pay_from_text(text: str) -> tuple[float | None, float | None]:
    m = _PAY_RANGE.search(text)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = _PAY_SINGLE.search(text)
    if m:
        v = float(m.group(1))
        return (v, v)
    return (None, None)


def _is_noise_line(line: str) -> bool:
    low = line.strip().lower()
    if len(low) < 5:
        return True
    if low in (
        "remote",
        "remote - worldwide",
        "part-time",
        "full-time",
        "apply now",
        "view job",
    ):
        return True
    if _PAY_RANGE.search(line) or _PAY_SINGLE.search(line):
        return True
    if not re.search(r"[a-zA-Z]", line):
        return True
    return False


def _pick_title(lines: list[str]) -> str:
    for line in lines:
        if not _is_noise_line(line):
            return line.strip()[:200]
    return ""


def _extract_title(fragment: str) -> str:
    """Prefer an explicit heading tag, else the first plausible text line."""
    headings = [_to_text(m.group(1)) for m in _HEADING.finditer(fragment)]
    title = _pick_title(headings)
    if title:
        return title
    return _pick_title(_to_text(fragment).splitlines())


def _external_id(title: str, url: str) -> str:
    m = _JOB_LINK.search(url or "")
    if m:
        return m.group(1)
    key = re.sub(r"\W+", " ", (title or "").lower()).strip()
    return _hash(key) if key else _hash(url or "gig")


def _absolute_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("/"):
        return "https://www.alignerr.com" + url
    return url


def _snippet(plain: str, limit: int = 240) -> str:
    one_line = re.sub(r"\s+", " ", plain).strip()
    if len(one_line) > limit:
        one_line = one_line[:limit].rsplit(" ", 1)[0] + "…"
    return one_line


def _make_listing(
    *,
    title: str,
    url: str,
    text: str,
    remote: bool = True,
    pay_min: float | None = None,
    pay_max: float | None = None,
    currency: str = "USD",
    pay_period: str = "hour",
) -> GigListing:
    remote = bool(remote or "remote" in (text or "").lower())
    if pay_min is None and pay_max is None:
        pay_min, pay_max = _pay_from_text(text)
    return GigListing(
        external_id=_external_id(title, url),
        network="alignerr",
        title=title[:200],
        track=_classify_track(title),
        description=_snippet(_to_text(text)),
        url=_absolute_url(url),
        location="Remote" if remote else "",
        remote=remote,
        pay_min=pay_min,
        pay_max=pay_max,
        currency=currency or "USD",
        pay_period=pay_period or "hour",
        employment_type="part_time",
        eligibility="Remote" if remote else "",
    )


# ---------------------------------------------------------------------------
# HTML anchor strategy
# ---------------------------------------------------------------------------

_ANCHOR = re.compile(
    r'<a\b[^>]*href="([^"]*/jobs/[^"#?]+[^"]*)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def _gig_from_anchor(
    href: str, inner_html: str, context_html: str
) -> GigListing | None:
    inner_text = _to_text(inner_html)
    context_text = _to_text(context_html)
    title = _extract_title(inner_html) or _extract_title(context_html)
    if not title:
        return None
    combined = inner_text + "\n" + context_text
    return _make_listing(
        title=title,
        url=href,
        text=combined,
        remote="remote" in combined.lower(),
    )


# ---------------------------------------------------------------------------
# JSON (JSON-LD / __NEXT_DATA__ / generic script) strategy
# ---------------------------------------------------------------------------

_JSONLD = re.compile(
    r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_NAMED_JSON = re.compile(
    r'<script\b[^>]*(?:id=["\']__NEXT_DATA__["\']|type=["\']application/json["\'])[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_ALL_SCRIPTS = re.compile(r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)


def _iter_json_blobs(html_text: str) -> list[Any]:
    blobs: list[Any] = []
    seen_raw: set[str] = set()
    candidates: list[str] = []
    for pattern in (_JSONLD, _NAMED_JSON):
        for m in pattern.finditer(html_text):
            candidates.append(m.group(1).strip())
    # Generic fallback: script bodies that look like JSON and mention jobs.
    for m in _ALL_SCRIPTS.finditer(html_text):
        raw = m.group(1).strip()
        if raw and raw[0] in "{[" and "job" in raw.lower():
            candidates.append(raw)
    for raw in candidates:
        if not raw or raw in seen_raw:
            continue
        seen_raw.add(raw)
        try:
            blobs.append(json.loads(raw))
        except Exception:
            continue
    return blobs


def _iter_job_objects(node: Any, budget: list[int] | None = None) -> Any:
    """Recursively yield dicts that look like a job card (bounded)."""
    if budget is None:
        budget = [0]
    budget[0] += 1
    if budget[0] > 4000:
        return
    if isinstance(node, dict):
        if any(k in node for k in _TITLE_KEYS):
            yield node
        for value in node.values():
            yield from _iter_job_objects(value, budget)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_job_objects(value, budget)


def _first_str(obj: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _pay_from_json(obj: dict) -> tuple[float | None, float | None, str, str]:
    """Return (min, max, currency, period) from structured or stringified pay."""
    currency, period = "USD", "hour"
    for key in ("baseSalary", "compensation", "salary", "pay", "payRange"):
        c = obj.get(key)
        if isinstance(c, dict):
            # schema.org MonetaryAmount nests the numbers under "value".
            pay = dict(c)
            nested = c.get("value")
            if isinstance(nested, dict):
                pay.update(nested)
            if pay.get("currency"):
                currency = str(pay["currency"])
            unit = str(pay.get("unitText") or pay.get("pay_period") or "").lower()
            if "hour" in unit or "hr" in unit:
                period = "hour"
            elif "year" in unit or "annual" in unit:
                period = "year"
            elif "month" in unit:
                period = "month"
            elif "day" in unit:
                period = "day"
            lo = pay.get("minValue") or pay.get("pay_min") or pay.get("min")
            hi = pay.get("maxValue") or pay.get("pay_max") or pay.get("max")
            try:
                lo_f = float(lo) if lo is not None else None
                hi_f = float(hi) if hi is not None else None
            except (TypeError, ValueError):
                lo_f = hi_f = None
            if lo_f is not None or hi_f is not None:
                lo_f = lo_f if lo_f is not None else hi_f
                hi_f = hi_f if hi_f is not None else lo_f
                return (lo_f, hi_f, currency, period)
        elif isinstance(c, str):
            pmin, pmax = _pay_from_text(c)
            if pmin is not None:
                return (pmin, pmax, currency, period)
    pmin, pmax = _pay_from_text(json.dumps(obj, ensure_ascii=False))
    return (pmin, pmax, currency, period)


def _gig_from_json(obj: Any) -> GigListing | None:
    if not isinstance(obj, dict):
        return None
    # Skip non-job schema objects (Org / WebSite / WebPage ...).
    if str(obj.get("@type") or "").lower() in (
        "organization",
        "website",
        "webpage",
        "breadcrumblist",
    ):
        return None
    title = _first_str(obj, _TITLE_KEYS)
    if len(title) < 4:
        return None
    url = _first_str(obj, _LINK_KEYS)
    desc = _first_str(obj, _DESC_KEYS)
    pmin, pmax, currency, period = _pay_from_json(obj)
    blob = json.dumps(obj, ensure_ascii=False)
    has_job_link = bool(_JOB_LINK.search(blob))
    remote = (
        bool(obj.get("remote"))
        or bool(obj.get("isRemote"))
        or "telecommute" in blob.lower()
        or "remote" in blob.lower()
    )
    # Guard against arbitrary JSON objects that merely have a "name" key.
    if not (has_job_link or pmin is not None or (remote and desc)):
        return None
    text = f"{title}\n{desc}"
    if pmin is not None:
        text += f"\n${pmin:g}-{pmax:g}/hr" if pmax is not None else f"\n${pmin:g}/hr"
    return _make_listing(
        title=title,
        url=url,
        text=text,
        remote=remote,
        pay_min=pmin,
        pay_max=pmax,
        currency=currency,
        pay_period=period,
    )


# ---------------------------------------------------------------------------
# Public parser
# ---------------------------------------------------------------------------


def parse_alignerr_html(html: str) -> list[GigListing]:
    """Defensively extract :class:`GigListing` role cards from Alignerr HTML.

    Tries, in order:

    1. HTML anchors whose ``href`` matches ``/jobs/<uuid>`` (or any
       ``/jobs/<slug>``), reading the card title / pay / "Remote" / snippet
       from the anchor and its surrounding markup.
    2. JSON-LD, ``__NEXT_DATA__`` and other JSON ``<script>`` blobs, walked
       recursively for job-like objects.
    3. A last-resort regex over ``$NN-NN/hr`` pay ranges, reading the nearby
       text for a title.

    All numbers (role count, rates) are parsed, never hardcoded. Returns ``[]``
    (never raises) when nothing can be extracted.
    """
    text = html or ""
    listings: list[GigListing] = []
    seen: set[str] = set()

    def add(gig: GigListing | None) -> None:
        if gig is None:
            return
        if gig.external_id in seen:
            return
        seen.add(gig.external_id)
        listings.append(gig)

    try:
        # 1) Anchors matching /jobs/<...>
        for m in _ANCHOR.finditer(text):
            href, inner = m.group(1), m.group(2)
            context = text[max(0, m.start() - 500) : min(len(text), m.end() + 500)]
            add(_gig_from_anchor(href, inner, context))

        # 2) JSON blobs
        for blob in _iter_json_blobs(text):
            for obj in _iter_job_objects(blob):
                add(_gig_from_json(obj))

        # 3) Pay-range regex fallback (only when nothing else produced cards)
        if not listings:
            for m in _PAY_RANGE.finditer(text):
                window = text[max(0, m.start() - 500) : min(len(text), m.end() + 300)]
                title = _extract_title(window)
                if not title:
                    continue
                add(_make_listing(title=title, url="", text=_to_text(window)))
    except Exception as exc:  # defensive: never raise from a parse
        log.warning("alignerr_parse_error", error=str(exc)[:200])

    if not listings:
        log.warning("alignerr_parse_no_roles", html_len=len(text))
    return listings


# ---------------------------------------------------------------------------
# Networks
# ---------------------------------------------------------------------------


class AlignerrNetwork:
    """Live public source: https://www.alignerr.com/jobs."""

    name = "alignerr"

    def fetch(self, *, timeout: float = 12.0) -> list[GigListing]:
        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                },
            ) as client:
                resp = client.get(ALIGNERR_URL)
                resp.raise_for_status()
                listings = parse_alignerr_html(resp.text)
            if not listings:
                log.warning("alignerr_no_listings", url=ALIGNERR_URL)
            return listings
        except Exception as exc:
            log.warning("alignerr_fetch_failed", url=ALIGNERR_URL, error=str(exc)[:200])
            return []


class _ManualConfigNetwork:
    """Base stub: no public feed, needs manual account/assessment setup."""

    name = "manual"

    def fetch(self, *, timeout: float = 12.0) -> list[GigListing]:
        log.warning(
            "gig_network_manual_config_required",
            network=self.name,
            hint="no public feed configured; create an account + complete the assessment manually",
        )
        return []


class OutlierNetwork(_ManualConfigNetwork):
    name = "outlier"


class DataAnnotationNetwork(_ManualConfigNetwork):
    name = "dataannotation"


class MercorNetwork(_ManualConfigNetwork):
    name = "mercor"


class _UnknownNetwork(_ManualConfigNetwork):
    def __init__(self, name: str) -> None:
        self.name = name or "unknown"


_NETWORKS: dict[str, type[GigNetwork]] = {
    "alignerr": AlignerrNetwork,
    "outlier": OutlierNetwork,
    "dataannotation": DataAnnotationNetwork,
    "data_annotation": DataAnnotationNetwork,
    "mercor": MercorNetwork,
}


def get_network(name: str) -> GigNetwork:
    """Return a :class:`GigNetwork` for ``name`` (never raises).

    Unknown names fall back to a stub that logs a manual-configuration warning
    and returns ``[]``.
    """
    key = (name or "").strip().lower().replace(" ", "_").replace(".", "_")
    cls = _NETWORKS.get(key)
    if cls is not None:
        return cls()
    log.warning("gig_network_unknown", network=name)
    return _UnknownNetwork(key)
