"""Tests for the grounded, offline document engine.

Only pytest + tempfile are used. No network, no AI keys, no full-suite deps.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from job_agent.content import (
    CandidateProfile,
    GeneratedDocuments,
    JobRequirements,
    check_grounding,
    default_template_text,
    extract_requirements,
    generate_documents,
    load_candidate_profile,
    load_template_set,
    save_documents,
)
from job_agent.db.models import Platform
from job_agent.platforms.base import JobPosting

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "config" / "templates"
CANDIDATE_FILE = TEMPLATES_DIR / "candidate_profile.md"

# The templates that are meant to be rendered (not the knowledge base / README).
RENDERABLE_TEMPLATES = [
    "resume_master",
    "cover_letter",
    "email_apply",
    "connection_note",
    "gig_profile",
]


@pytest.fixture
def work_dir():
    """A private temp dir via tempfile (independent of pytest's shared tmp base)."""
    with tempfile.TemporaryDirectory(prefix="jobagent-content-") as directory:
        yield Path(directory)


def _make_job() -> JobPosting:
    return JobPosting(
        external_id="demo-1",
        platform=Platform.LINKEDIN,
        title="Frontend Engineer",
        company="Acme",
        location="Remote",
        description=(
            "Part-time remote role. Must have React, TypeScript, Next.js and "
            "design systems. Nice to have Go. GMT+7 timezone alignment."
        ),
        url="https://example.com/jobs/1",
    )


def _render_ctx(job: JobPosting) -> dict:
    """A rich context mirroring generate_documents, built by hand."""
    candidate = load_candidate_profile(CANDIDATE_FILE)
    requirements = extract_requirements(job.description, title=job.title)
    return {
        "candidate": candidate.as_context(),
        "job": {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "description": job.description,
            "url": job.url,
            "employment_type": job.employment_type,
            "remote_policy": job.remote_policy,
            "timezone": job.timezone,
        },
        "requirements": requirements.as_context(),
        "evidence": candidate.facts[:3],
        "matched_skills": ["React", "TypeScript", "Next.js"],
        "profile": {"name": "test"},
        "greeting": f"Dear {job.company} hiring team,",
        "signoff": f"Best regards,\n{candidate.name}",
    }


# --------------------------------------------------------------------------- #
# candidate.py
# --------------------------------------------------------------------------- #


class TestCandidateProfile:
    def test_shipped_profile_has_expected_name_and_skills(self):
        profile = load_candidate_profile(CANDIDATE_FILE)
        assert profile.name == "Aditya Himawan"
        assert profile.email == "adityahimaone@gmail.com"
        assert profile.years_experience == 4
        assert profile.headline
        # React lives under the Frontend category (and skills is a dict).
        assert isinstance(profile.skills, dict)
        assert any("React" in items for items in profile.skills.values())
        # Grounded, canonical claims are present.
        assert any("15K+ users" in fact for fact in profile.facts)
        assert profile.roles and profile.projects
        assert profile.do_not_claim
        assert len(profile.version) == 12

    def test_missing_file_returns_safe_empty_profile(self, work_dir):
        profile = load_candidate_profile(work_dir / "does_not_exist.md")
        assert profile.name == "Candidate"
        assert profile.facts == []
        assert profile.roles == []
        assert profile.version  # still computes without raising

    def test_parser_is_tolerant(self, work_dir):
        path = work_dir / "profile.md"
        # Empty file must not raise.
        path.write_text("", encoding="utf-8")
        assert load_candidate_profile(path).name == "Candidate"
        # No front matter for most fields; '*' bullets; missing Experience /
        # Projects sections; front matter scalars.
        path.write_text(
            "---\nname: Jane\n---\n\n"
            "## Verified facts\n* Did a thing\n* Another thing\n\n"
            "## Do not claim\n- 9 years\n",
            encoding="utf-8",
        )
        profile = load_candidate_profile(path)
        assert profile.name == "Jane"
        assert profile.facts == ["Did a thing", "Another thing"]
        assert profile.do_not_claim == ["9 years"]
        assert profile.roles == []

    def test_version_is_a_content_hash(self):
        a = CandidateProfile(name="A", raw_markdown="hello")
        b = CandidateProfile(name="A", raw_markdown="world")
        assert a.version != b.version
        assert len(a.version) == 12

    def test_as_context_exposes_extra_front_matter(self):
        profile = load_candidate_profile(CANDIDATE_FILE)
        ctx = profile.as_context()
        # `education` is an extra front-matter key, surfaced automatically.
        assert isinstance(ctx.get("education"), list)
        assert ctx["years_experience_label"] == "4+ years"
        assert (
            ctx["constraints"]["availability"]
            == "remote roles (full-time, contract, or part-time)"
        )
        assert profile.grounded_facts_text().startswith("- ")


# --------------------------------------------------------------------------- #
# templates.py
# --------------------------------------------------------------------------- #


class TestTemplateSet:
    def test_each_shipped_template_renders_with_company(self):
        template_set = load_template_set(TEMPLATES_DIR)
        job = _make_job()
        for name in RENDERABLE_TEMPLATES:
            assert template_set.has(name)
            rendered = template_set.render(name, **_render_ctx(job))
            assert rendered.strip(), f"{name} rendered empty"
            assert "Acme" in rendered, f"{name} missing company name"
            # Strict rendering resolved every variable (no leftovers).
            assert "{{" not in rendered, f"{name} has unresolved variable"
            assert "{#" not in rendered, f"{name} leaked a Jinja comment"

    def test_built_in_fallbacks_exist_for_every_template(self):
        for name in RENDERABLE_TEMPLATES + ["candidate_profile"]:
            assert default_template_text(name).strip()

    def test_missing_directory_falls_back_to_built_ins(self, work_dir):
        template_set = load_template_set(work_dir / "no_such_dir")
        rendered = template_set.render(
            "cover_letter",
            candidate={"name": "A", "headline": "Dev"},
            job={"title": "Eng", "company": "Acme"},
            matched_skills=["React"],
            evidence=["did a thing"],
            greeting="Hi",
            signoff="Bye",
        )
        assert "Acme" in rendered and rendered.strip()

    def test_undefined_variable_does_not_raise(self, work_dir):
        directory = work_dir / "tpl"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "cover_letter.md").write_text(
            "Hello {{ totally_missing }} at {{ job.company }}", encoding="utf-8"
        )
        template_set = load_template_set(directory)
        rendered = template_set.render("cover_letter", job={"company": "Acme"})
        assert "Acme" in rendered
        assert "totally_missing" not in rendered


