"""Regression tests for the Phase 0 safety / correctness fixes.

These protect the behaviours that matter most before real applications are
submitted: dry-run purity, an approval gate, applied_at, daily caps and honest
submission verification, plus dashboard auth + CSRF.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from job_agent.config import Settings
from job_agent.db.models import (
    ApplicationStatus,
    Base,
    JobStatus,
    Platform,
)
from job_agent.db.repository import ApplicationRepository, JobRepository
from job_agent.orchestrator.pipeline_steps import (
    apply_to_job,
    daily_cap_reached,
    decide_job,
)
from job_agent.orchestrator.timing import StageTimer
from job_agent.orchestrator.verification import (
    CONFIRMED,
    PENDING_HUMAN,
    UNVERIFIED,
    snapshot_verdict,
    verify_driver_result,
)
from job_agent.platforms.base import JobPosting


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def settings():
    return Settings(
        _env_file=None,
        anthropic_api_key="test-key",
        database_url="sqlite:///:memory:",
        flask_secret_key="test-secret",
    )


def _make_job(db_session, status=JobStatus.APPROVED):
    job = JobRepository(db_session).create(
        external_id="ext-safe-1",
        platform=Platform.LINKEDIN,
        title="Design Engineer",
        company="Acme",
        location="Remote",
        description="React and TypeScript",
        url="https://example.com/job/1",
    )
    job.status = status
    db_session.commit()
    return job


def _posting():
    return JobPosting(
        external_id="ext-safe-1",
        platform=Platform.LINKEDIN,
        title="Design Engineer",
        company="Acme",
        description="React and TypeScript",
        url="https://example.com/job/1",
        employment_type="part_time",
        remote=True,
    )


def _mocks(success=True):
    driver = MagicMock()
    driver.apply.return_value = success
    driver.is_job_open.return_value = True
    # Avoid the MagicMock last_result being mistaken for a rich Ego result.
    driver.last_result = None
    resume_tailor = MagicMock()
    resume_tailor.tailor_and_save.return_value = "/tmp/resume.pdf"
    cl_gen = MagicMock()
    cl_gen.generate_and_save.return_value = "/tmp/cover.txt"
    return driver, resume_tailor, cl_gen


def _apply(db_session, job, settings, success=True, **kwargs):
    driver, resume_tailor, cl_gen = _mocks(success)
    return apply_to_job(
        job=job,
        posting=_posting(),
        driver=driver,
        resume_tailor=resume_tailor,
        cover_letter_gen=cl_gen,
        app_repo=ApplicationRepository(db_session),
        candidate_summary="React engineer",
        matched_skills=["React"],
        settings=settings,
        **kwargs,
    ), driver


class TestDecideJob:
    def test_high_score_is_queued_when_approval_required(self, db_session, settings):
        job = _make_job(db_session, JobStatus.MATCHED)
        score = MagicMock()
        score.score = 0.99
        assert decide_job(job, score, settings, require_approval=True) == "queue"
        assert job.status == JobStatus.QUEUED

    def test_high_score_auto_applies_when_explicitly_allowed(
        self, db_session, settings
    ):
        job = _make_job(db_session, JobStatus.MATCHED)
        score = MagicMock()
        score.score = 0.99
        assert decide_job(job, score, settings, require_approval=False) == "auto_apply"


class TestDryRunPurity:
    def test_dry_run_writes_nothing(self, db_session, settings):
        job = _make_job(db_session)
        settings.agent.dry_run = True
        result, driver = _apply(db_session, job, settings)
        assert result is False
        driver.apply.assert_not_called()
        assert ApplicationRepository(db_session).get_by_job_id(job.id) is None
        assert job.status == JobStatus.APPROVED  # unchanged

    def test_approval_gate_blocks_unapproved_job(self, db_session, settings):
        job = _make_job(db_session, JobStatus.QUEUED)
        settings.agent.require_approval_for_all = True
        result, driver = _apply(db_session, job, settings)
        assert result is False
        driver.apply.assert_not_called()
        assert ApplicationRepository(db_session).get_by_job_id(job.id) is None


class TestAppliedAtAndVerification:
    def test_success_records_applied_at(self, db_session, settings):
        job = _make_job(db_session)
        settings.agent.require_approval_for_all = True  # job is APPROVED
        result, _ = _apply(db_session, job, settings)
        assert result is True
        app = ApplicationRepository(db_session).get_by_job_id(job.id)
        assert app is not None
        assert app.applied_at is not None
        assert app.verification_status == UNVERIFIED
        assert app.status == ApplicationStatus.SUBMITTED
        assert job.status == JobStatus.APPLIED

    def test_daily_cap_blocks_extra_submissions(self, db_session, settings):
        job = _make_job(db_session)
        app_repo = ApplicationRepository(db_session)
        app_repo.create(
            job_id=job.id,
            status=ApplicationStatus.SUBMITTED,
            applied_at=datetime.now(timezone.utc),
        )
        db_session.commit()
        settings.platforms.linkedin.max_applications_per_day = 1
        assert daily_cap_reached(app_repo, settings, "linkedin") is True
        settings.platforms.linkedin.max_applications_per_day = 25
        assert daily_cap_reached(app_repo, settings, "linkedin") is False


class TestVerification:
    def test_legacy_true_is_unverified_not_confirmed(self):
        driver = MagicMock()
        driver.last_result = None
        verdict = verify_driver_result(driver, True)
        assert verdict.state == UNVERIFIED
        assert verdict.ok is True

    def test_failed_driver_is_reported_as_failed(self):
        driver = MagicMock()
        driver.last_result = None
        verdict = verify_driver_result(driver, False)
        assert verdict.state == "failed"
        assert verdict.ok is False

    def test_ego_confirmation_is_trusted(self):
        driver = MagicMock()
        driver.last_result = MagicMock(status=CONFIRMED, note="confirmation text")
        assert verify_driver_result(driver, True).state == CONFIRMED

    def test_pending_human_is_not_success(self):
        driver = MagicMock()
        driver.last_result = MagicMock(status=PENDING_HUMAN, note="handoff")
        assert verify_driver_result(driver, False).state == PENDING_HUMAN

    def test_snapshot_phrases(self):
        assert snapshot_verdict("Your application submitted").state == CONFIRMED
        assert snapshot_verdict("Please complete all required fields").state == "failed"
        assert snapshot_verdict("Welcome back").state == UNVERIFIED


class TestBatchDedup:
    def test_filter_existing_returns_pairs_in_one_query(self, db_session):
        repo = JobRepository(db_session)
        repo.create(external_id="a", platform=Platform.LINKEDIN, title="A", company="X")
        repo.create(external_id="b", platform=Platform.INDEED, title="B", company="Y")
        db_session.commit()
        found = repo.filter_existing(
            [
                ("a", Platform.LINKEDIN),
                ("c", Platform.LINKEDIN),
                ("b", Platform.INDEED),
            ]
        )
        assert found == {("a", "linkedin"), ("b", "indeed")}


class TestStageTimer:
    def test_records_stage_and_elapsed(self):
        timer = StageTimer()
        with timer.stage("unit"):
            pass
        assert "unit" in timer.snapshot()
        assert timer.total_ms() >= 0


class TestDashboardSecurity:
    def test_auth_and_csrf_are_enforced(self, settings):
        from job_agent.dashboard.app import create_app

        settings.dashboard.auth_enabled = True
        settings.dashboard.csrf_enabled = True
        settings.dashboard.auth_token = "test-token"
        app = create_app(settings)
        client = app.test_client()

        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 302
        assert "/login" in client.get("/").headers["Location"]

        with client.session_transaction() as sess:
            sess["ja_auth"] = True
            sess["csrf"] = "csrf-value"
        assert client.post("/queue/approve-all").status_code == 403
        assert (
            client.post(
                "/queue/approve-all", headers={"X-CSRF-Token": "csrf-value"}
            ).status_code
            != 403
        )

    def test_default_code_settings_are_permissive_for_tests(self, settings):
        from job_agent.dashboard.app import create_app

        app = create_app(settings)
        assert app.config["AUTH_ENABLED"] is False
        assert app.config["CSRF_ENABLED"] is False
