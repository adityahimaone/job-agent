"""Generic, config-driven ego(lite) board discovery.

The fast LinkedIn guest-API path lives in ``discovery.py``; everything else is
scraped with a single documented ``page.evaluate()`` call that runs
``document.querySelectorAll`` **inside the browser Page** (Node-side code has no
DOM). Extraction is defensive: a missing selector or field never raises, and
rows are deduplicated by URL. Selectors are best-effort defaults and can be
overridden per board in ``config/discovery_sources.yaml`` under ``ego_boards``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import yaml

from job_agent.db.models import Platform
from job_agent.platforms.base import JobPosting
from job_agent.utils.logging import get_logger

log = get_logger(__name__)

_FIELDS = (
    "platform",
    "search_url",
    "card_selector",
    "title_selector",
    "company_selector",
    "location_selector",
    "link_selector",
    "remote_hint",
)


@dataclass
class BoardSpec:
    """How to find job cards on one board."""

    name: str
    platform: str
    search_url: str  # contains {query} and {location} placeholders
    card_selector: str
    title_selector: str
    company_selector: str
    location_selector: str
    link_selector: str
    remote_hint: str = ""


def default_board_specs() -> dict[str, BoardSpec]:
    """Sensible best-effort specs for the supported boards."""
    return {
        "linkedin": BoardSpec(
            name="linkedin",
            platform="linkedin",
            search_url="https://www.linkedin.com/jobs/search/?keywords={query}&location={location}",
            card_selector="div.base-card, li",
            title_selector="h3.base-search-card__title, h3",
            company_selector="h4.base-search-card__subtitle, h4",
            location_selector="span.job-search-card__location, span",
            link_selector="a.base-card__full-link, a[href*='/jobs/view/']",
        ),
        "wellfound": BoardSpec(
            name="wellfound",
            platform="wellfound",
            search_url="https://wellfound.com/jobs?query={query}&location={location}",
            card_selector="a[href*='/jobs/'], div[data-test='JobCard'], article, li",
            title_selector="h2, h3, [class*='title']",
            company_selector="[class*='company'], h4, span",
            location_selector="[class*='location'], [class*='Location']",
            link_selector="a[href*='/jobs/']",
            remote_hint="remote",
        ),
        "jobstreet": BoardSpec(
            name="jobstreet",
            platform="jobstreet",
            search_url="https://www.jobstreet.com/id/en/search-jobs?keywords={query}&location={location}",
            card_selector="article, [data-automation='job-card'], div[class*='job-card']",
            title_selector="h1, h2, h3, [data-automation='jobTitle']",
            company_selector="[data-automation='jobCompany'], [class*='company'], span",
            location_selector="[data-automation='jobLocation'], [class*='location']",
            link_selector="a[href*='/job/'], a[href*='jobstreet']",
            remote_hint="remote",
        ),
        "glints": BoardSpec(
            name="glints",
            platform="glints",
            search_url="https://glints.com/id/opportunities/job_explorer?keywords={query}&location={location}",
            card_selector="a[href*='/opportunities/'], article, div[class*='JobCard']",
            title_selector="h2, h3, [class*='title']",
            company_selector="[class*='company'], span, p",
            location_selector="[class*='location'], [class*='Location']",
            link_selector="a[href*='/opportunities/']",
            remote_hint="remote",
        ),
        "kalibrr": BoardSpec(
            name="kalibrr",
            platform="kalibrr",
            search_url="https://www.kalibrr.com/job-board/te?q={query}&l={location}",
            card_selector="a[href*='/c/'], div[class*='job'], article, li",
            title_selector="h2, h3, [class*='title']",
            company_selector="[class*='company'], span",
            location_selector="[class*='location'], [class*='Location']",
            link_selector="a[href*='/c/'], a[href*='/jobs/']",
            remote_hint="remote",
        ),
        "seek": BoardSpec(
            name="seek",
            platform="seek",
            search_url="https://www.seek.com.au/jobs?keywords={query}&where={location}",
            card_selector="article, [data-automation='normalJob'], div[class*='job']",
            title_selector="h1, h2, h3, [data-automation='jobTitle']",
            company_selector="[data-automation='jobCompany'], [class*='company'], span",
            location_selector="[data-automation='jobLocation'], [class*='location']",
            link_selector="a[href*='/job/'], a[href*='seek.com.au']",
            remote_hint="remote",
        ),
    }


def _spec_from_dict(name: str, data: dict, base: BoardSpec | None) -> BoardSpec | None:
    """Build a BoardSpec from a YAML dict, falling back to *base* defaults."""

    def get(field: str) -> str:
        value = data.get(field)
        if value is None or value == "":
            value = getattr(base, field, "") if base is not None else ""
        return str(value) if value is not None else ""

    spec = BoardSpec(
        name=name,
        platform=get("platform") or name,
        search_url=get("search_url"),
        card_selector=get("card_selector"),
        title_selector=get("title_selector"),
        company_selector=get("company_selector"),
        location_selector=get("location_selector"),
        link_selector=get("link_selector"),
        remote_hint=get("remote_hint"),
    )
    if not spec.search_url or not spec.card_selector:
        return None
    return spec


def load_board_specs(path: str | Path) -> dict[str, BoardSpec]:
    """Return built-in specs merged with optional ``ego_boards`` overrides.

    The built-in boards are always present, so a shared/foreign YAML (e.g. one
    written by the feeds feature) that has no ``ego_boards`` key is harmless.
    Malformed files/entries are ignored rather than raising.
    """
    specs = default_board_specs()
    try:
        p = Path(path)
        if not p.is_file():
            return specs
        with open(p, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001 - config must never break discovery
        log.warning("ego_board_config_unreadable", path=str(path), error=str(exc)[:200])
        return specs

    if not isinstance(data, dict):
        return specs
    boards = data.get("ego_boards")

    items: list[dict] = []
    if isinstance(boards, dict):
        for key, value in boards.items():
            if isinstance(value, dict):
                entry = dict(value)
                entry.setdefault("name", key)
                items.append(entry)
    elif isinstance(boards, list):
        items = [dict(d) for d in boards if isinstance(d, dict)]
    else:
        return specs

    for entry in items:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        spec = _spec_from_dict(name, entry, specs.get(name))
        if spec is not None:
            specs[name] = spec
    return specs


# One JS expression (a function) reused by the single and batched scripts. It
# runs inside the Page, so DOM APIs are available; Node-side globals are not.
_ROW_EXTRACTOR = r"""async (cfg) => {
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const pick = (card, sel) => {
    if (!sel) return "";
    try {
      const e = card.querySelector(sel);
      return e ? clean(e.innerText || e.textContent || "") : "";
    } catch (err) { return ""; }
  };
  const linkOf = (card) => {
    try {
      const e = (cfg.link && card.querySelector(cfg.link)) ||
        card.querySelector("a[href]");
      if (!e) return "";
      const h = e.getAttribute("href") || "";
      if (!h) return e.href || "";
      return new URL(h, document.baseURI).href;
    } catch (err) { return h || ""; }
  };
  let cards = [];
  try { cards = [...document.querySelectorAll(cfg.card)]; }
  catch (err) { cards = []; }
  return cards.slice(0, cfg.limit).map((card) => ({
    id: "",
    url: linkOf(card),
    title: pick(card, cfg.title),
    company: pick(card, cfg.company),
    location: pick(card, cfg.location),
    description: clean(card.innerText || card.textContent || "").slice(0, 2000),
  }));
}"""


def build_search_url(spec: BoardSpec, query: str, location: str) -> str:
    """Substitute (URL-encoded) query/location into a board's search_url."""
    return spec.search_url.replace("{query}", quote(str(query or ""))).replace(
        "{location}", quote(str(location or ""))
    )


