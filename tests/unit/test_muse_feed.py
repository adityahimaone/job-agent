"""Tests for the Muse fusion LinkedIn timeline/feed lane (offline, no
network, no AI calls).

Run with:  pytest tests/unit/test_muse_feed.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from job_agent.ai.job_matcher import JobMatcher, MatchScore
from job_agent.config import Settings
from job_agent.db.models import Base, JobStatus, Platform
from job_agent.db.repository import JobRepository
from job_agent.db.session import reset_engine
from job_agent.discovery import load_feed_specs
from job_agent.discovery.feeds import FeedHub, FeedSpec
from job_agent.discovery.muse_feed import (
    FEED_SOURCE,
    classify_post,
    extract_job_details,
    feed_external_id,
    feed_post_to_posting,
    import_feed_jobs,
    parse_feed_file,
    render_feed_connection_note,
)

ROOT = Path(__file__).resolve().parents[2]

PROFILE = {
    "name": "Design Engineer / Frontend Engineer (React, Design Systems)",
    "search": {
        "keywords": ["Frontend Engineer", "Design Engineer", "React Developer"],
        "experience_level": "mid",
    },
    "skills": {"required": ["React", "TypeScript"], "preferred": ["Next.js"]},
    "exclusions": {"companies": [], "keywords": [], "locations": []},
}

# A real-shaped feed post: hiring signal + role "at" company + location.
FEED_POST = {
    "post_url": "https://www.linkedin.com/posts/code-ninety_hiring-activity-1",
    "post_text": (
        "HIRING – Frontend Developer | Remote. Code Ninety is looking for a "
        "Frontend Developer to build intuitive user interfaces. Location: "
        "Remote. Experience: 2+ years. React, TypeScript, Tailwind CSS."
    ),
    "author": "Code Ninety",
    "author_company": "Code Ninety",
    "posted_at": "2026-11-03",
}

EXPLICIT_POST = {
    "post_url": "https://www.linkedin.com/posts/someone_activity-2",
    "post_text": "We're hiring! Details in the comments. Apply now.",
    "author": "Hiring Manager",
    "title": "React Frontend Developer",
    "company": "Acme Studio",
    "location": "Remote, Singapore",
    "apply_url": "https://example.com/jobs/react-frontend-acme",
}

NON_JOB_POST = {
    "post_url": "https://www.linkedin.com/posts/someone_activity-3",
    "post_text": (
        "Excited to share our new design system launch! Six months of work "
        "from the whole team. Here's what we learned about tokens."
    ),
    "author": "Design Lead",
}

INCOMPLETE_POST = {
    "post_url": "https://www.linkedin.com/posts/someone_activity-4",
    "post_text": "We're hiring! Someone great, please DM me. Role details soon.",
    "author": "Founder",
}


class _FakeMatcher:
    """Deterministic matcher: 0.8 for React jobs, 0.2 otherwise."""

    def match(self, posting, profile):
        if "react" in posting.title.lower():
            return MatchScore(
                score=0.8,
                reasoning="fake: React match",
                matched_skills=["React"],
                missing_skills=["Next.js"],
                role_fit="possible",
            )
        return MatchScore(
            score=0.2,
            reasoning="fake: no fit",
            matched_skills=[],
            missing_skills=["React"],
            role_fit="none",
        )


class _FakeAI:
    def __init__(self, score: float = 0.40):
        self.score = score

    def complete(self, **kwargs):
        return (
            '{"score": %s, "reasoning": "chat fallback", '
            '"matched_skills": [], "missing_skills": [], "role_fit": "none", '
            '"red_flags": []}' % self.score
        )


def _make_session():
    reset_engine()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)()


def _settings():
    return Settings(matching={"auto_apply_threshold": 0.90, "review_threshold": 0.40})


# ---------------------------------------------------------------------------
# Classification + extraction
# ---------------------------------------------------------------------------


def test_classify_post_hiring_signal_vs_launch_post():
    assert classify_post(FEED_POST["post_text"]) == "job"
    assert classify_post("We're hiring a Frontend Engineer to join us!") == "job"
    assert classify_post(NON_JOB_POST["post_text"]) == "non_job"
    assert classify_post("Congrats to the team on the release!") == "non_job"


def test_extract_job_details_title_at_company():
    details = extract_job_details(
        "We're hiring a Frontend Developer at HFT Architecture. "
        "Fully remote, worldwide. TypeScript, React, Next.js."
    )
    assert details["title"] == "Frontend Developer"
    assert details["company"] == "HFT Architecture"
    assert details["location"] == "Remote"


def test_extract_job_details_hiring_a_role():
    details = extract_job_details(
        "Senior Frontend Engineer (React & Next.js). We are looking for an "
        "experienced Senior Frontend Engineer to join a growing team. "
        "Location: Remote. 3+ years with React."
    )
    assert "Frontend Engineer" in details["title"]
    assert details["location"] == "Remote"


def test_extract_job_details_prefers_apply_url_over_post_url():
    details = extract_job_details(
        "We're hiring a Frontend Developer. Apply here: "
        "https://example.com/careers/frontend — remote worldwide."
    )
    assert details["apply_url"] == "https://example.com/careers/frontend"


# ---------------------------------------------------------------------------
# feed_post_to_posting
# ---------------------------------------------------------------------------


def test_feed_post_to_posting_stamps_muse_feed_source():
    posting = feed_post_to_posting(FEED_POST)
    assert posting.platform == Platform.MUSE
    assert posting.source == FEED_SOURCE
    assert posting.title == "Frontend Developer"
    assert posting.company == "Code Ninety"
    assert posting.url == FEED_POST["post_url"]
    assert posting.extra["post_url"] == FEED_POST["post_url"]
    assert posting.extra["post_author"] == "Code Ninety"
    assert posting.extra["origin_platform"] == "linkedin_feed"
    assert posting.external_id.startswith("muse-feed:")


def test_feed_external_id_is_namespaced_and_stable():
    assert feed_external_id(FEED_POST) == feed_external_id(FEED_POST)
    assert feed_external_id(FEED_POST).startswith("muse-feed:")
    assert (
        feed_external_id({"external_id": "abc-123", **FEED_POST}) == "muse-feed:abc-123"
    )


def test_explicit_title_company_win_over_extraction():
    posting = feed_post_to_posting(EXPLICIT_POST)
    assert posting.title == "React Frontend Developer"
    assert posting.company == "Acme Studio"
    assert posting.url == "https://example.com/jobs/react-frontend-acme"
    assert posting.location == "Remote, Singapore"


def test_non_job_post_is_rejected():
    with pytest.raises(ValueError, match="not a job post"):
        feed_post_to_posting(NON_JOB_POST)


def test_post_without_extractable_job_is_rejected():
    with pytest.raises(ValueError, match="no job details extractable"):
        feed_post_to_posting(INCOMPLETE_POST)


def test_extracted_non_frontend_role_is_rejected():
    with pytest.raises(ValueError, match="not a frontend role"):
        feed_post_to_posting(
            {
                "post_url": "https://www.linkedin.com/posts/x_activity-9",
                "post_text": "We're hiring a Head of People to join our team. Remote.",
                "author": "CEO",
            }
        )


# ---------------------------------------------------------------------------
# parse_feed_file
# ---------------------------------------------------------------------------


def test_parse_feed_file_counts_non_job_and_incomplete(tmp_path):
    jf = tmp_path / "feed.json"
    jf.write_text(
        json.dumps(
            {"posts": [FEED_POST, EXPLICIT_POST, NON_JOB_POST, INCOMPLETE_POST]}
        ),
        encoding="utf-8",
    )
    postings, errors, stats = parse_feed_file(jf)
    assert stats == {"posts": 4, "non_job": 1, "incomplete": 1}
    assert len(postings) == 2
    assert len(errors) == 2
    assert postings[0].source == FEED_SOURCE


def test_parse_feed_csv(tmp_path):
    cf = tmp_path / "feed.csv"
    with cf.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["post_url", "post_text", "author", "author_company"]
        )
        w.writeheader()
        w.writerow({k: FEED_POST[k] for k in w.fieldnames})
    postings, errors, stats = parse_feed_file(cf)
    assert errors == []
    assert stats["posts"] == 1
    assert len(postings) == 1


# ---------------------------------------------------------------------------
# Import + dedup + scoring wiring
# ---------------------------------------------------------------------------


def test_import_feed_scores_and_queues_without_applying():
    engine, session = _make_session()
    try:
        stats = import_feed_jobs(
            session,
            [feed_post_to_posting(EXPLICIT_POST), feed_post_to_posting(FEED_POST)],
            PROFILE,
            _settings(),
            matcher=_FakeMatcher(),
        )
        assert stats["imported"] == 2
        assert stats["matched"] == 2
        assert stats["queued"] == 1  # React 0.8 >= review 0.40
        assert stats["skipped"] == 1  # Frontend Developer 0.2 < 0.40
        jobs = JobRepository(session).list_all()
        assert all(j.source == FEED_SOURCE for j in jobs)
        react = next(j for j in jobs if "React" in j.title)
        assert react.status == JobStatus.QUEUED
        assert react.application is None  # import never applies
    finally:
        session.close()
        engine.dispose()
        reset_engine()


def test_feed_duplicate_of_formal_posting_is_skipped():
    engine, session = _make_session()
    try:
        repo = JobRepository(session)
        repo.create(
            external_id="li-1",
            platform=Platform.LINKEDIN,
            title="React Frontend Developer",
            company="Acme Studio",
        )
        session.commit()
        stats = import_feed_jobs(
            session,
            [feed_post_to_posting(EXPLICIT_POST)],
            PROFILE,
            _settings(),
            score=False,
        )
        assert stats["imported"] == 0
        assert stats["cross_platform_duplicates"] == 1
    finally:
        session.close()
        engine.dispose()
        reset_engine()


def test_feed_dry_run_writes_nothing_but_scores():
    engine, session = _make_session()
    try:
        stats = import_feed_jobs(
            session,
            [feed_post_to_posting(EXPLICIT_POST)],
            PROFILE,
            _settings(),
            dry_run=True,
            matcher=_FakeMatcher(),
        )
        assert stats["would_import"] == 1
        assert stats["imported"] == 0
        assert stats["scores"][0]["score"] == 0.8
        assert JobRepository(session).list_all() == []
    finally:
        session.close()
        engine.dispose()
        reset_engine()


def test_thin_feed_post_scores_via_floor(monkeypatch):
    """A one-line feed post must not hard-zero: the thin floor applies.

    The sample post names React + TypeScript + Tailwind (2+ stack skills),
    so it earns the higher floor, 0.60 — not the 0.40 the JEV/chat path
    would give a thin description.
    """
    monkeypatch.setattr("job_agent.ai.job_matcher.jev_enabled", lambda: True)
    monkeypatch.setattr("job_agent.ai.job_matcher.score_fit", lambda state: 0.40)
    posting = feed_post_to_posting(FEED_POST)
    assert len(posting.description.split()) < 40  # thin by construction
    result = JobMatcher(_FakeAI()).match(posting, PROFILE)
    assert result.score == pytest.approx(0.60)
    assert "thin_description_floor" in result.red_flags


# ---------------------------------------------------------------------------
# Connection note (grounded template, preparation only — never sent)
# ---------------------------------------------------------------------------


def test_feed_connection_note_uses_app_template():
    engine, session = _make_session()
    try:
        stats = import_feed_jobs(
            session,
            [feed_post_to_posting(EXPLICIT_POST)],
            PROFILE,
            _settings(),
            matcher=_FakeMatcher(),
        )
        assert stats["imported"] == 1
        job = JobRepository(session).list_all()[0]
        note, warnings = render_feed_connection_note(
            job,
            PROFILE,
            session,
            candidate_path=ROOT / "config/templates/candidate_profile.md",
            templates_dir=ROOT / "config/templates",
        )
        assert "Acme Studio" in note
        assert "React Frontend Developer" in note
        assert len(note) <= 300  # LinkedIn's connection-note limit
        assert warnings == []
    finally:
        session.close()
        engine.dispose()
        reset_engine()


# ---------------------------------------------------------------------------
# Feed config: muse-feed is a registered, import-only source
# ---------------------------------------------------------------------------


def test_muse_feed_is_registered_import_only():
    specs = load_feed_specs(ROOT / "config/feeds.yaml")
    names = {s.name for s in specs}
    assert "muse_feed" in names
    hub = FeedHub(
        [FeedSpec(name="muse_feed", kind="muse_feed", url="", platform="muse")]
    )
    assert hub.discover(["frontend"], ["Remote"]) == []
    assert hub.results()[0].error == ""
