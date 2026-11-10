"""Muse fusion lane: import jobs found by Muse into job-agent.

Muse (the personal agent) finds jobs in chat / email / the web that the
feed and browser lanes never see. This module is the import-only bridge
for those jobs:

    job-agent muse import <file.json|file.csv> --profile config/profiles/aditya.yaml

Input rows are normalised with the same ``feeds._normalize`` helper the
structured feeds use, stamped ``platform=muse`` / ``source="muse"``,
deduped by ``(external_id, platform)`` (falling back to a URL hash, like
``FeedHub._dedupe``), and — unless ``--dry-run`` / ``--no-score`` — run
through the existing JEV matcher (with the OpenRouter chat fallback in
``JobMatcher``) and the existing ``decide_job`` thresholds. Imported jobs
never apply: ``decide_job`` is always called with
``require_approval=True``, so the best outcome is QUEUED for a human.

Flow into the human surface is unchanged: ``scripts/sync_hermes_tab.py``
selects by ``match_results.score`` / ``QUEUED`` status, not by platform,
so a muse job scoring >= MIN_FIT (30) appears in the hermes tab like
any other job.

No new DB schema is needed: ``jobs.source`` already exists, and
``Platform.MUSE`` is a plain string value in SQLite (no CHECK constraint
on the live ``jobs`` table), so no Alembic migration is required.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from job_agent.db.models import Job, Platform
from job_agent.db.repository import (
    JobRepository,
    MatchResultRepository,
)
from job_agent.platforms.base import JobPosting
from job_agent.utils.logging import get_logger

log = get_logger(__name__)

MUSE_SOURCE = "muse"
MUSE_PLATFORM = Platform.MUSE

# Accept these (case-insensitive) column / key spellings in input files.
_FIELD_ALIASES: dict[str, str] = {
    "title": "title",
    "role": "title",
    "job_title": "title",
    "company": "company",
    "company_name": "company",
    "employer": "company",
    "location": "location",
    "url": "url",
    "link": "url",
    "apply_url": "url",
    "job_url": "url",
    "description": "description",
    "job_description": "description",
    "salary": "salary",
    "employment_type": "employment_type",
    "job_type": "employment_type",
    "type": "employment_type",
    "remote": "remote",
    "external_id": "external_id",
    "id": "external_id",
    "job_id": "external_id",
    "source": "source",
    "origin_platform": "origin_platform",
    "found_via": "origin_platform",
    "tags": "tags",
    "skills": "tags",
}


def _hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", "ignore")).hexdigest()[:16]


def muse_external_id(raw: dict[str, Any]) -> str:
    """Stable, namespaced external id for a Muse-found job.

    ``muse:<given-id>`` when the input supplies one, otherwise
    ``muse:<sha1(url)>`` / ``muse:<sha1(title|company)>``. The ``muse:``
    prefix keeps Muse jobs in their own dedup namespace even when a feed
    found the same posting under a different id.
    """
    given = str(raw.get("external_id") or raw.get("id") or "").strip()
    if given:
        return given if given.startswith("muse:") else f"muse:{given}"
    url = str(raw.get("url") or "").strip()
    if url:
        return f"muse:{_hash(url)}"
    title = str(raw.get("title") or "").strip().lower()
    company = str(raw.get("company") or "").strip().lower()
    return f"muse:{_hash(f'{title}|{company}')}"


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


def to_posting(raw: dict[str, Any]) -> JobPosting:
    """Normalise one Muse-found job dict into a JobPosting.

    Raises ``ValueError`` when title or company is missing — the caller
    records that row as skipped instead of aborting the whole import.
    """
    from job_agent.discovery.feeds import _normalize

    row = _coerce_raw(raw)
    title = str(row.get("title") or "").strip()
    company = str(row.get("company") or "").strip()
    if not title or not company:
        raise ValueError("title and company are required")
    tags = row.get("tags")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    elif isinstance(tags, list):
        tags = [str(t).strip() for t in tags if str(t).strip()]
    else:
        tags = []
    extra: dict[str, Any] = {}
    origin = str(row.get("origin_platform") or "").strip()
    if origin:
        extra["origin_platform"] = origin
    return _normalize(
        MUSE_PLATFORM,
        {
            "external_id": muse_external_id(row),
            "title": title,
            "company": company,
            "location": row.get("location") or "",
            "description": row.get("description") or "",
            "url": row.get("url") or "",
            "salary": row.get("salary") or "",
            "tags": tags,
            "job_type": row.get("employment_type") or "",
            "remote": bool(row.get("remote"))
            if isinstance(row.get("remote"), bool)
            else str(row.get("remote") or "").strip().lower()
            in ("true", "yes", "remote", "1"),
            "source": MUSE_SOURCE,
            "extra": extra,
        },
    )


def parse_muse_file(path: str | Path) -> tuple[list[JobPosting], list[str]]:
    """Parse a Muse jobs file (JSON or CSV) into postings.

    JSON accepts a list, ``{"jobs": [...]}``, or a single job object.
    CSV accepts the aliased headers in :data:`_FIELD_ALIASES`.
    Returns ``(postings, errors)`` — invalid rows are reported, never fatal.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Muse jobs file not found: {p}")
    if p.suffix.lower() == ".csv":
        with p.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    elif p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("jobs"), list):
            rows = data["jobs"]
        elif isinstance(data, dict):
            rows = [data]
        elif isinstance(data, list):
            rows = data
        else:
            raise ValueError('JSON must be a job object, a list, or {"jobs": [...]}')
    else:
        raise ValueError(
            f"Unsupported Muse jobs file type: {p.suffix} (use .json or .csv)"
        )

    postings: list[JobPosting] = []
    errors: list[str] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"row {i}: not an object")
            continue
        try:
            postings.append(to_posting(row))
        except ValueError as exc:
            errors.append(f"row {i}: {exc}")
    return postings, errors


