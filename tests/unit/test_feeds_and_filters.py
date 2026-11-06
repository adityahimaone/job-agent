"""Offline unit tests for feeds, filters, boards and the gig lane.

NO network is used: HTTP is replaced with fake responses / monkeypatched
parsers. Run with:  pytest tests/unit/test_feeds_and_filters.py
"""

from __future__ import annotations

from pathlib import Path

from job_agent.config import Settings
from job_agent.db.models import Platform
from job_agent.discovery import (
    FeedHub,
    FeedSpec,
    default_board_specs,
    default_feed_specs,
    load_board_specs,
    load_feed_specs,
)
from job_agent.discovery import feeds as feeds_mod
from job_agent.gigs import GigListing, GigService, parse_alignerr_html
from job_agent.gigs import service as gig_service_mod
from job_agent.matching import (
    FilterResult,
    apply_filters,
    build_filters,
    score_employment_fit,
)
from job_agent.platforms.base import JobPosting

ROOT = Path(__file__).resolve().parents[2]
FEEDS_YAML = ROOT / "config" / "feeds.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _job(**kwargs) -> JobPosting:
    defaults = {
        "external_id": "x",
        "platform": Platform.REMOTIVE,
        "title": "Some Role",
        "company": "Acme",
    }
    defaults.update(kwargs)
    return JobPosting(**defaults)


class _FakeResponse:
    def __init__(self, payload=None, text: str = "") -> None:
        self._payload = payload
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeClient:
    """Minimal stand-in for httpx.Client used by the real feed parsers."""

    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse(self.payload)


# ---------------------------------------------------------------------------
# Alignerr parser
# ---------------------------------------------------------------------------


def test_parse_alignerr_html_extracts_title_and_pay_range():
    html = """
    <html><body>
    <a href="/jobs/3f9a1c2e-7b4d-4a11-9f80-1c2d3e4f5a6b" class="job-card">
      <h3>Software Developer &mdash; AI Coding Assistant</h3>
      <span class="pay">$60-100/hr</span>
      <span class="loc">Remote</span>
      <p>Help train and evaluate AI coding models. Flexible part-time hours.</p>
    </a>
    <a href="/jobs/aa11bb22-cc33-dd44-ee55-ff6677889900" class="job-card">
      <h3>Voice Transcription Specialist</h3>
      <span>$25-35/hr</span><span>Remote</span>
      <p>Transcribe short audio clips.</p>
    </a>
    </body></html>
    """
    listings = parse_alignerr_html(html)
    assert len(listings) >= 2

    coding = next(g for g in listings if "Software Developer" in g.title)
    assert coding.pay_min == 60.0
    assert coding.pay_max == 100.0
    assert coding.track == "coding"
    assert coding.remote is True
    assert coding.network == "alignerr"
    assert coding.pay_period == "hour"

    audio = next(g for g in listings if "Voice Transcription" in g.title)
    assert audio.track == "audio"
    assert (audio.pay_min, audio.pay_max) == (25.0, 35.0)


def test_parse_alignerr_html_jsonld_and_never_raises():
    ld = """
    <script type="application/ld+json">
    {"@type":"ItemList","itemListElement":[{"@type":"JobPosting",
      "title":"Software Developer - AI Coding Assistant",
      "url":"https://www.alignerr.com/jobs/3f9a1c2e-7b4d-4a11-9f80-1c2d3e4f5a6b",
      "description":"Train AI coding models.",
      "jobLocationType":"TELECOMMUTE",
      "baseSalary":{"currency":"USD","value":{"minValue":60,"maxValue":100,"unitText":"HOUR"}}}]}
    </script>
    """
    listings = parse_alignerr_html(ld)
    assert any("Software Developer" in g.title for g in listings)
    coding = next(g for g in listings if "Software Developer" in g.title)
    assert (coding.pay_min, coding.pay_max) == (60.0, 100.0)

    # Empty / malformed input never raises.
    assert parse_alignerr_html("") == []
    assert parse_alignerr_html("<html><body>no jobs</body></html>") == []


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_apply_filters_keeps_parttime_remote_rejects_fulltime_onsite():
    filters = build_filters(
        {
            "search": {
                "filters": {
                    "employment_type": "part-time",
                    "remote_only": True,
                    "allowed_remote_policies": ["remote", "hybrid"],
                }
            }
        }
    )
    part_time_remote = _job(
        external_id="1",
        title="Part-time Remote Designer",
        employment_type="part_time",
        remote=True,
        remote_policy="remote",
    )
    full_time_onsite = _job(
        external_id="2",
        title="Full-time Onsite Engineer",
        employment_type="full_time",
        remote=False,
        remote_policy="onsite",
    )
    result = apply_filters([part_time_remote, full_time_onsite], filters)

    assert isinstance(result, FilterResult)
    assert part_time_remote in result.kept
    assert full_time_onsite not in result.kept
    reasons = [why for _, why in result.rejected]
    assert len(reasons) == 1
    assert reasons[0]  # a human-readable reason string
    assert "employment_type" in reasons[0] or "remote_only" in reasons[0]


