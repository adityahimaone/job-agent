"""Tests for the Muse fusion lane (offline, no network, no AI calls).

Run with:  pytest tests/unit/test_muse_fusion.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from job_agent.ai.job_matcher import MatchScore
from job_agent.config import Settings
from job_agent.db.models import Base, JobStatus, Platform
from job_agent.db.repository import JobRepository
from job_agent.db.session import reset_engine
from job_agent.discovery import load_feed_specs
from job_agent.discovery.feeds import FeedHub, FeedSpec
from job_agent.discovery.muse import (
    MUSE_SOURCE,
    import_muse_jobs,
    muse_external_id,
    parse_muse_file,
    to_posting,
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

REACT_JOB = {
    "title": "React Frontend Developer",
    "company": "Acme Studio",
    "location": "Remote, Singapore",
    "url": "https://example.com/jobs/react-frontend-acme",
    "description": "Build React and TypeScript front ends, Next.js, design systems, Tailwind CSS. Remote, part-time friendly.",
}

JAVA_JOB = {
    "title": "Java Backend Developer",
    "company": "Other Corp",
    "location": "Remote",
    "url": "https://example.com/jobs/java-backend-other",
    "description": "Java, Spring Boot, Kafka. Onsite in Jakarta.",
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


def _make_session():
    reset_engine()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)()


def _settings():
    return Settings(matching={"auto_apply_threshold": 0.90, "review_threshold": 0.55})


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_muse_external_id_is_namespaced_and_stable():
    assert muse_external_id(REACT_JOB) == muse_external_id(REACT_JOB)
    assert muse_external_id(REACT_JOB).startswith("muse:")
    assert muse_external_id({"external_id": "abc-123", **REACT_JOB}) == "muse:abc-123"
    assert muse_external_id({"external_id": "muse:abc-123"}) == "muse:abc-123"


def test_to_posting_stamps_muse_source_and_platform():
    posting = to_posting(REACT_JOB)
    assert posting.platform == Platform.MUSE
    assert posting.source == MUSE_SOURCE
    assert posting.title == "React Frontend Developer"
    assert posting.company == "Acme Studio"
    assert posting.remote is True
    assert posting.url == REACT_JOB["url"]


def test_to_posting_rejects_missing_title_or_company():
    import pytest

    with pytest.raises(ValueError):
        to_posting({"company": "Acme"})
    with pytest.raises(ValueError):
        to_posting({"title": "React Developer"})


def test_parse_json_list_and_csv(tmp_path):
    jf = tmp_path / "muse.json"
    jf.write_text(json.dumps([REACT_JOB, JAVA_JOB]), encoding="utf-8")
    postings, errors = parse_muse_file(jf)
    assert errors == []
    assert len(postings) == 2
    assert postings[0].platform == Platform.MUSE

    cf = tmp_path / "muse.csv"
    with cf.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["title", "company", "location", "url", "description"]
        )
        w.writeheader()
        w.writerow({k: REACT_JOB[k] for k in w.fieldnames})
    postings, errors = parse_muse_file(cf)
    assert errors == []
    assert len(postings) == 1


def test_parse_json_object_and_jobs_key(tmp_path):
    jf = tmp_path / "one.json"
    jf.write_text(
        json.dumps({"jobs": [REACT_JOB, {"title": "No company"}]}), encoding="utf-8"
    )
    postings, errors = parse_muse_file(jf)
    assert len(postings) == 1
    assert len(errors) == 1  # missing company is reported, not fatal


# ---------------------------------------------------------------------------
# Import + dedup + scoring wiring
# ---------------------------------------------------------------------------


def test_import_scores_and_queues_high_fit_without_applying():
    engine, session = _make_session()
    try:
        stats = import_muse_jobs(
            session,
            [to_posting(REACT_JOB), to_posting(JAVA_JOB)],
            PROFILE,
            _settings(),
            matcher=_FakeMatcher(),
        )
        assert stats["imported"] == 2
        assert stats["matched"] == 2
        assert stats["queued"] == 1  # React 0.8 >= review 0.55
        assert stats["skipped"] == 1  # Java 0.2 < 0.55
        jobs = JobRepository(session).list_all()
        assert all(j.source == MUSE_SOURCE for j in jobs)
        assert all(j.platform == Platform.MUSE for j in jobs)
        react = next(j for j in jobs if "React" in j.title)
        assert react.status == JobStatus.QUEUED
        assert react.application is None  # import never applies
    finally:
        session.close()
        engine.dispose()
        reset_engine()


def test_reimport_is_a_noop_dedup():
    engine, session = _make_session()
    try:
        first = import_muse_jobs(
            session,
            [to_posting(REACT_JOB)],
            PROFILE,
            _settings(),
            score=False,
        )
        assert first["imported"] == 1
        second = import_muse_jobs(
            session,
            [to_posting(REACT_JOB)],
            PROFILE,
            _settings(),
            score=False,
        )
        assert second["imported"] == 0
        assert second["duplicates"] == 1
        assert len(JobRepository(session).list_all()) == 1
    finally:
        session.close()
        engine.dispose()
        reset_engine()


def test_cross_platform_duplicate_is_skipped():
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
        stats = import_muse_jobs(
            session,
            [to_posting(REACT_JOB)],
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


def test_dry_run_writes_nothing_but_scores():
    engine, session = _make_session()
    try:
        stats = import_muse_jobs(
            session,
            [to_posting(REACT_JOB)],
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


# ---------------------------------------------------------------------------
# Feed config: muse is a registered, import-only source
# ---------------------------------------------------------------------------


def test_feeds_yaml_contains_muse_source():
    specs = load_feed_specs(ROOT / "config" / "feeds.yaml")
    muse = [s for s in specs if s.name == "muse"]
    assert len(muse) == 1
    assert muse[0].kind == "muse"


def test_muse_feed_parser_returns_no_postings_without_network():
    hub = FeedHub([FeedSpec(name="muse", kind="muse", url="", platform="muse")])
    assert hub.discover(["frontend"], ["Remote"]) == []
    assert hub.results()[0].error == ""
