"""Human-supervised job-board definitions for the Ego Lite lane.

.. important::

   **This module never performs any scraping or network I/O.** The
   :class:`BoardSpec` entries below are *URL templates + CSS selector hints*
   consumed by the human-supervised Ego Lite browser lane (signed-in,
   human-in-the-loop), not by an automated scraper. Boards that require login,
   CAPTCHA, or region-specific rendering are driven by a real person via Ego
   Lite; the fast structured-feed lane lives in
   :mod:`job_agent.discovery.feeds`.

Each ``search_url`` contains ``{query}`` and ``{location}`` placeholders that
the caller fills before navigating. Where a site supports remote / part-time
filters, the URL already carries the relevant flag (e.g. LinkedIn ``f_WT=2``
for remote and ``f_JT=P`` for part-time). The accompanying
``config/discovery_sources.yaml`` (or ``config/feeds.yaml``) documents how to
toggle those flags and how to add a new board.

The selectors are *starting points* for the Ego lane's DOM inspection; they
should be re-verified against the live site when a board's markup changes.
"""

from __future__ import annotations

import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_agent.utils.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public data structure
# ---------------------------------------------------------------------------


@dataclass
class BoardSpec:
    """A job board navigated (not scraped) by the Ego Lite browser lane."""

    name: str
    platform: str
    search_url: str  # must contain {query} and {location}
    card_selector: str
    title_selector: str
    company_selector: str
    location_selector: str
    link_selector: str
    enabled: bool = True


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def default_board_specs() -> list[BoardSpec]:
    """Built-in boards, including SEA (JobStreet / Glints / Kalibrr / Seek).

    URL notes (remote / part-time friendly flags are already included where the
    site supports them):

    * LinkedIn  ``f_WT=2`` = Remote, ``f_JT=P`` = Part-time.
    * Wellfound ``remote=true``.
    * JobStreet ``workFromHome=true`` (id + global variants).
    * Glints    ``workArrangement=REMOTE``.
    * Kalibrr   ``remote=true``.
    * Seek      ``worktype=parttime``.
    * RemoteOK / We Work Remotely are remote-only by definition.
    * Otta      ``remote=true`` + ``employmentType=part_time``.
    """
    return [
        BoardSpec(
            name="LinkedIn",
            platform="linkedin",
            search_url="https://www.linkedin.com/jobs/search?keywords={query}&location={location}&f_WT=2&f_JT=P",
            card_selector=".base-card, li.jobs-search-results__list-item",
            title_selector=".base-search-card__title, .job-card-list__title",
            company_selector=".base-search-card__subtitle, .job-card-container__company-name",
            location_selector=".job-search-card__location, .job-card-container__metadata-item",
            link_selector="a.base-card__full-link, a.job-card-list__title",
        ),
        BoardSpec(
            name="Wellfound",
            platform="wellfound",
            search_url="https://wellfound.com/jobs?query={query}&location={location}&remote=true",
            card_selector="[data-test='JobCard'], .job-listing, a[href*='/jobs/']",
            title_selector="h2, h3, .job-title",
            company_selector=".company-name, [data-test='CompanyName'], .company",
            location_selector=".location, .job-location",
            link_selector="a[href*='/jobs/']",
        ),
        BoardSpec(
            name="JobStreet Indonesia",
            platform="jobstreet",
            search_url="https://id.jobstreet.com/id/{query}-jobs/in-{location}?workFromHome=true",
            card_selector="article[data-testid='job-card'], div[data-testid='job-card'], .job-card",
            title_selector="[data-testid='job-card-title'], h2, .job-title",
            company_selector="[data-testid='job-card-company'], .company-name",
            location_selector="[data-testid='job-card-location'], .job-location",
            link_selector="a[data-testid='job-card-title'], a[href*='/job/']",
        ),
        BoardSpec(
            name="JobStreet",
            platform="jobstreet",
            search_url="https://www.jobstreet.com/{query}-jobs/in-{location}?workFromHome=true",
            card_selector="article[data-testid='job-card'], div[data-testid='job-card'], .job-card",
            title_selector="[data-testid='job-card-title'], h2, .job-title",
            company_selector="[data-testid='job-card-company'], .company-name",
            location_selector="[data-testid='job-card-location'], .job-location",
            link_selector="a[data-testid='job-card-title'], a[href*='/job/']",
        ),
        BoardSpec(
            name="Glints",
            platform="glints",
            search_url="https://glints.com/id/opportunities/job_explorer?keywords={query}&location={location}&workArrangement=REMOTE",
            card_selector="[data-testid='job-card'], .JobCard, a[href*='/opportunities/jobs/']",
            title_selector="h2, h3, .job-title, [data-testid='job-title']",
            company_selector=".company-name, [data-testid='company-name'], .CompanyName",
            location_selector=".location, .job-location, [data-testid='job-location']",
            link_selector="a[href*='/opportunities/jobs/']",
        ),
        BoardSpec(
            name="Kalibrr",
            platform="kalibrr",
            search_url="https://www.kalibrr.com/id-ID/job-board?text={query}&location={location}&remote=true",
            card_selector=".job-card, .k-job-card, a[href*='/c/']",
            title_selector="h2, h3, .job-title, .k-job-card__title",
            company_selector=".company-name, .k-job-card__company, .company",
            location_selector=".location, .k-job-card__location",
            link_selector="a[href*='/c/'], a.job-card__link",
        ),
        BoardSpec(
            name="Seek",
            platform="seek",
            search_url="https://www.seek.com.au/{query}-jobs/in-{location}?worktype=parttime",
            card_selector="article[data-testid='job-card'], div[data-testid='job-card'], .job-card",
            title_selector="[data-testid='job-card-title'], h2, a[data-automation='jobTitle']",
            company_selector="[data-testid='job-card-company'], a[data-automation='jobCompany']",
            location_selector="[data-testid='job-card-location'], .job-location",
            link_selector="a[data-automation='jobTitle'], a[href*='/job/']",
        ),
        BoardSpec(
            name="RemoteOK",
            platform="remoteok",
            search_url="https://remoteok.com/?tags={query}&location={location}&remote=true",
            card_selector="tr.job, .job, li.job",
            title_selector="h2, .job-title, [itemprop='title']",
            company_selector=".company, [itemprop='hiringOrganization'], .company-name",
            location_selector=".location, .job-location",
            link_selector="a[href*='/remote-jobs/'], a.job-link",
        ),
        BoardSpec(
            name="We Work Remotely",
            platform="other",
            search_url="https://weworkremotely.com/remote-jobs/search?term={query}&location={location}",
            card_selector="li.feature, .jobs li, article.job",
            title_selector=".title, h2, h3, span.title",
            company_selector=".company, .company-name",
            location_selector=".region, .location, .company",
            link_selector="a[href*='/remote-jobs/']",
        ),
        BoardSpec(
            name="Otta",
            platform="other",
            search_url="https://app.otta.com/jobs/search?query={query}&location={location}&remote=true&employmentType=part_time",
            card_selector="[data-testid='job-card'], .job-card, a[href*='/jobs/']",
            title_selector="h2, h3, .job-title",
            company_selector=".company-name, [data-testid='company-name']",
            location_selector=".location, .job-location",
            link_selector="a[href*='/jobs/']",
        ),
    ]