def test_build_filters_normalises_profile():
    filters = build_filters(
        {
            "search": {
                "filters": {
                    "employment_type": "part-time",
                    "remote_only": "true",
                    "allowed_remote_policies": "remote, hybrid",
                    "timezone_alignment": "GMT+7 ±4",
                    "min_hourly_rate": "20",
                    "max_hours_per_week": 30,
                    "exclude_keywords": ["on-call", "clearance"],
                    "include_keywords": "figma",
                    "max_age_days": "30",
                }
            }
        }
    )
    assert filters["employment_types"] == ["part_time"]
    assert filters["remote_only"] is True
    assert filters["allowed_remote_policies"] == ["remote", "hybrid"]
    assert filters["timezone_offset"] == 7.0
    assert filters["timezone_delta"] == 4.0
    assert filters["min_hourly_rate"] == 20.0
    assert filters["max_hours_per_week"] == 30.0
    assert filters["include_keywords"] == ["figma"]
    assert filters["max_age_days"] == 30
    assert "on-call" in filters["exclude_keywords"]

    # Flat {"filters": {...}} and top-level forms are also accepted.
    assert build_filters({"filters": {"employment_type": "contract"}})[
        "employment_types"
    ] == ["contract"]
    assert build_filters({"employment_type": "intern"})["employment_types"] == [
        "internship"
    ]


def test_apply_filters_fail_open_and_score():
    # filters=None keeps everything.
    result = apply_filters([_job(), _job(external_id="y")], None)
    assert len(result.kept) == 2
    assert result.rejected == []

    # Unknown employment_type is kept by default (treated as unknown)...
    filters = build_filters(
        {"filters": {"employment_types": ["part_time", "contract"]}}
    )
    unknown = _job(external_id="u", employment_type="")
    assert unknown in apply_filters([unknown], filters).kept
    # ...unless require_known_employment_type is set.
    strict = dict(filters, require_known_employment_type=True)
    assert unknown in [p for p, _ in apply_filters([unknown], strict).rejected]

    # Score is bounded and never raises.
    for p in (_job(), unknown, _job(employment_type="part_time", remote=True)):
        assert 0.0 <= score_employment_fit(p, filters) <= 1.0


# ---------------------------------------------------------------------------
# FeedHub (offline)
# ---------------------------------------------------------------------------


def test_feedhub_empty_spec_list_returns_empty():
    hub = FeedHub(specs=[])
    try:
        assert hub.discover(["designer"]) == []
        assert hub.results() == []
    finally:
        hub.close()


def test_feedhub_discover_with_mocked_parser(monkeypatch):
    """Filters + dedupe + limit + source stamping, all without network."""

    def fake_parser(client, spec, queries, locations, timeout):
        return [
            _job(
                external_id="1",
                title="Part-time Designer",
                employment_type="part_time",
                remote=True,
                remote_policy="remote",
            ),
            _job(
                external_id="1",
                title="Part-time Designer DUP",
                employment_type="part_time",
                remote=True,
                remote_policy="remote",
            ),
            _job(
                external_id="2",
                title="Full-time Engineer",
                employment_type="full_time",
                remote=False,
                remote_policy="onsite",
            ),
        ]

    monkeypatch.setitem(feeds_mod._PARSERS, "remotive", fake_parser)
    hub = FeedHub(
        specs=[FeedSpec(name="remotive", kind="remotive", platform="remotive")]
    )
    try:
        filters = build_filters(
            {"filters": {"employment_type": "part-time", "remote_only": True}}
        )
        out = hub.discover(["designer"], limit=10, filters=filters)
        # "2" is filtered out; the two "1" postings dedupe to one.
        assert len(out) == 1
        assert out[0].source == "remotive"
        assert out[0].platform == Platform.REMOTIVE
        assert hub.results()[0].error == ""
        assert hub.results()[0].source == "remotive"
    finally:
        hub.close()


