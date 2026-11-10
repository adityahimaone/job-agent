from __future__ import annotations

from job_agent.platforms.ego.boards import build_board_script, load_board_specs


def test_dealls_default_spec_present():
    specs = load_board_specs("config/discovery_sources.yaml")
    spec = specs["dealls"]
    assert spec.platform == "dealls"
    assert spec.search_url == "https://dealls.com/loker?search={query}"
    assert "/posisi" in spec.card_selector
    assert "/industri" in spec.card_selector
    assert spec.title_selector
    script = build_board_script(
        spec, query="frontend", location="", limit=5, settle_ms=10
    )
    assert "dealls.com/loker?search=frontend" in script
