"""Platform driver that searches and applies through ego(lite).

ego(lite) is already the person's logged-in browser on their own machine, so
`login()` is a no-op: there is no credential to replay. Applications are
human-supervised by default: the agent fills and attaches documents, then hands
control back for login / 2FA / CAPTCHA / the final submit.
"""

from __future__ import annotations

import os

from job_agent.db.models import Platform
from job_agent.platforms.base import JobPosting, PlatformDriver
from job_agent.platforms.ego.applicator import EgoApplyResult, EgoApplicator
from job_agent.platforms.ego.client import (
    DEFAULT_BINARY,
    EgoClient,
    EgoError,
    resolve_ego_binary,
)
from job_agent.platforms.ego.discovery import EgoDiscovery
from job_agent.platforms.ego.session import EgoSession
from job_agent.utils.logging import get_logger

log = get_logger(__name__)

# Snapshot phrases that mean the posting is no longer accepting applications.
_CLOSED_KEYWORDS = (
    "no longer accepting",
    "no longer accept",
    "applications closed",
    "application closed",
    "position has been filled",
    "this job has expired",
    "job is closed",
    "job has expired",
    "posting has expired",
    "no longer available",
    "position closed",
)


class EgoDriver(PlatformDriver):
    # ego(lite) holds the live session; there is no credential to replay.
    requires_login = False

    def __init__(self, settings, platform_name: str):
        self.platform = Platform(platform_name)
        self.platform_name = platform_name
        cfg = getattr(settings, "ego", None)
        host = os.environ.get("JOB_AGENT_EGO_HOST") or getattr(cfg, "host", "")
        task_space = getattr(cfg, "task_space", "job-agent") or "job-agent"
        settle_ms = getattr(cfg, "settle_ms", 2500)
        self._apply_enabled = bool(getattr(cfg, "apply_enabled", True))
        self._require_human_submit = bool(getattr(cfg, "require_human_submit", True))

        configured_binary = getattr(cfg, "binary", "") or ""
        try:
            binary = resolve_ego_binary(configured_binary)
        except EgoError:
            # Resolution can fail on a non-Mac dev/CI box; keep construction safe.
            binary = configured_binary or DEFAULT_BINARY

        self.client = EgoClient(
            host=host or None,
            user=getattr(cfg, "user", ""),
            binary=binary,
            timeout=getattr(cfg, "timeout", 180),
        )
        self.discovery = EgoDiscovery(
            self.client,
            platform_name,
            task_space=task_space,
            settle_ms=settle_ms,
            board_config_path=getattr(cfg, "board_config_path", "") or "",
        )
        self._session = EgoSession(self.client, task_space)
        self._applicator = EgoApplicator(
            self._session,
            require_human_submit=self._require_human_submit,
            settle_ms=settle_ms,
        )
        # Rich result of the last apply(), consumed by orchestrator/verification.py.
        self.last_result: EgoApplyResult | None = None

    def login(self, username: str = "", password: str = "") -> None:
        """Nothing to do: ego(lite) holds the live session already."""

    def search_jobs(
        self,
        query: str,
        location: str = "",
        remote: bool = False,
        experience_level: str = "",
        limit: int = 25,
    ) -> list[JobPosting]:
        return self.discovery.search(
            query=query,
            location=location,
            remote=remote,
            experience_level=experience_level,
            limit=limit,
        )

    def get_job_details(self, job_url: str) -> JobPosting:
        body = self.discovery.details(job_url)
        return JobPosting(
            external_id="",
            platform=self.platform,
            title="",
            company="",
            description=body,
            url=job_url,
        )

    def apply(
        self,
        job: JobPosting,
        resume_path: str,
        cover_letter_path: str = "",
        answers: dict[str, str] | None = None,
    ) -> bool:
        """Delegate to EgoApplicator; return True ONLY for a confirmed submit."""
        if not self._apply_enabled:
            self.last_result = EgoApplyResult(
                False, "failed", "ego.apply_enabled is False in settings", job.url
            )
            log.warning("ego_apply_disabled", url=job.url)
            return False

        # Only explicitly supplied answers are filled (never fabricated). A key
        # that is not a usable selector is tolerated by the applicator.
        fields = dict(answers) if answers else None
        try:
            result = self._applicator.apply(
                job.url,
                resume_path,
                cover_letter_path,
                fields=fields,
            )
        except Exception as exc:  # noqa: BLE001 - never leak to the pipeline
            log.warning("ego_apply_error", url=job.url, error=str(exc)[:300])
            result = EgoApplyResult(
                False, "failed", f"apply error: {exc}"[:500], job.url
            )

        self.last_result = result
        if result.status == "confirmed":
            log.info("ego_apply_confirmed", url=job.url, note=result.note)
            return True
        if result.status in ("pending_human", "unverified"):
            log.info(
                "ego_apply_outcome",
                url=job.url,
                status=result.status,
                note=result.note,
            )
        else:
            log.warning("ego_apply_failed", url=job.url, note=result.note)
        return False

    def is_job_open(self, job: JobPosting) -> bool:
        """Snapshot the posting and look for closed/expired keywords."""
        try:
            self._session.goto(job.url)
            text = (self._session.snapshot(full_page=True) or "").lower()
        except EgoError as exc:
            log.warning("ego_is_job_open_failed", url=job.url, error=str(exc)[:200])
            return True  # assume open when the bridge cannot observe
        if any(kw in text for kw in _CLOSED_KEYWORDS):
            log.info("ego_job_closed", url=job.url)
            return False
        return True

    def is_already_applied(self, job: JobPosting) -> bool:
        return False

    def close(self) -> None:
        """Close the discovery task space. Safe to call more than once."""
        self.discovery.close()

    def _unused(self) -> None:  # pragma: no cover
        return None
