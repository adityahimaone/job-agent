"""Unit tests for the spreadsheet integration (CSV backend, no network)."""

from __future__ import annotations

import csv

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from job_agent.db.models import (
    Application,
    ApplicationStatus,
    Base,
    GigOpportunity,
    Job,
    JobStatus,
    Platform,
)
from job_agent.integrations.spreadsheet import (
    SHEET_SPECS,
    CsvBackend,
    SheetConfig,
    SheetSync,
    job_to_row,
    make_backend,
)


@pytest.fixture
def config(tmp_path):
    return SheetConfig(directory=tmp_path / "sheets")


@pytest.fixture
def sync(config):
    return SheetSync(config)


@pytest.fixture
def session():
    """Self-contained in-memory SQLite session (no conftest fixtures)."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    yield db
    db.close()
    engine.dispose()


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def _write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# ensure_templates
# ---------------------------------------------------------------------------


def test_ensure_templates_creates_four_header_only_csvs(sync, config):
    paths = sync.ensure_templates()
    assert len(paths) == 4
    assert {p.name for p in paths} == {
        "jobs.csv",
        "applications.csv",
        "gigs.csv",
        "search_profiles.csv",
    }
    for sheet, spec in SHEET_SPECS.items():
        path = config.directory / f"{sheet}.csv"
        assert path.exists()
        fieldnames, rows = _read_csv(path)
        assert fieldnames == spec["columns"]
        assert rows == []


def test_ensure_templates_does_not_overwrite_existing_data(sync, config):
    sync.ensure_templates()
    jobs = config.directory / "jobs.csv"
    _write_csv(
        jobs,
        SHEET_SPECS["jobs"]["columns"],
        [{"job_key": "linkedin:1", "decision": "yes", "notes": "mine"}],
    )
    # Second run must leave the existing file untouched.
    paths = sync.ensure_templates()
    assert len(paths) == 4
    _, rows = _read_csv(jobs)
    assert len(rows) == 1
    assert rows[0]["decision"] == "yes"
    assert rows[0]["notes"] == "mine"


# ---------------------------------------------------------------------------
# upsert: idempotency + preserving human columns/extras
# ---------------------------------------------------------------------------


def test_upsert_is_idempotent_and_preserves_decision_notes_and_extras(config):
    backend = CsvBackend(config.directory)
    incoming = [
        {
            "job_key": "linkedin:1",
            "title": "Dev",
            "company": "Co",
            "decision": "",
            "notes": "",
        }
    ]
    assert backend.upsert("jobs", "job_key", incoming) == 1

    path = config.directory / "jobs.csv"
    fieldnames, rows = _read_csv(path)
    # Human edits: set decision/notes and add an extra column.
    extra_cols = list(fieldnames) + ["priority"]
    rows[0]["decision"] = "approve"
    rows[0]["notes"] = "great fit"
    rows[0]["priority"] = "high"
    _write_csv(path, extra_cols, rows)

    updated = [
        {
            "job_key": "linkedin:1",
            "title": "Senior Dev",  # changed by the agent
            "company": "Co",
            "decision": "",  # must NOT overwrite human decision
            "notes": "",  # must NOT overwrite human notes
        }
    ]
    assert backend.upsert("jobs", "job_key", updated) == 1

    fieldnames2, rows2 = _read_csv(path)
    assert len(rows2) == 1  # no duplicate row -> idempotent
    assert rows2[0]["title"] == "Senior Dev"
    assert rows2[0]["decision"] == "approve"
    assert rows2[0]["notes"] == "great fit"
    assert "priority" in fieldnames2
    assert rows2[0]["priority"] == "high"


def test_upsert_keeps_rows_the_human_added(config):
    backend = CsvBackend(config.directory)
    backend.upsert(
        "jobs",
        "job_key",
        [{"job_key": "linkedin:1", "title": "A"}],
    )
    path = config.directory / "jobs.csv"
    fieldnames, rows = _read_csv(path)
    rows.append(
        {**{c: "" for c in fieldnames}, "job_key": "manual:xyz", "decision": "skip"}
    )
    _write_csv(path, fieldnames, rows)

    backend.upsert(
        "jobs",
        "job_key",
        [{"job_key": "linkedin:1", "title": "A v2"}],
    )
    _, rows2 = _read_csv(path)
    keys = {r["job_key"] for r in rows2}
    assert keys == {"linkedin:1", "manual:xyz"}


# ---------------------------------------------------------------------------
# pull_decisions
# ---------------------------------------------------------------------------


def test_pull_decisions_normalises_values(sync):
    sync.backend.write_all(
        "jobs",
        [
            {"job_key": "linkedin:1", "decision": "Approved"},
            {"job_key": "linkedin:2", "decision": "REJECT"},
            {"job_key": "linkedin:3", "decision": "yes"},
            {"job_key": "linkedin:4", "decision": "n"},
            {"job_key": "linkedin:5", "decision": "Hold "},
            {"job_key": "linkedin:6", "decision": ""},
            {"job_key": "linkedin:7", "decision": "maybe"},
        ],
    )
    sync.backend.write_all(
        "gigs",
        [{"gig_key": "alignerr:9", "decision": "YES"}],
    )
    result = sync.pull_decisions()
    assert result == {
        "linkedin:1": "approve",
        "linkedin:2": "reject",
        "linkedin:3": "approve",
        "linkedin:4": "reject",
        "linkedin:5": "skip",
        "alignerr:9": "approve",
    }


# ---------------------------------------------------------------------------
# apply_decisions (in-memory SQLite)
# ---------------------------------------------------------------------------


def _insert_job(
    session, external_id, platform=Platform.LINKEDIN, status=JobStatus.QUEUED
):
    job = Job(
        external_id=external_id,
        platform=platform,
        title="Dev",
        company="Co",
        status=status,
    )
    session.add(job)
    session.commit()
    return job


def test_apply_decisions_approves_a_queued_job(sync, session):
    _insert_job(session, "abc123", status=JobStatus.QUEUED)
    counts = sync.apply_decisions(session, {"linkedin:abc123": "approve"})
    assert counts == {
        "approved": 1,
        "rejected": 0,
        "skipped": 0,
        "unchanged": 0,
        "unknown": 0,
    }
    fetched = session.scalars(select(Job).where(Job.external_id == "abc123")).first()
    assert fetched.status == JobStatus.APPROVED


def test_apply_decisions_reject_skip_and_bad_keys(sync, session):
    _insert_job(session, "r1", platform=Platform.INDEED, status=JobStatus.QUEUED)
    _insert_job(session, "s1", platform=Platform.INDEED, status=JobStatus.DISCOVERED)
    counts = sync.apply_decisions(
        session,
        {
            "indeed:r1": "reject",
            "indeed:s1": "skip",
            "no-colon": "approve",  # bad key
            "notaplatform:x": "reject",  # unknown platform
            "indeed:missing": "approve",  # no DB row
        },
    )
    assert counts["rejected"] == 1
    assert counts["skipped"] == 1
    assert counts["unknown"] == 3  # never raises on bad keys


def test_apply_decisions_is_idempotent_on_decided_job(sync, session):
    _insert_job(session, "done", status=JobStatus.APPROVED)
    counts = sync.apply_decisions(session, {"linkedin:done": "approve"})
    assert counts["unchanged"] == 1
    assert counts["approved"] == 0


def test_apply_decisions_pulls_from_sheet_when_none(sync, session, config):
    _insert_job(session, "pull1", status=JobStatus.QUEUED)
    sync.backend.write_all(
        "jobs",
        [{"job_key": "linkedin:pull1", "decision": "approved"}],
    )
    counts = sync.apply_decisions(session)  # decisions=None -> pull_decisions()
    assert counts["approved"] == 1
    fetched = session.scalars(select(Job).where(Job.external_id == "pull1")).first()
    assert fetched.status == JobStatus.APPROVED


# ---------------------------------------------------------------------------
# sync_from_session
# ---------------------------------------------------------------------------


def test_sync_from_session_returns_counts(sync, session):
    j1 = _insert_job(session, "1", status=JobStatus.QUEUED)
    _insert_job(session, "2", platform=Platform.INDEED, status=JobStatus.QUEUED)
    session.add(Application(job_id=j1.id, status=ApplicationStatus.SUBMITTED))
    session.add(GigOpportunity(external_id="g1", network="alignerr", title="Gig"))
    session.commit()

    counts = sync.sync_from_session(session, limit=10)
    assert counts == {"jobs": 2, "applications": 1, "gigs": 1}

    job_rows = sync.backend.read("jobs")
    assert len(job_rows) == 2
    keys = {r["job_key"] for r in job_rows}
    assert keys == {"linkedin:1", "indeed:2"}


def test_push_gigs_derives_gig_key_from_orm_shaped_rows(sync, config):
    # Rows shaped like GigOpportunity columns (external_id/network, no gig_key)
    # are accepted and given a stable identity.
    sync.push_gigs(
        [
            {
                "external_id": "g1",
                "network": "alignerr",
                "title": "Gig",
                "description": "long text",
            }
        ]
    )
    rows = sync.backend.read("gigs")
    assert len(rows) == 1
    assert rows[0]["gig_key"] == "alignerr:g1"
    assert rows[0]["title"] == "Gig"
    # Extra columns from such rows are preserved (added to the header).
    assert rows[0]["description"] == "long text"


def test_push_gigs_accepts_aligned_rows_and_keeps_human_columns(sync, config):
    # A producer-aligned row (explicit gig_key + decision/notes + ORM extras).
    row = {
        "gig_key": "alignerr:c1",
        "external_id": "c1",
        "network": "alignerr",
        "title": "Coding Expert",
        "status": "discovered",
        "description": "long text",
        "decision": "",
        "notes": "",
    }
    assert sync.push_gigs([row]) == 1
    first = sync.backend.read("gigs")
    assert len(first) == 1
    # An explicit gig_key must NOT be re-derived / double-prefixed.
    assert first[0]["gig_key"] == "alignerr:c1"
    assert first[0]["description"] == "long text"  # extra column preserved

    # Human review edits survive a re-push of the same producer rows.
    path = config.directory / "gigs.csv"
    fieldnames, data = _read_csv(path)
    data[0]["decision"] = "approve"
    data[0]["notes"] = "strong fit"
    _write_csv(path, fieldnames, data)

    assert sync.push_gigs([row]) == 1
    second = sync.backend.read("gigs")
    assert len(second) == 1  # idempotent, no duplicate
    assert second[0]["decision"] == "approve"
    assert second[0]["notes"] == "strong fit"
    assert second[0]["gig_key"] == "alignerr:c1"


# ---------------------------------------------------------------------------
# converters + backend factory
# ---------------------------------------------------------------------------


def test_job_to_row_normalises_enum_datetime_and_none(session):
    job = _insert_job(session, "conv", status=JobStatus.QUEUED)
    row = job_to_row(job)
    assert row["job_key"] == "linkedin:conv"
    assert row["platform"] == "linkedin"
    assert row["status"] == "queued"
    assert row["salary"] == ""  # None -> ""
    assert row["decision"] == ""
    assert row["discovered_at"]  # ISO string
    assert set(row) == set(SHEET_SPECS["jobs"]["columns"])


def test_csv_backend_works_with_no_google_libs(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "gspread" or name.startswith("google."):
            raise ImportError("gspread/google-auth not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)

    # Importing the module / building the CSV backend must not need gspread.
    backend = make_backend(SheetConfig(directory=tmp_path, backend="csv"))
    assert isinstance(backend, CsvBackend)

    # The google backend fails cleanly with a helpful message.
    with pytest.raises(RuntimeError, match="uv pip install gspread google-auth"):
        make_backend(SheetConfig(directory=tmp_path, backend="google"))
