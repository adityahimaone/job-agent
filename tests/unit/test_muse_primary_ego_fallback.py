"""Tests for the Muse-primary / Ego-fallback discovery lanes.

Offline: no network, no Mac, no AI calls. The Ego side is a fake callable,
so these tests prove the *decision* logic, not the Ego bridge itself.

Run with:  pytest tests/unit/test_muse_primary_ego_fallback.py
"""

from __future__ import annotations

from job_agent.config import Settings
from job_agent.discovery.lanes import (
    DEFAULT_EGO_PLATFORMS,
    LANE_EGO,
    LANE_MUSE,
    new_muse_jobs,
    resolve_lane_order,
    run_muse_first,
    should_fallback_to_ego,
)


def _settings(**discovery_overrides):
    """Settings with the discovery section overridden, env ignored."""
    s = Settings(_env_file=None)
    for key, value in discovery_overrides.items():
        setattr(s.discovery, key, value)
    return s


def _stats(imported=0, would_import=0, parsed=0):
    return {
        "parsed": parsed,
        "imported": imported,
        "would_import": would_import,
        "duplicates": 0,
        "cross_platform_duplicates": 0,
        "matched": 0,
        "queued": 0,
        "skipped": 0,
        "deferred_no_ai": 0,
        "scores": [],
    }


# --- config defaults -------------------------------------------------------


def test_discovery_defaults_are_muse_primary_ego_fallback():
    s = Settings(_env_file=None)
    assert s.discovery.primary == LANE_MUSE
    assert s.discovery.fallback == LANE_EGO
    assert s.discovery.fallback_enabled is True
    assert s.discovery.fallback_on_zero_new is True
    assert s.discovery.ego_platforms == DEFAULT_EGO_PLATFORMS
    assert "Frontend Engineer React" in s.discovery.muse_queries
    assert "Singapore" in s.discovery.muse_locations
    assert s.discovery.ego_limit_per_query == 10


def test_discovery_config_parses_from_dict():
    s = Settings(
        _env_file=None,
        discovery={
            "primary": "muse",
            "fallback": "ego",
            "fallback_enabled": False,
            "ego_platforms": ["dealls"],
        },
    )
    assert s.discovery.fallback_enabled is False
    assert s.discovery.ego_platforms == ["dealls"]


# --- new_muse_jobs ----------------------------------------------------------


def test_new_muse_jobs_counts_imported_on_real_run():
    assert new_muse_jobs(_stats(imported=5, parsed=5)) == 5
    assert new_muse_jobs(_stats(imported=0, parsed=5)) == 0


def test_new_muse_jobs_counts_would_import_on_dry_run():
    assert new_muse_jobs(_stats(would_import=3, parsed=3), dry_run=True) == 3
    assert new_muse_jobs(_stats(would_import=0, parsed=3), dry_run=True) == 0
    # A dry run never counts `imported` (it stays 0 by construction).
    assert new_muse_jobs(_stats(imported=0, would_import=0), dry_run=True) == 0


# --- should_fallback_to_ego -------------------------------------------------


def test_fallback_triggers_on_zero_new_muse_jobs():
    trigger, reason = should_fallback_to_ego(_stats(imported=0, parsed=5))
    assert trigger is True
    assert "zero new jobs" in reason


def test_fallback_triggers_on_empty_or_missing_muse_file():
    # Missing file -> the caller passes an empty stats dict.
    trigger, _ = should_fallback_to_ego(_stats(imported=0, parsed=0))
    assert trigger is True
    trigger, _ = should_fallback_to_ego({})
    assert trigger is True


def test_no_fallback_when_muse_yields_new_jobs():
    trigger, reason = should_fallback_to_ego(_stats(imported=1, parsed=1))
    assert trigger is False
    assert "not needed" in reason


def test_no_fallback_on_dry_run_would_import():
    trigger, _ = should_fallback_to_ego(_stats(would_import=2, parsed=2), dry_run=True)
    assert trigger is False


