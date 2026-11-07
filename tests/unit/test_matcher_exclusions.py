"""Hard exclusions: location skip + skill columns filled on the JEV path."""

import pytest

from job_agent.ai.job_matcher import JobMatcher, MatchScore
from job_agent.platforms.base import JobPosting, Platform

PROFILE = {
    "name": "Test Candidate",
    "search": {"keywords": ["Frontend Engineer"], "experience_level": "mid"},
    "skills": {"required": ["React", "TypeScript"], "preferred": ["Tailwind CSS"]},
    "exclusions": {"companies": [], "keywords": [], "locations": ["india"]},
}


def _job(location: str) -> JobPosting:
    return JobPosting(
        external_id="1",
        platform=Platform.LINKEDIN,
        title="Senior Frontend Engineer",
        company="Acme",
        location=location,
        description="React, TypeScript and Tailwind CSS, remote contract.",
        url="https://example.com/1",
    )


class _FakeAI:
    def complete(self, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("AI must not be called for excluded locations")


@pytest.mark.parametrize(
    "location", ["Pune City, Maharashtra, India", "INDIA", "Bengaluru, india"]
)
def test_excluded_location_zeroes_score(location, monkeypatch):
    monkeypatch.setattr("job_agent.ai.job_matcher.jev_enabled", lambda: False)
    result = JobMatcher(_FakeAI()).match(_job(location), PROFILE)
    assert isinstance(result, MatchScore)
    assert result.score == 0.0
    assert result.red_flags == ["excluded_location"]


def test_allowed_location_not_skipped(monkeypatch):
    monkeypatch.setattr("job_agent.ai.job_matcher.jev_enabled", lambda: False)
    monkeypatch.setattr(
        JobMatcher, "_title_matches_keywords", staticmethod(lambda *a, **k: True)
    )
    result = JobMatcher(_FakeAI()).match(_job("Jakarta, Indonesia"), PROFILE)
    # reaches the skill prefilter / matcher, not the location gate
    assert "excluded_location" not in result.red_flags


def test_jev_path_fills_skill_columns(monkeypatch):
    monkeypatch.setattr("job_agent.ai.job_matcher.jev_enabled", lambda: True)
    monkeypatch.setattr("job_agent.ai.job_matcher.score_fit", lambda state: 0.62)
    result = JobMatcher(_FakeAI()).match(_job("Jakarta, Indonesia"), PROFILE)
    assert result.score == pytest.approx(0.62)
    assert "React" in result.matched_skills
    assert "Tailwind CSS" in result.matched_skills
    assert result.missing_skills == []