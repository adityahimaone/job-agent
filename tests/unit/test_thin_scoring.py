"""Thin-description scoring: one-line Muse/Ego imports must not hard-zero.

Regression tests for the 2026-11-11 audit: FINN, StarHub, Capgemini,
Coinbase and GudangAda frontend postings all scored 0.00 because the skill
prefilter only looked at the (one-line) description. Real frontend roles
with thin descriptions now reach the queue via a title-role floor instead.
"""

import pytest

from job_agent.ai.job_matcher import JobMatcher
from job_agent.platforms.base import JobPosting, Platform

PROFILE = {
    "name": "Aditya Himawan",
    "search": {
        "keywords": [
            "Design Engineer",
            "Frontend Engineer",
            "Frontend Developer",
            "React Developer",
            "UI Engineer",
        ],
        "experience_level": "mid",
        "remote_preference": "remote_first",
    },
    "skills": {
        "required": ["React", "TypeScript", "Next.js"],
        "preferred": ["design systems", "Tailwind CSS", "Zustand", "Go"],
    },
    "exclusions": {
        "companies": [],
        "keywords": ["intern", "junior"],
        "locations": ["india"],
    },
}


def _job(
    title: str, description: str, company: str = "Acme", location: str = "Singapore"
) -> JobPosting:
    return JobPosting(
        external_id="1",
        platform=Platform.MUSE,
        title=title,
        company=company,
        location=location,
        description=description,
    )


class _FakeAI:
    def __init__(self, score: float = 0.40):
        self.score = score
        self.called = False

    def complete(self, **kwargs):
        self.called = True
        return (
            '{"score": %s, "reasoning": "chat fallback", '
            '"matched_skills": [], "missing_skills": [], "role_fit": "none", '
            '"red_flags": []}' % self.score
        )


def _jev(monkeypatch, value):
    monkeypatch.setattr("job_agent.ai.job_matcher.jev_enabled", lambda: True)
    monkeypatch.setattr("job_agent.ai.job_matcher.score_fit", lambda state: value)


def test_thin_frontend_title_no_stack_floors_to_queue(monkeypatch):
    """FINN / StarHub / Capgemini: title-only frontend role, no stack words."""
    _jev(monkeypatch, 0.40)
    job = _job("Frontend Developer", "Frontend Developer. Mid-Senior level, Singapore.")
    result = JobMatcher(_FakeAI()).match(job, PROFILE)
    assert result.score == pytest.approx(0.55)
    assert "thin_description_floor" in result.red_flags


def test_thin_frontend_title_with_stack_floors_higher(monkeypatch):
    """Two or more stack skills in a thin posting earn the higher floor."""
    _jev(monkeypatch, 0.34)
    job = _job(
        "Frontend Developer",
        "Frontend Developer. React and Next.js applications, headless CMS, "
        "design systems with Tailwind CSS. 3+ years frontend experience.",
    )
    result = JobMatcher(_FakeAI()).match(job, PROFILE)
    assert result.score == pytest.approx(0.60)
    assert "thin_description_floor" in result.red_flags


def test_thin_frontend_title_jev_above_floor_untouched(monkeypatch):
    """The floor never lowers a score JEV already rated higher."""
    _jev(monkeypatch, 0.80)
    job = _job(
        "Frontend Developer, React Js, Next Js",
        "Frontend Developer, React Js, Next Js. Singapore, Remote, Contract. "
        "4-6 years frontend development, React, Next.js, TypeScript.",
    )
    result = JobMatcher(_FakeAI()).match(job, PROFILE)
    assert result.score == pytest.approx(0.80)
    assert "thin_description_floor" not in result.red_flags


def test_thin_non_frontend_title_gets_no_floor(monkeypatch):
    """A thin non-frontend role is not rescued by the floor."""
    _jev(monkeypatch, 0.30)
    job = _job(
        "Software Developer",
        "We use Python, Django and Go for our backend services, plus SQL.",
    )
    result = JobMatcher(_FakeAI()).match(job, PROFILE)
    assert result.score == pytest.approx(0.30)
    assert "thin_description_floor" not in result.red_flags


def test_rich_description_no_skill_overlap_still_zero(monkeypatch):
    """The guard stays: a rich description with no skill overlap is 0.00."""
    monkeypatch.setattr("job_agent.ai.job_matcher.jev_enabled", lambda: False)
    ai = _FakeAI()
    job = _job(
        "Software Developer",
        "We are hiring a Salesforce administrator to manage our CRM, "
        "build reports and dashboards, train new users on Apex workflows, "
        "maintain data quality across regions, and support the sales team "
        "with custom objects, validation rules, and process automation.",
    )
    result = JobMatcher(ai).match(job, PROFILE)
    assert result.score == 0.0
    assert "no_skill_overlap" in result.red_flags
    assert ai.called is False


def test_rich_description_title_skill_counts(monkeypatch):
    """Skill words in the title now count for the prefilter."""
    monkeypatch.setattr("job_agent.ai.job_matcher.jev_enabled", lambda: False)
    ai = _FakeAI(score=0.70)
    job = _job(
        "React Developer",
        "We are hiring a developer to build and maintain our customer "
        "portal, work with designers on new screens, fix production bugs, "
        "write tests for new features, join our on-call rotation, and help "
        "improve our deployment pipeline and documentation for the team.",
    )
    result = JobMatcher(ai).match(job, PROFILE)
    assert ai.called is True
    assert result.score == pytest.approx(0.70)


def test_empty_description_frontend_title_floors(monkeypatch):
    """An empty description is thin too: title evidence still counts."""
    _jev(monkeypatch, 0.40)
    job = _job("Senior Frontend Engineer", "")
    result = JobMatcher(_FakeAI()).match(job, PROFILE)
    assert result.score == pytest.approx(0.55)
    assert "thin_description_floor" in result.red_flags


def test_match_error_gets_no_floor(monkeypatch):
    """A scoring failure must never look queueable via the floor."""
    monkeypatch.setattr("job_agent.ai.job_matcher.jev_enabled", lambda: True)
    monkeypatch.setattr("job_agent.ai.job_matcher.score_fit", lambda state: None)

    class _BrokenAI:
        def complete(self, **kwargs):
            raise RuntimeError("endpoint down")

    job = _job("Frontend Developer", "Frontend Developer. Singapore.")
    result = JobMatcher(_BrokenAI()).match(job, PROFILE)
    assert result.score == 0.0
    assert "match_error" in result.red_flags
    assert "thin_description_floor" not in result.red_flags


def test_excluded_location_still_zero_despite_thin_floor(monkeypatch):
    """Hard exclusions run before any floor."""
    _jev(monkeypatch, 0.90)
    job = _job(
        "Frontend Developer", "Frontend Developer. Remote.", location="Pune, India"
    )
    result = JobMatcher(_FakeAI()).match(job, PROFILE)
    assert result.score == 0.0
    assert "excluded_location" in result.red_flags


def test_go_matches_word_boundary_only():
    """'Go' must not match 'going', 'good' or 'Google'."""
    assert (
        JobMatcher._matched_skills_in("We are going to build good products", ["Go"])
        == []
    )
    assert JobMatcher._matched_skills_in(
        "Experience with Go and React", ["Go", "React"]
    ) == ["Go", "React"]
    assert JobMatcher._matched_skills_in("We use React.js daily", ["React"]) == [
        "React"
    ]