def _posting_from_job(job: Job) -> JobPosting:
    return JobPosting(
        external_id=job.external_id,
        platform=job.platform,
        title=job.title,
        company=job.company,
        location=job.location or "",
        description=job.description or "",
        url=job.url or "",
        salary=job.salary,
        easy_apply=job.easy_apply,
        remote=bool(job.remote),
        employment_type=job.employment_type or "",
        remote_policy=job.remote_policy or "",
        timezone=job.timezone or "",
        hours_per_week=job.hours_per_week,
        currency=job.currency or "",
        salary_period=job.salary_period or "",
        source=job.source or "",
    )


def import_muse_jobs(
    session,
    postings: list[JobPosting],
    profile: dict,
    settings,
    *,
    dry_run: bool = False,
    score: bool = True,
    matcher=None,
) -> dict[str, Any]:
    """Import Muse-found postings, score them, and decide queue/skip.

    * Dedupes within the batch (same rule as ``FeedHub._dedupe``), against
      existing ``(external_id, muse)`` rows, and against same title+company
      jobs on *other* platforms (those are counted as
      ``cross_platform_duplicates`` and skipped — the feed lane already
      found them).
    * ``dry_run=True`` writes nothing: it reports what *would* be imported
      and, when ``score`` is true and a matcher is given, in-memory scores.
    * Otherwise stores new jobs, then scores them with ``matcher`` (built
      from ``settings`` when not supplied, via JEV-first ``JobMatcher``)
      and ``decide_job(..., require_approval=True)`` — never auto-apply,
      never apply. A job left at DISCOVERED means the AI provider was
      unavailable (``provider == "none"``), so it can be matched later.
    """
    from job_agent.ai.client import build_ai_client
    from job_agent.ai.job_matcher import JobMatcher
    from job_agent.discovery.feeds import FeedHub
    from job_agent.orchestrator.pipeline_steps import decide_job, match_job

    job_repo = JobRepository(session)
    match_repo = MatchResultRepository(session)

    stats: dict[str, Any] = {
        "parsed": len(postings),
        "imported": 0,
        "duplicates": 0,
        "cross_platform_duplicates": 0,
        "matched": 0,
        "queued": 0,
        "skipped": 0,
        "deferred_no_ai": 0,
        "scores": [],
    }

    # 1. Batch dedup (external_id, platform), URL-hash fallback — same as feeds.
    deduped = FeedHub._dedupe(list(postings))
    stats["duplicates"] += len(postings) - len(deduped)

    # 2. Dedup against the DB + cross-platform duplicates.
    existing = job_repo.filter_existing([(p.external_id, p.platform) for p in deduped])
    new: list[JobPosting] = []
    for posting in deduped:
        key = (posting.external_id, posting.platform.value)
        if key in existing:
            stats["duplicates"] += 1
            continue
        other = job_repo.find_cross_platform_duplicate(
            posting.title, posting.company, posting.platform
        )
        if other is not None:
            stats["cross_platform_duplicates"] += 1
            log.info(
                "muse_cross_platform_duplicate",
                title=posting.title,
                company=posting.company,
                original_platform=other.platform.value,
            )
            continue
        new.append(posting)

    if dry_run:
        stats["would_import"] = len(new)
        if score:
            if matcher is None:
                from job_agent.ai.client import build_ai_client
                from job_agent.ai.job_matcher import JobMatcher

                ai_client = build_ai_client(settings)
                if getattr(ai_client, "provider", "") == "none":
                    stats["deferred_no_ai"] = len(new)
                else:
                    matcher = JobMatcher(ai_client)
            if matcher is not None:
                for posting in new:
                    result = matcher.match(posting, profile)
                    stats["scores"].append(
                        {
                            "title": posting.title,
                            "company": posting.company,
                            "score": result.score,
                            "reasoning": result.reasoning,
                        }
                    )
                    stats["matched"] += 1
                    if result.score >= settings.matching.review_threshold:
                        stats["queued"] += 1
                    else:
                        stats["skipped"] += 1
        return stats

    # 3. Store (same fields as pipeline._store_postings).
    created: list[Job] = []
    for posting in new:
        created.append(
            job_repo.create(
                external_id=posting.external_id,
                platform=posting.platform,
                title=posting.title,
                company=posting.company,
                location=posting.location,
                description=posting.description,
                url=posting.url,
                salary=posting.salary,
                easy_apply=posting.easy_apply,
                remote=posting.remote,
                employment_type=posting.employment_type or "",
                remote_policy=posting.remote_policy or "",
                timezone=posting.timezone or "",
                hours_per_week=posting.hours_per_week,
                currency=posting.currency or "",
                salary_period=posting.salary_period or "",
                source=posting.source or MUSE_SOURCE,
                profile_name=profile.get("name", ""),
            )
        )
    stats["imported"] = len(created)
    session.commit()

    # 4. Score + decide. No AI provider -> leave DISCOVERED for a later run.
    if score and created:
        if matcher is None:
            ai_client = build_ai_client(settings)
            if getattr(ai_client, "provider", "") == "none":
                stats["deferred_no_ai"] = len(created)
                log.info("muse_matches_deferred_no_ai_provider")
                return stats
            matcher = JobMatcher(ai_client)
        for job in created:
            posting = _posting_from_job(job)
            result = match_job(
                job=job,
                posting=posting,
                matcher=matcher,
                match_repo=match_repo,
                profile=profile,
            )
            stats["matched"] += 1
            stats["scores"].append(
                {
                    "title": job.title,
                    "company": job.company,
                    "score": result.score,
                    "reasoning": result.reasoning,
                }
            )
            decision = decide_job(job, result, settings, require_approval=True)
            if decision == "queue":
                stats["queued"] += 1
            else:
                stats["skipped"] += 1
        session.commit()
    return stats
