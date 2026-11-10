"""SQLAlchemy ORM models."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Platform(PyEnum):
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    GLASSDOOR = "glassdoor"
    ZIPRECRUITER = "ziprecruiter"
    DICE = "dice"
    WELLFOUND = "wellfound"
    # Structured feeds / job boards (Phase 3)
    REMOTIVE = "remotive"
    REMOTEOK = "remoteok"
    ARBEITNOW = "arbeitnow"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    JOBSTREET = "jobstreet"
    GLINTS = "glints"
    KALIBRR = "kalibrr"
    SEEK = "seek"
    DEALLS = "dealls"
    # Muse fusion lane: jobs found by Muse, imported via `job-agent muse import`.
    MUSE = "muse"
    # AI-training / expert networks
    ALIGNERR = "alignerr"
    OUTLIER = "outlier"
    DATAANNOTATION = "dataannotation"
    MERCOR = "mercor"
    OTHER = "other"


class JobStatus(PyEnum):
    DISCOVERED = "discovered"
    MATCHED = "matched"
    QUEUED = "queued"
    AUTO_APPROVED = "auto_approved"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    APPLY_FAILED = "apply_failed"
    SKIPPED = "skipped"
    FILTERED = "filtered"
    PENDING_HUMAN = "pending_human"


class ApplicationStatus(PyEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    UNVERIFIED = "unverified"
    PENDING_HUMAN = "pending_human"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"


class GigStatus(PyEnum):
    DISCOVERED = "discovered"
    ELIGIBLE = "eligible"
    APPLIED = "applied"
    ASSESSMENT = "assessment"
    ACTIVE = "active"
    REJECTED = "rejected"
    NOT_ELIGIBLE = "not_eligible"


class OutreachStatus(PyEnum):
    PENDING = "pending"
    DRAFTED = "drafted"
    SENT = "sent"
    ACCEPTED = "accepted"
    REPLIED = "replied"
    FOLLOW_UP_SENT = "follow_up_sent"
    FAILED = "failed"


class RunStatus(PyEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform))
    title: Mapped[str] = mapped_column(String(500))
    company: Mapped[str] = mapped_column(String(255))
    location: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(2048), default="")
    salary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    easy_apply: Mapped[bool] = mapped_column(Boolean, default=False)
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    # Structured employment metadata (Phase 3). Empty string means "unknown".
    employment_type: Mapped[str] = mapped_column(String(64), default="", index=True)
    remote_policy: Mapped[str] = mapped_column(String(64), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="")
    hours_per_week: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(16), default="")
    salary_period: Mapped[str] = mapped_column(String(32), default="")
    source: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), default=JobStatus.DISCOVERED, index=True
    )
    bookmarked: Mapped[bool] = mapped_column(Boolean, default=False)
    profile_name: Mapped[str] = mapped_column(String(255), default="")
    duplicate_of_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("jobs.id"), nullable=True, default=None
    )
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    match_result: Mapped[MatchResult | None] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )
    application: Mapped[Application | None] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Job {self.id}: {self.title} @ {self.company}>"


class MatchResult(Base):
    __tablename__ = "match_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), unique=True)
    score: Mapped[float] = mapped_column(Float)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    matched_skills: Mapped[str] = mapped_column(Text, default="")  # JSON list
    missing_skills: Mapped[str] = mapped_column(Text, default="")  # JSON list
    role_fit: Mapped[str] = mapped_column(Text, default="")
    red_flags: Mapped[str] = mapped_column(Text, default="")  # JSON list
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    job: Mapped[Job] = relationship(back_populates="match_result")

    def __repr__(self) -> str:
        return f"<MatchResult job_id={self.job_id} score={self.score}>"


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), unique=True)
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus), default=ApplicationStatus.PENDING
    )
    resume_path: Mapped[str] = mapped_column(String(1024), default="")
    cover_letter_path: Mapped[str] = mapped_column(String(1024), default="")
    screenshot_path: Mapped[str] = mapped_column(String(1024), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    verification_status: Mapped[str] = mapped_column(String(32), default="")
    verification_note: Mapped[str] = mapped_column(Text, default="")
    approval_source: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[str] = mapped_column(String(64), default="")
    documents_manifest: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    job: Mapped[Job] = relationship(back_populates="application")

    def __repr__(self) -> str:
        return f"<Application job_id={self.job_id} status={self.status.value}>"


class OutreachMessage(Base):
    __tablename__ = "outreach_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform))
    recipient_name: Mapped[str] = mapped_column(String(255))
    recipient_title: Mapped[str] = mapped_column(String(255), default="")
    recipient_company: Mapped[str] = mapped_column(String(255), default="")
    recipient_profile_url: Mapped[str] = mapped_column(String(2048), default="")
    message_type: Mapped[str] = mapped_column(String(50))  # connection, inmail
    message_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[OutreachStatus] = mapped_column(
        Enum(OutreachStatus), default=OutreachStatus.PENDING
    )
    related_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id"), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    @property
    def email_subject(self) -> str:
        """Parse subject from JSON message_text for email-type messages."""
        if self.message_type != "email":
            return ""
        try:
            data = json.loads(self.message_text)
            return data.get("subject", "")
        except (json.JSONDecodeError, TypeError):
            return ""

    @property
    def email_body(self) -> str:
        """Parse body from JSON message_text for email-type messages."""
        if self.message_type != "email":
            return ""
        try:
            data = json.loads(self.message_text)
            return data.get("body", self.message_text)
        except (json.JSONDecodeError, TypeError):
            return self.message_text

    def __repr__(self) -> str:
        return f"<OutreachMessage {self.recipient_name} ({self.status.value})>"


class PlatformCredential(Base):
    __tablename__ = "platform_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform), unique=True)
    username: Mapped[str] = mapped_column(String(255))
    encrypted_password: Mapped[str] = mapped_column(Text)
    extra_data: Mapped[str] = mapped_column(Text, default="")  # JSON encrypted blob
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<PlatformCredential {self.platform.value}>"


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus), default=RunStatus.RUNNING
    )
    profile_name: Mapped[str] = mapped_column(String(255), default="")
    platform: Mapped[str] = mapped_column(String(50), default="")
    jobs_discovered: Mapped[int] = mapped_column(Integer, default=0)
    jobs_matched: Mapped[int] = mapped_column(Integer, default=0)
    jobs_applied: Mapped[int] = mapped_column(Integer, default=0)
    jobs_queued: Mapped[int] = mapped_column(Integer, default=0)
    jobs_skipped: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    # Observability: stage timings (JSON), AI calls and estimated cost.
    timings: Mapped[str] = mapped_column(Text, default="")
    ai_calls: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<AgentRun {self.id} ({self.status.value})>"


class GigOpportunity(Base):
    """AI-training / expert-network gigs (Alignerr, Outlier, ...).

    These are not resume applications: onboarding is account + assessment,
    pay is hourly/task based, and availability is country dependent.
    """

    __tablename__ = "gig_opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    network: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(500))
    track: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(2048), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    remote: Mapped[bool] = mapped_column(Boolean, default=True)
    pay_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    pay_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(16), default="USD")
    pay_period: Mapped[str] = mapped_column(String(32), default="hour")
    employment_type: Mapped[str] = mapped_column(String(64), default="part_time")
    eligibility: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[GigStatus] = mapped_column(
        Enum(GigStatus), default=GigStatus.DISCOVERED
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<GigOpportunity {self.id}: {self.title} @ {self.network}>"
