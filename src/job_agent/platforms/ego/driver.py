"""Platform driver that searches through ego(lite) instead of Playwright.

ego(lite) is already the person's logged-in browser on their own machine, so
`login()` is a no-op: there is no credential to replay.
"""

from __future__ import annotations

import os

from job_agent.db.models import Platform
from job_agent.platforms.base import JobPosting, PlatformDriver
from job_agent.platforms.ego.client import DEFAULT_BINARY, EgoClient
from job_agent.platforms.ego.discovery import EgoDiscovery
from job_agent.utils.logging import get_logger

log = get_logger(__name__)


class EgoDriver(PlatformDriver):
    # ego(lite) holds the live session; there is no credential to replay.
    requires_login = False

    def __init__(self, settings, platform_name: str):
        self.platform = Platform(platform_name)
        self.platform_name = platform_name
        cfg = getattr(settings, "ego", None)
        host = os.environ.get("JOB_AGENT_EGO_HOST") or getattr(cfg, "host", "")
        self.discovery = EgoDiscovery(
            EgoClient(
                host=host or None,
                user=getattr(cfg, "user", ""),
                binary=getattr(cfg, "binary", "") or DEFAULT_BINARY,
                timeout=getattr(cfg, "timeout", 180),
            ),
            platform_name,
            task_space=getattr(cfg, "task_space", "job-agent"),
            settle_ms=getattr(cfg, "settle_ms", 2500),
        )

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
        # ponytail: discovery only. Filling an ATS through ego is a separate slice;
        # until it exists, report not-applied rather than pretend.
        log.warning("ego_apply_not_implemented", url=job.url)
        return False

    def is_already_applied(self, job: JobPosting) -> bool:
        return False

    def close(self) -> None:
        """No browser of ours to close."""

    def _unused(self) -> None:  # pragma: no cover
        return None