def test_real_feed_parser_offline():
    """Exercise a real JSON parser with a fake httpx response."""
    spec = FeedSpec(name="remotive", kind="remotive", platform="remotive")
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Part-time Product Designer",
                "company_name": "Acme",
                "candidate_required_location": "Worldwide",
                "salary": "$40-60/hr",
                "tags": ["figma", "design systems"],
                "job_type": "part_time",
                "description": "<p>Remote. 20 hours per week. GMT+7.</p>",
                "url": "https://remotive.com/jobs/1",
                "publication_date": "2026-11-01",
            }
        ]
    }
    jobs = feeds_mod._parse_remotive(_FakeClient(payload), spec, [], [], 12.0)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.employment_type == "part_time"
    assert j.remote_policy == "remote"
    assert j.currency == "USD"
    assert j.salary_period == "hour"
    assert j.hours_per_week == 20.0
    assert j.timezone == "GMT+7"
    assert j.source == "remotive"
    assert j.platform == Platform.REMOTIVE


# ---------------------------------------------------------------------------
# Boards / loaders
# ---------------------------------------------------------------------------


def test_default_board_specs_includes_jobstreet():
    specs = default_board_specs()
    assert any(
        "jobstreet" in s.platform.lower() or "jobstreet" in s.name.lower()
        for s in specs
    )
    assert all(
        "{query}" in s.search_url and "{location}" in s.search_url for s in specs
    )


def test_loaders_read_yaml_and_tolerate_missing_file():
    feed_specs = load_feed_specs(FEEDS_YAML)
    assert any(s.kind == "remotive" for s in feed_specs)
    board_specs = load_board_specs(FEEDS_YAML)
    assert any("jobstreet" in b.name.lower() for b in board_specs)

    missing = ROOT / "config" / "__does_not_exist__.yaml"
    assert load_feed_specs(missing) == default_feed_specs()
    assert load_board_specs(missing) == default_board_specs()


# ---------------------------------------------------------------------------
# GigService
# ---------------------------------------------------------------------------


def test_gigservice_eligible_drops_audio_only_roles():
    settings = Settings(_env_file=None)
    settings.gigs.min_hourly_rate = 20.0
    settings.gigs.remote_only = True
    gs = GigService(settings)

    coding = GigListing(
        external_id="c",
        network="alignerr",
        title="Software Developer - AI Coding Assistant",
        track="coding",
        pay_min=60,
        pay_max=100,
    )
    audio = GigListing(
        external_id="a",
        network="alignerr",
        title="Voice Transcription Specialist",
        track="audio",
        pay_min=25,
        pay_max=35,
    )
    voice_but_general = GigListing(
        external_id="v",
        network="alignerr",
        title="Voice Audio Transcriber",
        track="general",
        pay_min=30,
        pay_max=40,
    )
    low_rate = GigListing(
        external_id="l",
        network="alignerr",
        title="General Rater",
        track="general",
        pay_min=5,
        pay_max=8,
    )

    kept = gs.eligible([coding, audio, voice_but_general, low_rate])
    assert coding in kept
    assert audio not in kept  # audio track dropped
    assert voice_but_general not in kept  # voice-only content dropped
    assert low_rate not in kept  # below min_hourly_rate


def test_gigservice_refresh_without_session(monkeypatch):
    class FakeNetwork:
        name = "alignerr"

        def fetch(self, *, timeout: float = 12.0):
            return [
                GigListing(
                    external_id="c",
                    network="alignerr",
                    title="Software Developer - AI Coding Assistant",
                    track="coding",
                    pay_min=60,
                    pay_max=100,
                )
            ]

    monkeypatch.setattr(gig_service_mod, "get_network", lambda name: FakeNetwork())
    settings = Settings(_env_file=None)
    gs = GigService(settings, session=None)
    counts = gs.refresh(networks=["alignerr"])

    assert counts["discovered"] == 1
    assert counts["eligible"] == 1
    assert counts["skipped"] == 0
    assert counts["errors"] == 0
    rows = gs.to_rows(gs.eligible())
    assert len(rows) == 1
    assert rows[0]["status"] == "eligible"
    assert rows[0]["network"] == "alignerr"
    assert rows[0]["gig_key"] == "alignerr:c"  # canonical sheet key
    assert rows[0]["decision"] == ""  # human-owned, left blank