# ---------------------------------------------------------------------------
# Loading
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


def load_board_specs(path: str | Path) -> list[BoardSpec]:
    """Load a ``boards:`` list from YAML; tolerant of a missing/invalid file.

    * Missing file (or a file without a ``boards`` key) -> :func:`default_board_specs`.
    * A present but empty ``boards: []`` -> ``[]``.
    * Entries with missing ``name``/``platform`` or missing placeholders are
      skipped with a warning rather than raising.
    """
    p = Path(path)
    if not p.exists():
        return default_board_specs()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log.warning("board_spec_load_failed", path=str(p), error=str(exc)[:200])
        return default_board_specs()
    if not isinstance(data, dict) or data.get("boards") is None:
        return default_board_specs()
    specs: list[BoardSpec] = []
    for item in data.get("boards") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        platform = str(item.get("platform") or "").strip()
        search_url = str(item.get("search_url") or "").strip()
        if not name or not platform or not search_url:
            log.warning("board_spec_incomplete", name=name or "<unnamed>")
            continue
        specs.append(
            BoardSpec(
                name=name,
                platform=platform,
                search_url=search_url,
                card_selector=str(item.get("card_selector") or "").strip(),
                title_selector=str(item.get("title_selector") or "").strip(),
                company_selector=str(item.get("company_selector") or "").strip(),
                location_selector=str(item.get("location_selector") or "").strip(),
                link_selector=str(item.get("link_selector") or "").strip(),
                enabled=_as_bool(item.get("enabled"), True),
            )
        )
    return specs