# --------------------------------------------------------------------------- #
# requirements.py
# --------------------------------------------------------------------------- #


class TestExtractRequirements:
    def test_detects_part_time_and_remote(self):
        req = extract_requirements(
            "This is a part-time, fully remote position. Must have React.",
            title="Frontend Engineer",
        )
        assert isinstance(req, JobRequirements)
        assert "part_time" in req.employment_types
        assert req.remote_policy == "remote"
        assert "React" in req.must_have

    def test_must_have_vs_nice_to_have(self):
        req = extract_requirements(
            "Must have React and TypeScript. Nice to have Docker and Go.",
            title="Engineer",
        )
        assert "React" in req.must_have
        assert "TypeScript" in req.must_have
        assert "Docker" in req.nice_to_have
        assert "Docker" not in req.must_have

    def test_never_raises_on_empty_or_huge_input(self):
        assert extract_requirements("").must_have == []
        huge = "remote React " * 5000
        req = extract_requirements(huge, title="Eng")
        assert req.remote_policy == "remote"


# --------------------------------------------------------------------------- #
# grounding.py
# --------------------------------------------------------------------------- #


class TestCheckGrounding:
    def test_flags_made_up_url_and_do_not_claim_phrase(self):
        profile = load_candidate_profile(CANDIDATE_FILE)
        warnings = check_grounding(
            "See http://fake-portfolio.example and we have 6 years of experience "
            "serving 999K users.",
            profile,
        )
        assert any("URL" in w for w in warnings), warnings
        assert any("do-not-claim" in w.lower() for w in warnings), warnings
        assert any("Numeric" in w for w in warnings), warnings

    def test_grounded_text_produces_no_warnings(self):
        profile = load_candidate_profile(CANDIDATE_FILE)
        warnings = check_grounding(
            "4+ years of experience, a product with 15K+ users, "
            "https://adityahimaone.space, adityahimaone@gmail.com",
            profile,
        )
        assert warnings == []

    def test_flags_foreign_email(self):
        profile = load_candidate_profile(CANDIDATE_FILE)
        warnings = check_grounding("Reach me at someone@elsewhere.com", profile)
        assert any("Email" in w for w in warnings)


