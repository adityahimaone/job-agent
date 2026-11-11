"""Tests for the live re-score lane (offline, no network, no AI calls).

Run with: pytest tests/unit/test_rescore.py
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from job_agent.ai.job_matcher import MatchScore
from job_agent.config import Settings
from job_agent.db.models import Base, Job, JobStatus, Platform
from job_agent.db.repository import JobRepository, MatchResultRepository
from job_agent.discovery.rescore import rescore_jobs

PROFILE = {
    "name": "Design Engineer / Frontend Engineer (React, Design Systems)",
    "search": {
        "keywords": ["Frontend Engineer", "Design Engineer", "React Developer"],
        "experience_level": "mid",
    },
    "skills": {"required": ["React", "TypeScript"], "preferred": ["Next.js"]},
    "exclusions": {"companies": [], "keywords": [], "locations": []},
}


def _settings(review_threshold: float = 0.40):
    return Settings(
        matching={"auto_apply_threshold": 0.90, "review_threshold": review_threshold}
    )


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


class _FakeMatcher:
    """Deterministic matcher: score by title keyword."""

    def __init__(self, scores):
        self.scores = scores

    def match(self, posting, profile):
        return MatchScore(
            score=self.scores.get(posting.title, 0.2),
            reasoning="fake",
            matched_skills=[],
            missing_skills=[],
            role_fit="possible",
        )


def _add_job(session, title, status, old_score=None):
    repo = JobRepository(session)
    job = repo.create(
        external_id=f"test:{title}",
        platform=Platform.MUSE,
        title=title,
        company="Acme",
        location="Singapore",
        description="Frontend Developer role.",
        url="https://example.com/j",
        status=status,
    )
    if old_score is not None:
        MatchResultRepository(session).create(
            job_id=job.id, score=old_score, reasoning="old"
        )
    session.commit()
    return job


def test_rescore_queues_borderline_under_loosened_threshold():
    """0.42 was skipped at 0.50; at 0.40 it queues for human review."""
    session = _session()
    job = _add_job(session, "Frontend Developer", JobStatus.SKIPPED, old_score=0.42)
    matcher = _FakeMatcher({"Frontend Developer": 0.42})
    stats = rescore_jobs(session, PROFILE, _settings(), matcher=matcher)
    assert stats["rescored"] == 1
    assert stats["queued"] == 1
    assert session.get(Job, job.id).status == JobStatus.QUEUED
    # Existing match result updated in place, not duplicated.
    from sqlalchemy import select
    from job_agent.db.models import MatchResult

    rows = session.scalars(
        select(MatchResult).where(MatchResult.job_id == job.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].score == 0.42


def test_rescore_never_applies_or_touches_applied():
    """A 0.95 score still only queues (require_approval), and APPLIED
    jobs are never re-scored."""
    session = _session()
    applied = _add_job(session, "Frontend Developer", JobStatus.APPLIED, old_score=0.95)
    high = _add_job(session, "React Developer", JobStatus.SKIPPED, old_score=0.40)
    matcher = _FakeMatcher({"Frontend Developer": 0.95, "React Developer": 0.95})
    stats = rescore_jobs(session, PROFILE, _settings(), matcher=matcher)
    assert stats["rescored"] == 1  # only the SKIPPED one
    assert session.get(Job, applied.id).status == JobStatus.APPLIED
    assert session.get(Job, high.id).status == JobStatus.QUEUED


def test_rescore_dry_run_writes_nothing():
    session = _session()
    job = _add_job(session, "Frontend Developer", JobStatus.SKIPPED, old_score=0.40)
    matcher = _FakeMatcher({"Frontend Developer": 0.80})
    stats = rescore_jobs(session, PROFILE, _settings(), matcher=matcher, dry_run=True)
    assert stats["queued"] == 1
    assert session.get(Job, job.id).status == JobStatus.SKIPPED
    from sqlalchemy import select
    from job_agent.db.models import MatchResult

    rows = session.scalars(
        select(MatchResult).where(MatchResult.job_id == job.id)
    ).all()
    assert rows[0].score == 0.40


def test_rescore_creates_result_when_missing():
    session = _session()
    job = _add_job(session, "Frontend Developer", JobStatus.DISCOVERED)
    matcher = _FakeMatcher({"Frontend Developer": 0.60})
    stats = rescore_jobs(session, PROFILE, _settings(), matcher=matcher)
    assert stats["rescored"] == 1
    assert stats["queued"] == 1
    assert MatchResultRepository(session).get_by_job_id(job.id) is not None


def test_rescore_threshold_boundary_040():
    """At the 0.40 review threshold, a 0.40 score queues and 0.39 stays skipped."""
    session = _session()
    at = _add_job(session, "Frontend Developer", JobStatus.SKIPPED, old_score=0.40)
    below = _add_job(session, "React Developer", JobStatus.SKIPPED, old_score=0.39)
    matcher = _FakeMatcher({"Frontend Developer": 0.40, "React Developer": 0.39})
    stats = rescore_jobs(session, PROFILE, _settings(), matcher=matcher)
    assert stats["rescored"] == 2
    assert stats["queued"] == 1
    assert session.get(Job, at.id).status == JobStatus.QUEUED
    assert session.get(Job, below.id).status == JobStatus.SKIPPED
