"""Score jobs against profiles using AI."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from job_agent.ai.client import AIClient
from job_agent.ai.jev import jev_enabled, score_fit
from job_agent.ai.prompts import JOB_MATCH_TEMPLATE
from job_agent.platforms.base import JobPosting
from job_agent.utils.logging import get_logger

log = get_logger(__name__)


def _constraints_text(search: dict) -> str:
    """Human-readable part-time / remote / timezone constraints for the prompt."""
    filters = search.get("filters") or {}
    parts: list[str] = []
    if search.get("remote_preference"):
        parts.append(f"remote preference: {search['remote_preference']}")
    if filters.get("employment_types"):
        parts.append("employment types: " + ", ".join(filters["employment_types"]))
    if filters.get("max_hours_per_week"):
        parts.append(f"max {filters['max_hours_per_week']} h/week")
    if filters.get("min_hourly_rate"):
        parts.append(f"min rate {filters['min_hourly_rate']}")
    if filters.get("timezone_alignment"):
        parts.append(f"timezone: {filters['timezone_alignment']}")
    return "; ".join(parts) or "none stated"


_NOISE_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "in",
        "to",
        "for",
        "with",
        "at",
        "on",
        "is",
        "are",
        "was",
        "we",
        "our",
        "you",
        "your",
        "i",
        "ii",
        "iii",
        "iv",
        "v",
        "sr",
        "jr",
        "level",
        "role",
        "position",
        "job",
        "remote",
        "hybrid",
        "onsite",
        "full",
        "time",
        "part",
        "contract",
        "temporary",
    }
)


@dataclass
class MatchScore:
    """Result of matching a job against a profile."""

    score: float
    reasoning: str = ""
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    role_fit: str = ""
    red_flags: list[str] = field(default_factory=list)


class JobMatcher:
    """Scores jobs against candidate profiles using AI."""

    def __init__(self, ai_client: AIClient):
        self.ai = ai_client

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Extract meaningful lowercase tokens from text."""
        tokens = set(re.findall(r"[a-z]+", text.lower()))
        return tokens - _NOISE_WORDS

    @staticmethod
    def _title_matches_keywords(title: str, keywords: list[str]) -> bool:
        """Check if any search keyword shares meaningful tokens with the job title."""
        title_tokens = JobMatcher._tokenize(title)
        if not title_tokens:
            return True
        for kw in keywords:
            kw_tokens = JobMatcher._tokenize(kw)
            if kw_tokens & title_tokens:
                return True
        return False

    @staticmethod
    def _has_skill_overlap(description: str, skills: list[str]) -> bool:
        """Check if the description mentions any of the given skills."""
        desc_lower = description.lower()
        for skill in skills:
            if skill.lower() in desc_lower:
                return True
        return False

    def match(self, job: JobPosting, profile: dict[str, Any]) -> MatchScore:
        """Score a job posting against a candidate profile."""
        # Check exclusions first (no need to call AI)
        exclusions = profile.get("exclusions", {})
        excluded_companies = [c.lower() for c in exclusions.get("companies", [])]
        excluded_keywords = [k.lower() for k in exclusions.get("keywords", [])]

        if job.company.lower() in excluded_companies:
            return MatchScore(
                score=0.0,
                reasoning=f"Company '{job.company}' is in the exclusion list.",
                red_flags=["excluded_company"],
            )

        desc_lower = job.description.lower()
        title_lower = job.title.lower()
        for kw in excluded_keywords:
            pattern = rf"\b{re.escape(kw)}\b"
            if re.search(pattern, title_lower) or re.search(pattern, desc_lower):
                return MatchScore(
                    score=0.0,
                    reasoning=f"Excluded keyword '{kw}' found in posting.",
                    red_flags=["excluded_keyword"],
                )

        # Pre-filter: title must share tokens with at least one search keyword
        search = profile.get("search", {})
        search_keywords = search.get("keywords", [])
        if search_keywords and not self._title_matches_keywords(
            job.title, search_keywords
        ):
            log.info(
                "prefilter_title_skip",
                job_title=job.title,
                reason="no keyword overlap",
            )
            return MatchScore(
                score=0.0,
                reasoning=f"Title '{job.title}' has no relevance to search keywords.",
                red_flags=["title_mismatch"],
            )

        # Pre-filter: description must mention at least one required or preferred skill
        skills_config = profile.get("skills", {})
        all_skills = skills_config.get("required", []) + skills_config.get(
            "preferred", []
        )
        if (
            all_skills
            and job.description
            and not self._has_skill_overlap(job.description, all_skills)
        ):
            log.info(
                "prefilter_skill_skip",
                job_title=job.title,
                reason="no skill overlap",
            )
            return MatchScore(
                score=0.0,
                reasoning="No required or preferred skills found in job description.",
                red_flags=["no_skill_overlap"],
            )

        # JEV primary when configured: structured 0-1 fit, no chat cost.
        if jev_enabled():
            skills = profile.get("skills", {})
            search = profile.get("search", {})
            employment_type = getattr(job, "employment_type", "") or ""
            remote_policy = getattr(job, "remote_policy", "") or ""
            state = (
                f"Job: {job.title} at {job.company}; location: {job.location}; "
                f"type: {employment_type or 'not stated'}; remote: "
                f"{remote_policy or 'not stated'}.\n"
                f"Description: {job.description[:6000]}\n"
                f"Candidate: {profile.get('name', '')}; required skills: "
                f"{', '.join(skills.get('required', []))}; preferred: "
                f"{', '.join(skills.get('preferred', []))}; experience: "
                f"{search.get('experience_level', '')}. "
                f"Constraints: {_constraints_text(search)}."
            )
            fit = score_fit(state)
            if fit is not None:
                return MatchScore(
                    score=fit,
                    reasoning=f"JEV fit score {fit:.2f} (typesafe jev, structured).",
                    role_fit=(
                        "strong" if fit >= 0.90 else "possible" if fit >= 0.70 else "none"
                    ),
                    red_flags=["jev_scored"] if fit >= 0.70 else ["jev_low_fit"],
                )

        # Build prompt
        skills = profile.get("skills", {})
        search = profile.get("search", {})
        prompt = JOB_MATCH_TEMPLATE.render(
            job_title=job.title,
            company=job.company,
            location=job.location,
            description=job.description[:6000],  # Truncate very long descriptions
            profile_name=profile.get("name", ""),
            required_skills=skills.get("required", []),
            preferred_skills=skills.get("preferred", []),
            experience_level=search.get("experience_level", ""),
            remote_preference=search.get("remote_preference", ""),
            salary_minimum=search.get("salary_minimum", "Not specified"),
            excluded_companies=exclusions.get("companies", []),
            excluded_keywords=exclusions.get("keywords", []),
            employment_type=getattr(job, "employment_type", "") or "not stated",
            remote_policy=getattr(job, "remote_policy", "") or "not stated",
            timezone=getattr(job, "timezone", "") or "not stated",
            candidate_constraints=_constraints_text(search),
        )

        try:
            response = self.ai.complete(
                prompt=prompt,
                system="You are a precise job matching AI. Always respond with valid JSON only.",
                temperature=0.2,
            )
            return self._parse_response(response)
        except Exception as e:
            log.error("match_failed", job_id=job.external_id, error=str(e))
            # A failed match must never look like a neutral, queueable score.
            return MatchScore(
                score=0.0,
                reasoning=f"Matching failed: {e}",
                red_flags=["match_error"],
            )

    def _parse_response(self, response: str) -> MatchScore:
        """Parse the AI response into a MatchScore."""
        text = response.strip()

        # Strip markdown code fences (```json ... ``` or ``` ... ```)
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)

        # Extract JSON object from surrounding text (Llama models sometimes wrap)
        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            text = json_match.group()

        try:
            data = json.loads(text)

            # Type-validate and coerce
            score = data.get("score", 0.5)
            try:
                score = float(score)
            except (TypeError, ValueError):
                score = 0.5
            score = max(0.0, min(1.0, score))

            matched = data.get("matched_skills", [])
            if not isinstance(matched, list):
                matched = []
            matched = [str(s) for s in matched]

            missing = data.get("missing_skills", [])
            if not isinstance(missing, list):
                missing = []
            missing = [str(s) for s in missing]

            flags = data.get("red_flags", [])
            if not isinstance(flags, list):
                flags = []
            flags = [str(f) for f in flags]

            return MatchScore(
                score=score,
                reasoning=str(data.get("reasoning", "")),
                matched_skills=matched,
                missing_skills=missing,
                role_fit=str(data.get("role_fit", "")),
                red_flags=flags,
            )
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("match_parse_error", error=str(e), response=text[:200])
            return MatchScore(
                score=0.5,
                reasoning=f"Failed to parse AI response: {e}",
                red_flags=["parse_error"],
            )
