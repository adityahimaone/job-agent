"""Re-score jobs already stored in the DB with the current matcher.

Used after a scoring change (e.g. the thin-description floor, a
threshold loosening) so jobs that were skipped under the old rules get a
second look. Re-scoring only ever changes match scores and job status
(SKIPPED/DISCOVERED -> QUEUED when the new score clears the review
threshold). It never applies, never creates an application, and never
touches APPLIED jobs: ``decide_job(..., require_approval=True)`` caps
the best outcome at QUEUED.

An existing match result is updated in place (no duplicate rows); a
job with no stored result gets one created.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from job_agent.db.models import Job, JobStatus
from job_agent.db.repository import MatchResultRepository
from job_agent.orchestrator.pipeline_steps import decide_job

# Only these statuses are re-scored. QUEUED jobs are already in human
# review (re-scoring them could only demote on a flaky re-match);
# APPLIED / AUTO_APPROVED jobs are outcomes, never re-opened.
_RESCORE_STATUSES = (JobStatus.SKIPPED, JobStatus.DISCOVERED)


def rescore_jobs(
    session,
    profile: dict,
    settings,
    *,
    matcher=None,
    limit: int = 0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Re-score stored SKIPPED/DISCOVERED jobs with the current matcher.

    Returns stats plus a per-job ``changes`` list (old score/status ->
    new score/status). In dry-run mode nothing is written.
    """
    if matcher is None:
        from job_agent.ai.client import build_ai_client
        from job_agent.ai.job_matcher import JobMatcher

        ai_client = build_ai_client(settings)
        if getattr(ai_client, "provider", "") == "none":
            return {
                "rescored": 0,
                "queued": 0,
                "skipped": 0,
                "unchanged": 0,
                "deferred_no_ai": 1,
                "changes": [],
            }
        matcher = JobMatcher(ai_client)

    match_repo = MatchResultRepository(session)
    review_threshold = settings.matching.review_threshold

    stmt = select(Job).where(Job.status.in_(_RESCORE_STATUSES)).order_by(Job.id)
    if limit and limit > 0:
        stmt = stmt.limit(limit)
    jobs = list(session.scalars(stmt))

    stats: dict[str, Any] = {
        "rescored": 0,
        "queued": 0,
        "skipped": 0,
        "unchanged": 0,
        "deferred_no_ai": 0,
        "changes": [],
    }

    for job in jobs:
        old_result = match_repo.get_by_job_id(job.id)
        old_score = old_result.score if old_result is not None else None
        old_status = job.status
        from job_agent.discovery.muse import _posting_from_job

        posting = _posting_from_job(job)
        result = matcher.match(posting, profile)
        new_status = (
            JobStatus.QUEUED if result.score >= review_threshold else JobStatus.SKIPPED
        )
        stats["rescored"] += 1
        if new_status == JobStatus.QUEUED:
            stats["queued"] += 1
        else:
            stats["skipped"] += 1
        if old_score == result.score and old_status == new_status:
            stats["unchanged"] += 1
        stats["changes"].append(
            {
                "job_id": job.id,
                "title": job.title,
                "company": job.company,
                "location": job.location or "",
                "url": job.url or "",
                "old_score": old_score,
                "new_score": result.score,
                "old_status": old_status.value,
                "new_status": new_status.value,
            }
        )
        if dry_run:
            continue
        if old_result is not None:
            old_result.score = result.score
            old_result.reasoning = result.reasoning
            old_result.matched_skills = json.dumps(result.matched_skills or [])
            old_result.missing_skills = json.dumps(result.missing_skills or [])
            old_result.role_fit = result.role_fit
            old_result.red_flags = json.dumps(result.red_flags or [])
        else:
            match_repo.create(
                job_id=job.id,
                score=result.score,
                reasoning=result.reasoning,
                matched_skills=result.matched_skills,
                missing_skills=result.missing_skills,
                role_fit=result.role_fit,
                red_flags=result.red_flags,
            )
        # require_approval=True: best outcome is QUEUED, never auto-apply.
        decide_job(job, result, settings, require_approval=True)

    return stats