# --------------------------------------------------------------------------- #
# generator.py
# --------------------------------------------------------------------------- #


class _UngroundedAI:
    def complete(self, prompt, system=""):
        return (
            "=== COVER_LETTER ===\nWe served 999K users at http://fake.example\n"
            "=== EMAIL_SUBJECT ===\nHello\n"
            "=== EMAIL_BODY ===\nReach me at fake@fake.example\n"
        )


class _GroundedAI:
    def complete(self, prompt, system=""):
        return (
            "=== COVER_LETTER ===\n4+ years at https://adityahimaone.space\n"
            "=== EMAIL_SUBJECT ===\nHello\n"
            "=== EMAIL_BODY ===\nContact adityahimaone@gmail.com\n"
        )


class TestGenerateAndSave:
    def test_generate_offline_and_save_all_files(self, work_dir):
        candidate = load_candidate_profile(CANDIDATE_FILE)
        template_set = load_template_set(TEMPLATES_DIR)
        job = _make_job()

        docs = generate_documents(
            posting=job,
            profile={"name": "test"},
            candidate=candidate,
            template_set=template_set,
            ai_client=None,
            matched_skills=["React", "TypeScript"],
        )
        assert isinstance(docs, GeneratedDocuments)
        assert docs.resume_markdown and docs.cover_letter
        assert docs.email_subject and docs.email_body
        assert docs.connection_note and docs.gig_profile
        assert "Acme" in docs.cover_letter
        assert docs.candidate_version == candidate.version
        assert docs.prompt_version == "2026-11-06"
        assert docs.warnings == []
        assert docs.as_dict()["cover_letter"] == docs.cover_letter

        out_dir = work_dir / "documents"
        # Key contains spaces/symbols to exercise sanitisation.
        paths = save_documents(docs, out_dir, "Acme/Frontend!")
        assert set(paths) == {
            "resume",
            "cover_letter",
            "email",
            "connection_note",
            "gig_profile",
            "manifest",
        }
        for path in paths.values():
            assert Path(path).is_file()

        # Filename is sanitised to alnum/dash/underscore only.
        assert Path(paths["resume"]).name == "Acme_Frontend_resume.md"

        email_text = Path(paths["email"]).read_text(encoding="utf-8")
        assert email_text.startswith("Subject: ")
        assert "\n\n" in email_text
        assert "Acme" in email_text

        manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
        assert manifest["candidate_version"] == candidate.version
        assert manifest["key"] == "Acme_Frontend"

    def test_ai_rewrite_discarded_when_ungrounded(self):
        candidate = load_candidate_profile(CANDIDATE_FILE)
        template_set = load_template_set(TEMPLATES_DIR)
        docs = generate_documents(
            posting=_make_job(),
            profile={},
            candidate=candidate,
            template_set=template_set,
            ai_client=_UngroundedAI(),
        )
        # The deterministic letter is kept, the fabricated URL is gone.
        assert "Dear Acme hiring team," in docs.cover_letter
        assert "fake.example" not in docs.cover_letter
        assert "fake@fake.example" not in docs.email_body
        # The grounding warnings were recorded.
        assert docs.warnings

    def test_ai_rewrite_accepted_when_fully_grounded(self):
        candidate = load_candidate_profile(CANDIDATE_FILE)
        template_set = load_template_set(TEMPLATES_DIR)
        docs = generate_documents(
            posting=_make_job(),
            profile={},
            candidate=candidate,
            template_set=template_set,
            ai_client=_GroundedAI(),
        )
        assert docs.cover_letter == "4+ years at https://adityahimaone.space"
        assert docs.email_body == "Contact adityahimaone@gmail.com"