def _selectors(spec: BoardSpec, limit: int) -> dict:
    return {
        "card": spec.card_selector,
        "title": spec.title_selector,
        "company": spec.company_selector,
        "location": spec.location_selector,
        "link": spec.link_selector,
        "limit": int(limit),
    }


def build_board_script(
    spec: BoardSpec,
    *,
    query: str,
    location: str,
    limit: int,
    settle_ms: int,
    space: str = "job-agent",
) -> str:
    """Build a complete standalone script for one board search.

    Uses ``page.evaluate`` + ``document.querySelectorAll`` in the browser Page
    (NOT Node-side DOM). All string inputs are JSON-encoded for injection safety.
    ``space`` is an optional keyword so the caller can reuse its task space.
    """
    cfg = {
        "space": space,
        "url": build_search_url(spec, query, location),
        "settle": int(settle_ms),
        "sel": _selectors(spec, limit),
    }
    return f"""
const cfg = {json.dumps(cfg)};
const task = await taskSpace(cfg.space);
const page = task.page("p1");
await page.goto(cfg.url);
await new Promise((r) => setTimeout(r, cfg.settle));
const rows = await page.evaluate({_ROW_EXTRACTOR}, cfg.sel);
console.log("__RESULT__" + JSON.stringify({{ rows }}));
"""


def build_board_batch_script(
    spec: BoardSpec,
    *,
    combos: list[tuple[str, str]],
    limit: int,
    settle_ms: int,
    space: str = "job-agent",
) -> str:
    """One script, several (query, location) searches — fewer process spawns."""
    urls = [build_search_url(spec, q, loc) for q, loc in combos]
    cfg = {
        "space": space,
        "urls": urls,
        "settle": int(settle_ms),
        "limit": int(limit),
        "sel": _selectors(spec, limit),
    }
    return f"""
const cfg = {json.dumps(cfg)};
const task = await taskSpace(cfg.space);
const page = task.page("p1");
const rows = [];
for (const url of cfg.urls) {{
  if (rows.length >= cfg.limit) break;
  await page.goto(url);
  await new Promise((r) => setTimeout(r, cfg.settle));
  const batch = await page.evaluate({_ROW_EXTRACTOR}, cfg.sel);
  for (const row of batch) {{
    rows.push(row);
    if (rows.length >= cfg.limit) break;
  }}
}}
console.log("__RESULT__" + JSON.stringify({{ rows: rows.slice(0, cfg.limit) }}));
"""


def _to_platform(name: str) -> Platform:
    try:
        return Platform(str(name).lower())
    except (ValueError, KeyError):
        return Platform.OTHER


def parse_board_rows(payload: dict, spec: BoardSpec) -> list[JobPosting]:
    """Convert extracted rows to JobPostings. Defensive; never raises on gaps."""
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    platform = _to_platform(spec.platform)
    hint = (spec.remote_hint or "").lower()
    seen: set[str] = set()
    postings: list[JobPosting] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        key = url.split("#")[0].rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        title = str(row.get("title") or "").strip()
        company = str(row.get("company") or "").strip()
        location = str(row.get("location") or "").strip()
        description = str(row.get("description") or "").strip()
        blob = f"{title} {location} {description}".lower()
        remote = (bool(hint) and hint in blob) or "remote" in blob
        external_id = (
            str(row.get("id") or "").strip()
            or hashlib.sha1(url.encode()).hexdigest()[:16]
        )
        postings.append(
            JobPosting(
                external_id=external_id,
                platform=platform,
                title=title,
                company=company,
                location=location,
                url=url,
                description=description,
                remote=remote,
                source=f"ego:{spec.name}",
                extra={"board": spec.name},
            )
        )
    return postings
