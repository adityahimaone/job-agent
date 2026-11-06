"""Extracted pipeline step functions — each handles one phase of the pipeline."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from job_agent.ai.cold_email import ColdEmailGenerator
from job_agent.ai.client import AIClient
from job_agent.ai.cover_letter import CoverLetterGenerator
from job_agent.ai.job_matcher import JobMatcher
from job_agent.ai.resume_tailor import ResumeTailor
from job_agent.config import Settings
from job_agent.db.models import (
    ApplicationStatus,
    Job,
    JobStatus,
    OutreachStatus,
    Platform,
)
from job_agent.db.repository import (
    ApplicationRepository,
    MatchResultRepository,
    OutreachRepository,
)
from job_agent.orchestrator.verification import (
    CONFIRMED,
    PENDING_HUMAN,
    verify_driver_result,
)
from job_agent.platforms.base import JobPosting, PlatformDriver
from job_agent.utils.json_helpers import parse_json_list
from job_agent.utils.logging import get_logger

log = get_logger(__name__)


def match_job(
    *,
    job: Job,
    posting: JobPosting,
    matcher: JobMatcher,
    match_repo: MatchResultRepository,
    profile: dict,
) -> object:
    """Run AI matching on a single job. Updates job status to MATCHED and stores result."""
    score = matcher.match(posting, profile)
    match_repo.create(
        job_id=job.id,
        score=score.score,
        reasoning=score.reasoning,
        matched_skills=score.matched_skills,
        missing_skills=score.missing_skills,
        role_fit=score.role_fit,
        red_flags=score.red_flags,
    )
    job.status = JobStatus.MATCHED
    log.info("job_matched", job_id=job.id, score=score.score, title=job.title)
    return score


def decide_job(
    job: Job, score, settings: Settings, *, require_approval: bool = False
) -> str:
    """Decide what to do with a matched job based on thresholds.

    Returns: "auto_apply", "queue", or "skip". When ``require_approval`` is
    set, high scorers are queued for a human instead of submitted unattended.
    """
    if score.score >= settings.matching.auto_apply_threshold:
        if require_approval:
            job.status = JobStatus.QUEUED
            log.info("job_queued_for_approval", job_id=job.id, score=score.score)
            return "queue"
        job.status = JobStatus.AUTO_APPROVED
        log.info("job_auto_approved", job_id=job.id, score=score.score)
        return "auto_apply"
    elif score.score >= settings.matching.review_threshold:
        job.status = JobStatus.QUEUED
        log.info("job_queued", job_id=job.id, score=score.score)
        return "queue"
    else:
        job.status = JobStatus.SKIPPED
        log.info("job_skipped", job_id=job.id, score=score.score)
        return "skip"


def daily_cap_reached(
    app_repo: ApplicationRepository, settings: Settings, platform_value: str
) -> bool:
    """Enforce the configured per-platform daily application cap."""
    cfg = getattr(settings.platforms, platform_value, None)
    cap = int(getattr(cfg, "max_applications_per_day", 0) or 0)
    if cap <= 0:
        return False
    try:
        platform = Platform(platform_value)
    except ValueError:
        return False
    used = app_repo.count_today(platform)
    if used >= cap:
        log.warning(
            "daily_application_cap_reached",
            platform=platform_value,
            used=used,
            cap=cap,
        )
        return True
    return False


def _document_key(posting: JobPosting) -> str:
    raw = f"{posting.platform.value}_{posting.external_id or posting.company or 'job'}"
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in raw)
    return cleaned[:80] or "application"


def prepare_documents(
    *,
    settings: Settings | None,
    posting: JobPosting,
    profile: dict,
    candidate_summary: str,
    matched_skills: list[str],
    resume_tailor: ResumeTailor,
    cover_letter_gen: CoverLetterGenerator,
    ai_client: AIClient | None = None,
    candidate=None,
    template_set=None,
) -> tuple[str, str, dict]:
    """Return (resume_path, cover_letter_path, manifest).

    Grounded mode renders the human-editable Markdown templates from verified
    candidate facts. Legacy mode keeps the original LLM-only behaviour.
    """
    grounded = bool(
        settings
        and settings.content.mode == "grounded"
        and candidate is not None
        and template_set is not None
    )

    if not grounded:
        resume_path = resume_tailor.tailor_and_save(posting, matched_skills)
        cl_path = ""
        try:
            cl_path = cover_letter_gen.generate_and_save(
                posting, candidate_summary, matched_skills
            )
        except Exception as exc:
            log.warning(
                "cover_letter_generation_failed",
                company=posting.company,
                error=str(exc),
            )
        return resume_path, cl_path, {"mode": "legacy", "email_path": ""}

    from job_agent.content.generator import generate_documents

    if getattr(ai_client, "provider", "") == "none":
        ai_client = None  # deterministic, grounded rendering without an LLM

    docs = generate_documents(
        posting=posting,
        profile=profile,
        candidate=candidate,
        template_set=template_set,
        ai_client=ai_client,
        matched_skills=matched_skills,
        prompt_version=settings.content.prompt_version,
    )
    out_dir: Path = settings.documents_dir
    key = _document_key(posting)
    resume_path = str(out_dir / f"{key}_resume.pdf")
    try:
        resume_tailor.markdown_to_pdf(docs.resume_markdown, resume_path)
    except Exception as exc:
        log.warning("resume_pdf_failed_using_markdown", error=str(exc))
        resume_path = str(out_dir / f"{key}_resume.md")
        Path(resume_path).write_text(docs.resume_markdown, encoding="utf-8")

    cl_path = str(out_dir / f"{key}_cover_letter.txt")
    Path(cl_path).write_text(docs.cover_letter, encoding="utf-8")
    email_path = str(out_dir / f"{key}_email.txt")
    Path(email_path).write_text(
        f"Subject: {docs.email_subject}\n\n{docs.email_body}", encoding="utf-8"
    )

    manifest = {
        "mode": "grounded",
        "email_path": email_path,
        "email_subject": docs.email_subject,
        "connection_note": docs.connection_note,
        "gig_profile": docs.gig_profile,
        "prompt_version": docs.prompt_version,
        "candidate_version": docs.candidate_version,
        "warnings": docs.warnings,
        "resume_path": resume_path,
        "cover_letter_path": cl_path,
    }
    if docs.warnings:
        log.warning("document_grounding_warnings", warnings=docs.warnings[:5])
    return resume_path, cl_path, manifest


def apply_to_job(
    *,
    job: Job,
    posting: JobPosting,
    driver: PlatformDriver,
    resume_tailor: ResumeTailor,
    cover_letter_gen: CoverLetterGenerator,
    app_repo: ApplicationRepository,
    candidate_summary: str,
    matched_skills: list[str],
    settings: Settings | None = None,
    profile: dict | None = None,
    ai_client: AIClient | None = None,
    candidate=None,
    template_set=None,
) -> bool:
    """Tailor documents and apply. Returns True only for a recorded submission.

    Safety gates: dry-run is a pure no-op, every submission needs an approval,
    and per-platform daily caps are enforced. Success is recorded with an
    explicit verification state instead of being assumed from a clicked button.
    """
    if settings and settings.agent.dry_run:
        log.info("dry_run_apply_skipped", job_id=job.id, title=job.title)
        return False
    if (
        settings
        and settings.agent.require_approval_for_all
        and job.status != JobStatus.APPROVED
    ):
        log.warning(
            "approval_required_before_submit", job_id=job.id, status=str(job.status)
        )
        return False
    if settings and daily_cap_reached(app_repo, settings, job.platform.value):
        return False

    if not driver.is_job_open(posting):
        job.status = JobStatus.APPLY_FAILED
        app_repo.create_or_update(
            job_id=job.id,
            resume_path="",
            cover_letter_path="",
            status=ApplicationStatus.FAILED,
            error_message="Job closed before applying",
        )
        return False

    resume_path, cl_path, manifest = prepare_documents(
        settings=settings,
        posting=posting,
        profile=profile or {},
        candidate_summary=candidate_summary,
        matched_skills=matched_skills,
        resume_tailor=resume_tailor,
        cover_letter_gen=cover_letter_gen,
        ai_client=ai_client,
        candidate=candidate,
        template_set=template_set,
    )

    success = driver.apply(posting, resume_path, cover_letter_path=cl_path)
    verification = verify_driver_result(driver, success)
    common = {
        "job_id": job.id,
        "resume_path": resume_path,
        "cover_letter_path": cl_path,
        "verification_status": verification.state,
        "verification_note": verification.note,
        "documents_manifest": json.dumps(manifest, ensure_ascii=False),
        "prompt_version": manifest.get("prompt_version", ""),
    }

    if verification.ok:
        job.status = JobStatus.APPLIED
        app_repo.create_or_update(
            **common,
            status=(
                ApplicationStatus.CONFIRMED
                if verification.state == CONFIRMED
                else ApplicationStatus.SUBMITTED
            ),
            error_message=None,
            applied_at=datetime.now(timezone.utc),
            approval_source="approved_queue",
        )
        log.info(
            "job_applied",
            job_id=job.id,
            title=job.title,
            company=job.company,
            verification=verification.state,
        )
        return True

    if verification.state == PENDING_HUMAN:
        job.status = JobStatus.PENDING_HUMAN
        app_repo.create_or_update(
            **common,
            status=ApplicationStatus.PENDING_HUMAN,
            error_message=verification.note or "Waiting for human submit",
        )
        log.info("job_pending_human_submit", job_id=job.id)
    else:
        job.status = JobStatus.APPLY_FAILED
        app_repo.create_or_update(
            **common,
            status=ApplicationStatus.FAILED,
            error_message=verification.note
            or "Application submission returned failure",
        )
        log.warning("job_apply_failed", job_id=job.id, title=job.title)
    return False


def generate_cold_email_draft(
    *,
    job: Job,
    ai_client: AIClient,
    settings: Settings,
    profile: dict,
    session,
    candidate=None,
) -> None:
    """Generate a cold email draft for a successfully applied job. Non-fatal on failure."""
    if getattr(ai_client, "provider", "") == "none":
        return
    try:
        outreach_repo = OutreachRepository(session)
        candidate_summary = build_candidate_summary(profile, candidate)

        matched_skills = parse_json_list(
            job.match_result.matched_skills if job.match_result else None
        )

        recipient_name = "Hiring Manager"
        recipient_title = "Recruiter"

        if outreach_repo.exists_email_for_job(job.id, recipient_name):
            return

        generator = ColdEmailGenerator(ai_client, settings)
        email_data = generator.generate(
            job_title=job.title,
            company=job.company,
            recipient_name=recipient_name,
            recipient_title=recipient_title,
            matched_skills=matched_skills,
            candidate_summary=candidate_summary,
            job_description=job.description or "",
        )

        outreach_repo.create(
            platform=job.platform,
            recipient_name=recipient_name,
            recipient_title=recipient_title,
            recipient_company=job.company,
            recipient_profile_url="",
            message_type="email",
            message_text=json.dumps(email_data),
            status=OutreachStatus.DRAFTED,
            related_job_id=job.id,
        )
        log.info("cold_email_draft_generated", job_id=job.id, company=job.company)
    except Exception as e:
        log.warning(
            "cold_email_draft_failed",
            job_id=job.id,
            company=job.company,
            error=str(e),
        )


def build_candidate_summary(profile: dict, candidate=None) -> str:
    """Build a candidate summary string from a profile dict.

    When a grounded :class:`CandidateProfile` is supplied, verified facts are
    included so cover letters and cold emails stop being a bare skills list.
    """
    if candidate is not None:
        facts = getattr(candidate, "grounded_facts_text", lambda: "")()
        if facts:
            return facts
    parts: list[str] = []
    if name := profile.get("name"):
        parts.append(f"Target Role: {name}")
    search = profile.get("search", {})
    if exp := search.get("experience_level"):
        parts.append(f"Experience Level: {exp}")
    skills = profile.get("skills", {})
    if req := skills.get("required"):
        parts.append(f"Required Skills: {', '.join(req)}")
    if pref := skills.get("preferred"):
        parts.append(f"Preferred Skills: {', '.join(pref)}")
    return "\n".join(parts)


def get_matched_skills_for_job(job: Job) -> list[str]:
    """Extract matched skills list from a job's match result."""
    if not job.match_result or not job.match_result.matched_skills:
        return []
    return parse_json_list(job.match_result.matched_skills)
