"""Self-check for the ego bridge. Runs offline, no Mac and no network needed.

    PYTHONPATH=src .venv/bin/python scripts/ego_selfcheck.py

A live check is separate, because it needs ego(lite) running somewhere:

    job-agent ego doctor
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from job_agent.db.models import Platform
from job_agent.platforms.base import JobPosting
from job_agent.platforms.ego.applicator import EgoApplyResult
from job_agent.platforms.ego.client import EgoError, EgoClient
from job_agent.platforms.ego.discovery import EgoDiscovery
from job_agent.platforms.ego.driver import EgoDriver


class StubClient:
    """Records the script it was handed and returns canned rows."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.scripts: list[str] = []

    def run(self, script: str) -> dict:
        self.scripts.append(script)
        return self.payload


class StubApplicator:
    """Avoid touching the real ego-browser during an offline self-check."""

    def apply(self, url, resume_path, cover_letter_path="", *, fields=None):
        return EgoApplyResult(False, "pending_human", "awaiting human submit", url)


def check_client_reads_stderr_result() -> None:
    """ego-browser prints console.log to stderr: the client must find it there."""
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "ego-browser"
        fake.write_text("#!/bin/bash\nprintf '%s\\n' '__RESULT__{\"ok\": true}' 1>&2\n")
        fake.chmod(0o755)
        client = EgoClient(binary=str(fake))
        assert client.run("") == {"ok": True}, "stderr __RESULT__ not read"


def check_client_raises_without_result() -> None:
    client = EgoClient(binary="true")
    try:
        client.run("")
    except EgoError:
        return
    raise AssertionError("missing __RESULT__ should raise EgoError")


def check_search_builds_linkedin_request() -> None:
    stub = StubClient({"status": 200, "pages": 1, "rows": []})
    EgoDiscovery(stub, "linkedin").search(
        query="frontend developer", location="Jakarta", remote=True, limit=5
    )
    script = stub.scripts[0]
    assert '"query": "frontend developer"' in script
    assert '"location": "Jakarta"' in script
    assert '"remote": true' in script
    assert "seeMoreJobPostings/search" in script


def check_search_maps_rows_to_postings() -> None:
    stub = StubClient(
        {
            "status": 200,
            "pages": 1,
            "rows": [
                {
                    "id": "4450932106",
                    "url": "https://www.linkedin.com/jobs/view/4450932106/",
                    "title": "Frontend Engineer",
                    "company": "Acme",
                    "location": "Jakarta, Indonesia",
                    "posted": "2026-11-01",
                }
            ],
        }
    )
    postings = EgoDiscovery(stub, "linkedin").search(query="frontend", limit=5)
    assert len(postings) == 1
    job = postings[0]
    assert job.external_id == "4450932106"
    assert job.title == "Frontend Engineer"
    assert job.company == "Acme"
    assert job.platform.value == "linkedin"
    assert job.extra["posted"] == "2026-11-01"


def check_search_skips_incomplete_rows() -> None:
    stub = StubClient(
        {"status": 200, "rows": [{"title": "", "url": "u"}, {"title": "T", "url": ""}]}
    )
    assert EgoDiscovery(stub, "linkedin").search(query="q") == []


def check_search_survives_ego_error() -> None:
    class Boom:
        def run(self, script):
            raise EgoError("down")

    assert EgoDiscovery(Boom(), "linkedin").search(query="q") == []


def check_board_platforms_are_supported() -> None:
    """SEA boards are now supported through the generic Ego board lane."""
    for name in ("wellfound", "jobstreet", "glints", "kalibrr", "seek"):
        discovery = EgoDiscovery(StubClient({"rows": []}), name)
        assert discovery.platform == name
    try:
        EgoDiscovery(StubClient({}), "not-a-real-board")
    except ValueError:
        return
    raise AssertionError("unknown board must raise ValueError")


def check_driver_hands_off_before_submit() -> None:
    class Settings:
        ego = type(
            "E",
            (),
            {
                "host": "",
                "user": "",
                "binary": "",
                "timeout": 5,
                "task_space": "job-agent",
                "settle_ms": 100,
                "apply_enabled": True,
                "require_human_submit": True,
                "board_config_path": "",
            },
        )()

        class platforms:
            pass

    driver = EgoDriver(Settings(), "linkedin")
    driver._applicator = StubApplicator()
    job = JobPosting(
        external_id="1",
        platform=Platform.LINKEDIN,
        title="Frontend Engineer",
        company="Acme",
        url="https://www.linkedin.com/jobs/view/1/",
    )
    assert driver.requires_login is False
    assert driver.login("user", "pass") is None
    assert driver.apply(job, "resume.pdf") is False
    assert driver.last_result is not None
    assert driver.last_result.status == "pending_human"


CHECKS = [
    check_client_reads_stderr_result,
    check_client_raises_without_result,
    check_search_builds_linkedin_request,
    check_search_maps_rows_to_postings,
    check_search_skips_incomplete_rows,
    check_search_survives_ego_error,
    check_board_platforms_are_supported,
    check_driver_hands_off_before_submit,
]

if __name__ == "__main__":
    failed = 0
    for check in CHECKS:
        try:
            check()
            print(f"ok   {check.__name__}")
        except Exception as exc:  # noqa: BLE001 - a self-check reports, not raises
            failed += 1
            print(f"FAIL {check.__name__}: {exc}")
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} passed")
    sys.exit(1 if failed else 0)