def test_fallback_not_requested():
    trigger, reason = should_fallback_to_ego(
        _stats(imported=0), fallback_requested=False
    )
    assert trigger is False
    assert "--with-ego-fallback" in reason


def test_fallback_disabled_by_config():
    trigger, reason = should_fallback_to_ego(_stats(imported=0), fallback_enabled=False)
    assert trigger is False
    assert "disabled" in reason


def test_fallback_disabled_when_ego_disabled():
    trigger, reason = should_fallback_to_ego(_stats(imported=0), ego_enabled=False)
    assert trigger is False
    assert "ego lane is disabled" in reason


def test_fallback_disabled_by_zero_new_flag():
    trigger, _ = should_fallback_to_ego(_stats(imported=0), fallback_on_zero_new=False)
    assert trigger is False


# --- resolve_lane_order ------------------------------------------------------


def test_lane_order_defaults_muse_then_ego():
    # Bare Settings() has ego.enabled=False (only config/default.yaml turns
    # it on), so enable it here to reflect the deployed config.
    s = _settings()
    s.ego.enabled = True
    assert resolve_lane_order(s) == [LANE_MUSE, LANE_EGO]


def test_lane_order_drops_ego_when_ego_disabled():
    s = _settings()
    s.ego.enabled = False
    assert resolve_lane_order(s) == [LANE_MUSE]


def test_lane_order_drops_ego_when_fallback_disabled():
    assert resolve_lane_order(_settings(fallback_enabled=False)) == [LANE_MUSE]


def test_lane_order_ignores_unknown_lane_names():
    s = _settings(primary="carrier-pigeon", fallback="ego")
    s.ego.enabled = True
    assert resolve_lane_order(s) == [LANE_MUSE, LANE_EGO]


# --- run_muse_first ----------------------------------------------------------


def test_run_muse_first_skips_ego_when_muse_finds_jobs():
    calls: list[str] = []

    def ego_search():
        calls.append("ego")
        return 4

    report = run_muse_first(
        muse_import=lambda: _stats(imported=3, parsed=3),
        ego_search=ego_search,
    )
    assert report["lane"] == LANE_MUSE
    assert report["fallback_triggered"] is False
    assert report["ego_ran"] is False
    assert report["ego_jobs"] == 0
    assert calls == []


def test_run_muse_first_falls_back_on_zero_new_jobs():
    calls: list[str] = []

    def ego_search():
        calls.append("ego")
        return 4

    report = run_muse_first(
        muse_import=lambda: _stats(imported=0, parsed=5),
        ego_search=ego_search,
    )
    assert report["lane"] == "muse+ego"
    assert report["fallback_triggered"] is True
    assert report["ego_ran"] is True
    assert report["ego_jobs"] == 4
    assert calls == ["ego"]


def test_run_muse_first_falls_back_on_empty_muse_result():
    report = run_muse_first(
        muse_import=lambda: _stats(imported=0, parsed=0),
        ego_search=lambda: 2,
    )
    assert report["fallback_triggered"] is True
    assert report["ego_ran"] is True


def test_run_muse_first_never_runs_ego_when_ego_disabled():
    calls: list[str] = []
    report = run_muse_first(
        muse_import=lambda: _stats(imported=0, parsed=0),
        ego_search=lambda: calls.append("ego") or 2,
        ego_enabled=False,
    )
    assert report["fallback_triggered"] is False
    assert report["ego_ran"] is False
    assert calls == []


def test_run_muse_first_reports_but_never_runs_ego_on_dry_run():
    calls: list[str] = []
    report = run_muse_first(
        muse_import=lambda: _stats(would_import=0, parsed=0),
        ego_search=lambda: calls.append("ego") or 2,
        dry_run=True,
    )
    # The fallback *would* trigger, but a dry run must not store jobs.
    assert report["fallback_triggered"] is True
    assert "dry-run" in report["fallback_reason"]
    assert report["ego_ran"] is False
    assert calls == []
