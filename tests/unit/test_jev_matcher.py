"""JEV scoring path in JobMatcher: primary when enabled, degrade to AI when not."""

import pytest

from job_agent.ai.job_matcher import JobMatcher, MatchScore
from job_agent.platforms.base import JobPosting, Platform

PROFILE = {
    "name": "Test Candidate",
    "search": {
        "keywords": ["Frontend Engineer"],
        "experience_level": "mid",
        "remote_preference": "remote_only",
        "filters": {"min_hourly_rate": 30},
    },
    "skills": {"required": ["React"], "preferred": ["TypeScript"]},
    "exclusions": {"companies": [], "keywords": []},
}

JOB = JobPosting(
    external_id="1",
    platform=Platform.LINKEDIN,
    title="Senior Frontend Engineer",
    company="Acme",
    location="Remote",
    description="React and TypeScript, fully remote contract.",
    url="https://example.com/1",
    employment_type="contract",
    remote_policy="remote",
)


class _FakeAI:
    called = False

    def complete(self, **kwargs):
        _FakeAI.called = True
        return '{"score": 0.42, "reasoning": "chat fallback"}'


def test_jev_score_used_when_available(monkeypatch):
    monkeypatch.setattr("job_agent.ai.job_matcher.jev_enabled", lambda: True)
    monkeypatch.setattr("job_agent.ai.job_matcher.score_fit", lambda state: 0.93)
    _FakeAI.called = False

    result = JobMatcher(_FakeAI()).match(JOB, PROFILE)

    assert isinstance(result, MatchScore)
    assert result.score == pytest.approx(0.93)
    assert result.role_fit == "strong"
    assert _FakeAI.called is False, "chat must not be called when JEV answers"


def test_jev_failure_falls_back_to_chat(monkeypatch):
    monkeypatch.setattr("job_agent.ai.job_matcher.jev_enabled", lambda: True)
    monkeypatch.setattr("job_agent.ai.job_matcher.score_fit", lambda state: None)
    _FakeAI.called = False

    result = JobMatcher(_FakeAI()).match(JOB, PROFILE)

    assert result.score == pytest.approx(0.42)
    assert _FakeAI.called is True