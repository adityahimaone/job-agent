"""Offline unit tests for the ego(lite) bridge v2.

No real browser is used: every test injects a fake ``EgoClient.run`` (or a fake
filesystem for binary resolution) and asserts on generated scripts / returned
values. Run with:

    PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_ego_bridge_v2.py -q
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from job_agent.db.models import Platform
from job_agent.platforms.base import JobPosting
from job_agent.platforms.ego.applicator import EgoApplicator, EgoApplyResult
from job_agent.platforms.ego.boards import (
    BoardSpec,
    build_board_script,
    default_board_specs,
    parse_board_rows,
)
from job_agent.platforms.ego.client import (
    DEFAULT_BINARY,
    EgoError,
    resolve_ego_binary,
)
from job_agent.platforms.ego.discovery import EgoDiscovery
from job_agent.platforms.ego.driver import EgoDriver
from job_agent.platforms.ego.session import EgoSession


class StubClient:
    """Records every script and returns a canned dict (no browser)."""

    def __init__(self, payload: dict | None = None):
        self.payload = payload if payload is not None else {}
        self.scripts: list[str] = []

    def run(self, script: str) -> dict:
        self.scripts.append(script)
        return self.payload


# ---------------------------------------------------------------------------
# client.resolve_ego_binary
# ---------------------------------------------------------------------------


def _fake_fs(monkeypatch, files, globbed=None, which=None):
    """Patch the filesystem helpers used by resolve_ego_binary."""
    monkeypatch.setattr(
        "job_agent.platforms.ego.client.os.path.isfile",
        lambda p: p in files,
    )
    monkeypatch.setattr(
        "job_agent.platforms.ego.client.os.access",
        lambda p, mode: p in files,
    )
    monkeypatch.setattr(
        "job_agent.platforms.ego.client.glob.glob",
        lambda pattern: list(globbed or []),
    )
    monkeypatch.setattr(
        "job_agent.platforms.ego.client.shutil.which",
        lambda name: which,
    )


def test_resolve_prefers_configured_executable(monkeypatch):
    configured = "/opt/custom/ego-browser"
    _fake_fs(monkeypatch, {configured})
    assert resolve_ego_binary(configured) == configured


def test_resolve_falls_back_to_current(monkeypatch):
    _fake_fs(monkeypatch, {DEFAULT_BINARY})
    assert resolve_ego_binary("") == DEFAULT_BINARY
    # a non-executable configured path is ignored
    assert resolve_ego_binary("/not/executable") == DEFAULT_BINARY


def test_resolve_picks_highest_version_glob(monkeypatch):
    old = "/Applications/x/Versions/0.6.2.8/Helpers/ego-browser"
    new = "/Applications/x/Versions/0.6.3.0/Helpers/ego-browser"
    low = "/Applications/x/Versions/0.5.9.0/Helpers/ego-browser"
    _fake_fs(monkeypatch, {old, new, low}, globbed=[low, old, new])
    result = resolve_ego_binary("")
    assert result == new  # highest version wins when Current is absent


def test_resolve_uses_path_then_raises_when_nothing(monkeypatch):
    on_path = "/usr/local/bin/ego-browser"
    _fake_fs(monkeypatch, {on_path}, globbed=[], which=on_path)
    assert resolve_ego_binary("") == on_path

    _fake_fs(monkeypatch, set(), globbed=[], which=None)
    with pytest.raises(EgoError) as exc:
        resolve_ego_binary("")
    assert "not found" in str(exc.value).lower()


def test_default_binary_is_current_symlink():
    assert DEFAULT_BINARY.endswith("Versions/Current/Helpers/ego-browser")


# ---------------------------------------------------------------------------
# EgoSession script generation
# ---------------------------------------------------------------------------


def test_session_scripts_use_documented_apis():
    stub = StubClient()
    session = EgoSession(stub, "my-space")

    session.open()
    assert "taskSpace(" in stub.scripts[-1]
    assert "spaceId" in stub.scripts[-1]

    session.goto("https://example.com/?q=1")
    assert "page.goto(" in stub.scripts[-1]
    assert "https://example.com/?q=1" in stub.scripts[-1]

    session.fill("input[name='q']", 'he said "hi"')
    assert "page.fill(" in stub.scripts[-1]
    assert json.dumps('he said "hi"') in stub.scripts[-1]  # JSON-injection safe

    session.click("button.submit")
    assert "page.click(" in stub.scripts[-1]

    session.set_input_files("input[type=file]", ["/tmp/resume.pdf"])
    assert "page.setInputFiles(" in stub.scripts[-1]
    assert "/tmp/resume.pdf" in stub.scripts[-1]

    session.evaluate("() => document.title", {"x": 1})
    assert "page.evaluate(" in stub.scripts[-1]

    session.snapshot(full_page=True)
    assert 'scope: "full_page"' in stub.scripts[-1]

    session.handoff("please submit")
    assert "task.handOff(" in stub.scripts[-1]
    assert "please submit" in stub.scripts[-1]

    session.wait_for_control(1234)
    assert "task.waitForControl(" in stub.scripts[-1]
    assert "1234" in stub.scripts[-1]

    session.finish(["p1"])
    assert "task.finish(" in stub.scripts[-1]
    assert '"p1"' in stub.scripts[-1]


def test_session_run_batch_is_one_script_with_all_actions():
    stub = StubClient()
    session = EgoSession(stub, "job-agent")
    result = session.run_batch(
        [
            {"type": "goto", "url": "https://x.test/?q=1"},
            {"type": "fill", "selector": "#q", "value": 'a"b'},
            {"type": "click", "selector": "button.go"},
            {"type": "snapshot", "full_page": True},
        ]
    )
    assert isinstance(result, dict)  # passthrough of the (stub) client result
    assert len(stub.scripts) == 1  # ONE process
    script = stub.scripts[0]
    assert "page.goto(" in script
    assert "page.fill(" in script
    assert "page.click(" in script
    assert "page.snapshot(" in script
    assert json.dumps('a"b') in script  # injection-safe value


def test_session_evaluate_returns_value():
    stub = StubClient({"value": 42})
    session = EgoSession(stub, "job-agent")
    assert session.evaluate("() => 42") == 42


# ---------------------------------------------------------------------------
# boards.parse_board_rows
# ---------------------------------------------------------------------------


def test_parse_board_rows_handles_missing_fields_and_dedupes():
    spec = default_board_specs()["wellfound"]
    payload = {
        "rows": [
            {"url": "https://x.test/1", "title": "Engineer"},  # missing fields
            {"url": "https://x.test/1", "title": "duplicate"},  # duplicate url
            {"title": "no url"},  # missing url
            {"url": "https://x.test/2"},  # missing title
            "not a dict",  # malformed
            {},  # empty
        ]
    }
    rows = parse_board_rows(payload, spec)
    assert len(rows) == 2
    assert all(isinstance(r, JobPosting) for r in rows)
    assert all(r.source == "ego:wellfound" for r in rows)
    assert all(r.platform == Platform.WELLFOUND for r in rows)
    assert rows[0].title == "Engineer"
    assert rows[0].company == ""  # missing -> ""
    assert rows[1].title == ""  # missing title -> ""


def test_parse_board_rows_empty_and_bad_payload():
    spec = default_board_specs()["seek"]
    assert parse_board_rows({}, spec) == []
    assert parse_board_rows({"rows": "not a list"}, spec) == []
    assert parse_board_rows({"rows": [None, 3]}, spec) == []


def test_parse_board_rows_unknown_platform_maps_to_other():
    spec = BoardSpec(
        name="custom",
        platform="definitely_not_a_platform",
        search_url="https://x/{query}/{location}",
        card_selector="article",
        title_selector="h2",
        company_selector="",
        location_selector="",
        link_selector="a",
    )
    rows = parse_board_rows({"rows": [{"url": "https://x/1", "title": "T"}]}, spec)
    assert rows[0].platform == Platform.OTHER
    assert rows[0].source == "ego:custom"


def test_build_board_script_uses_page_evaluate_in_page():
    spec = default_board_specs()["jobstreet"]
    script = build_board_script(
        spec, query="dev ops", location="Jakarta", limit=10, settle_ms=2500
    )
    assert "page.evaluate(" in script
    assert "document.querySelectorAll" in script  # in-page DOM, not Node-side
    assert "%20" in script or "dev%20ops" in script  # URL-encoded query
    assert '"limit": 10' in script


# ---------------------------------------------------------------------------
# EgoApplicator outcomes
# ---------------------------------------------------------------------------


def test_applicator_pending_human_when_handoff_required():
    stub = StubClient({})  # empty snapshots -> no confirmation
    session = EgoSession(stub, "job-agent")
    app = EgoApplicator(session, require_human_submit=True)
    result = app.apply("https://x/apply", "/tmp/resume.pdf", fields={"#name": "Ada"})
    assert isinstance(result, EgoApplyResult)
    assert result.status == "pending_human"
    assert result.ok is False
    # handoff path was used; and it NEVER clicked submit itself
    assert any("task.handOff(" in s for s in stub.scripts)
    assert any("task.waitForControl(" in s for s in stub.scripts)
    assert not any("data-ego-submit" in s for s in stub.scripts)
    assert not any("page.click(" in s for s in stub.scripts)


def test_applicator_unverified_when_no_confirmation_text_auto_mode():
    stub = StubClient({})  # no confirmation keywords anywhere
    session = EgoSession(stub, "job-agent")
    app = EgoApplicator(session, require_human_submit=False)
    result = app.apply("https://x/apply", "/tmp/resume.pdf")
    assert result.status == "unverified"
    assert result.ok is False
    # auto mode DID attempt a submit and guarded against captcha
    assert any("data-ego-submit" in s for s in stub.scripts)
    assert any("captcha" in s.lower() for s in stub.scripts)  # captcha guard present


def test_applicator_confirmed_with_confirmation_text():
    stub = StubClient({"snapshot": "Your application submitted. Thank you!"})
    session = EgoSession(stub, "job-agent")
    app = EgoApplicator(session, require_human_submit=True)
    result = app.apply("https://x/apply", "/tmp/resume.pdf")
    assert result.status == "confirmed"
    assert result.ok is True


def test_applicator_failed_on_bridge_error():
    class Boom:
        def run(self, script):
            raise EgoError("down")

    app = EgoApplicator(EgoSession(Boom(), "job-agent"), require_human_submit=True)
    result = app.apply("https://x/apply", "/tmp/resume.pdf")
    assert result.status == "failed"
    assert result.ok is False


# ---------------------------------------------------------------------------
# EgoDiscovery / EgoDriver
# ---------------------------------------------------------------------------


def test_discovery_accepts_wellfound_without_raising():
    stub = StubClient({"rows": []})
    discovery = EgoDiscovery(stub, "wellfound")
    assert discovery.platform == "wellfound"
    discovery.search("python", location="Remote")  # generic board path, no raise


def test_discovery_rejects_truly_unknown_platform():
    with pytest.raises(ValueError) as exc:
        EgoDiscovery(StubClient({}), "myspace")
    msg = str(exc.value).lower()
    assert "wellfound" in msg  # supported platforms listed


def test_discovery_close_is_safe_twice():
    stub = StubClient({"status": 200, "pages": 1, "rows": []})
    discovery = EgoDiscovery(stub, "linkedin")
    discovery.search("q")  # marks the task space as used
    discovery.close()
    discovery.close()  # must not raise / must not finish twice
    assert any("task.finish(" in s for s in stub.scripts)


def test_discovery_search_all_is_batched_and_deduped():
    stub = StubClient({"status": 200, "rows": []})
    discovery = EgoDiscovery(stub, "linkedin")
    discovery.search_all(["dev", "ops"], ["Jakarta", ""], limit=10)
    assert len(stub.scripts) == 1  # ONE nodejs invocation
    assert "combos" in stub.scripts[0]


class _StubSettings:
    ego = SimpleNamespace(
        host="",
        user="",
        binary="",
        timeout=5,
        task_space="job-agent",
        settle_ms=10,
        apply_enabled=True,
        require_human_submit=True,
        board_config_path="",
    )


def _job():
    return JobPosting(
        external_id="1",
        platform=Platform.LINKEDIN,
        title="Engineer",
        company="Acme",
        url="https://x/job/1",
    )


def test_driver_apply_true_only_for_confirmed_and_stores_last_result():
    driver = EgoDriver(_StubSettings(), "linkedin")
    driver._applicator.apply = lambda *a, **k: EgoApplyResult(
        False, "pending_human", "waiting", "https://x/job/1"
    )
    assert driver.apply(_job(), "resume.pdf") is False
    assert driver.last_result.status == "pending_human"

    driver._applicator.apply = lambda *a, **k: EgoApplyResult(
        True, "confirmed", "done", "https://x/job/1"
    )
    assert driver.apply(_job(), "resume.pdf") is True
    assert driver.last_result.status == "confirmed"


def test_driver_is_job_open_detects_closed_keyword():
    driver = EgoDriver(_StubSettings(), "linkedin")
    driver._session.snapshot = lambda **k: (
        "This job is closed and no longer accepting applications"
    )
    driver._session.goto = lambda url: {}
    assert driver.is_job_open(_job()) is False

    driver._session.snapshot = lambda **k: "Apply now for this role"
    assert driver.is_job_open(_job()) is True
