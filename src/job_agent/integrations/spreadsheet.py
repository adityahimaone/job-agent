"""Bidirectional spreadsheet integration: a human-editable approval surface.

The CSV backend mirrors jobs / applications / gigs into local CSV files that a
human can edit in Excel, Numbers, LibreOffice or import into Google Sheets. The
``decision`` column is the approval surface: edit it, then run
``job-agent sheet pull`` to apply the decisions to the database.

Values that leave the database are normalised to plain strings: enums to
``.value``, datetimes/date to ISO strings, booleans to ``true``/``false`` and
``None`` to an empty string. Google Sheets support is optional and lazily
imported, so the CSV backend works with no extra dependencies installed.
"""

from __future__ import annotations

import csv
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select

from job_agent.db.models import GigOpportunity, Job, JobStatus, Platform
from job_agent.db.repository import ApplicationRepository, JobRepository
from job_agent.utils.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Sheet layout
# ---------------------------------------------------------------------------

SHEET_SPECS: dict[str, dict] = {
    "jobs": {
        "key": "job_key",
        "columns": [
            "job_key",
            "platform",
            "title",
            "company",
            "location",
            "employment_type",
            "remote_policy",
            "timezone",
            "hours_per_week",
            "salary",
            "url",
            "score",
            "match_reason",
            "status",
            "decision",
            "notes",
            "discovered_at",
        ],
    },
    "applications": {
        "key": "job_key",
        "columns": [
            "job_key",
            "company",
            "title",
            "applied_at",
            "channel",
            "resume_path",
            "cover_letter_path",
            "verification_status",
            "status",
            "outcome",
            "follow_up_at",
            "notes",
        ],
    },
    "gigs": {
        "key": "gig_key",
        "columns": [
            "gig_key",
            "network",
            "title",
            "track",
            "pay_min",
            "pay_max",
            "currency",
            "pay_period",
            "employment_type",
            "remote",
            "url",
            "eligibility",
            "status",
            "decision",
            "notes",
        ],
    },
    "search_profiles": {
        "key": "name",
        "columns": [
            "name",
            "keywords",
            "locations",
            "employment_types",
            "remote_only",
            "min_hourly_rate",
            "max_hours_per_week",
            "timezone_alignment",
            "enabled",
        ],
    },
}

# Columns owned by the human. An upsert must never overwrite these.
_HUMAN_COLUMNS = ("decision", "notes")

_APPROVE = {"approve", "approved", "yes", "y"}
_REJECT = {"reject", "rejected", "no", "n"}
_SKIP = {"skip", "hold"}

# Only jobs sitting in one of these "undecided" states may be changed by a
# decision from the sheet.
_MODIFIABLE_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.AUTO_APPROVED,
    JobStatus.DISCOVERED,
    JobStatus.MATCHED,
    JobStatus.FILTERED,
    JobStatus.PENDING_HUMAN,
}

_TARGET_STATUSES = {
    "approve": JobStatus.APPROVED,
    "reject": JobStatus.REJECTED,
    "skip": JobStatus.SKIPPED,
}

_COUNT_KEYS = {
    "approve": "approved",
    "reject": "rejected",
    "skip": "skipped",
}


# ---------------------------------------------------------------------------
# Value normalisation helpers
# ---------------------------------------------------------------------------


def _cell(value: Any) -> str:
    """Coerce a database / user value to a plain spreadsheet string."""
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _relation(obj: Any, name: str) -> Any:
    """Safely read a lazy SQLAlchemy relationship; return None on failure."""
    try:
        return getattr(obj, name, None)
    except Exception:  # detached / expired instance
        return None


def _columns(spec_cols: list[str], *sources: Iterable[str]) -> list[str]:
    """Spec columns first, then any extra columns from the given sources."""
    cols = list(spec_cols)
    seen = set(spec_cols)
    for source in sources:
        for col in source:
            if col and col not in seen:
                cols.append(col)
                seen.add(col)
    return cols


def _row_keys(rows: Iterable[dict]) -> list[str]:
    keys: list[str] = []
    for row in rows:
        keys.extend(row.keys())
    return keys


def _normalize_decision(raw: Any) -> str:
    """Normalise a human decision to ``approve`` / ``reject`` / ``skip`` / ````."""
    value = str(raw or "").strip().lower()
    if value in _APPROVE:
        return "approve"
    if value in _REJECT:
        return "reject"
    if value in _SKIP:
        return "skip"
    return ""


def _parse_job_key(key: Any) -> tuple[Platform, str] | None:
    """Parse ``<platform>:<external_id>`` into a Platform + external id."""
    text = "" if key is None else str(key)
    parts = text.split(":", 1)
    if len(parts) != 2:
        return None
    platform_str = parts[0].strip().lower()
    external_id = parts[1].strip()
    if not platform_str or not external_id:
        return None
    try:
        platform = Platform(platform_str)
    except ValueError:
        return None
    return platform, external_id


def _merge_rows(
    existing: list[dict],
    fieldnames: list[str],
    rows: list[dict],
    key_field: str,
) -> list[dict]:
    """Merge incoming rows into existing rows, keyed by ``key_field``.

    - Every existing row (including rows the human added) is preserved.
    - Human-owned columns (decision/notes) are never overwritten.
    - Extra columns a human added are preserved on every row.
    """
    result: list[dict] = []
    index: dict[str, int] = {}
    for raw in existing:
        row = {c: _cell(raw.get(c)) for c in fieldnames}
        result.append(row)
        key = str(row.get(key_field, "") or "")
        if key:
            index[key] = len(result) - 1

    for incoming in rows:
        key = str(incoming.get(key_field, "") or "")
        if key in index:
            pos = index[key]
            merged = dict(result[pos])
            for col, value in incoming.items():
                if col in _HUMAN_COLUMNS:
                    continue
                merged[col] = value
            result[pos] = merged
        else:
            new_row = {c: "" for c in fieldnames}
            for col, value in incoming.items():
                if col in _HUMAN_COLUMNS:
                    continue
                new_row[col] = value
            result.append(new_row)
            if key:
                index[key] = len(result) - 1
    return result


# ---------------------------------------------------------------------------
# Row converters (database model -> spreadsheet row)
# ---------------------------------------------------------------------------


def job_to_row(job) -> dict:
    """Convert a ``Job`` ORM object to a jobs-sheet row."""
    platform = _cell(getattr(job, "platform", ""))
    external_id = _cell(getattr(job, "external_id", ""))
    match = _relation(job, "match_result")
    return {
        "job_key": f"{platform}:{external_id}",
        "platform": platform,
        "title": _cell(getattr(job, "title", "")),
        "company": _cell(getattr(job, "company", "")),
        "location": _cell(getattr(job, "location", "")),
        "employment_type": _cell(getattr(job, "employment_type", "")),
        "remote_policy": _cell(getattr(job, "remote_policy", "")),
        "timezone": _cell(getattr(job, "timezone", "")),
        "hours_per_week": _cell(getattr(job, "hours_per_week", None)),
        "salary": _cell(getattr(job, "salary", None)),
        "url": _cell(getattr(job, "url", "")),
        "score": _cell(getattr(match, "score", None) if match else None),
        "match_reason": _cell(getattr(match, "reasoning", "") if match else ""),
        "status": _cell(getattr(job, "status", "")),
        "decision": "",
        "notes": "",
        "discovered_at": _cell(getattr(job, "discovered_at", None)),
    }


def application_to_row(app) -> dict:
    """Convert an ``Application`` ORM object to an applications-sheet row."""
    job = _relation(app, "job")
    if job is not None:
        platform = _cell(getattr(job, "platform", ""))
        external_id = _cell(getattr(job, "external_id", ""))
        job_key = f"{platform}:{external_id}"
        company = _cell(getattr(job, "company", ""))
        title = _cell(getattr(job, "title", ""))
        channel = platform
    else:
        job_key = _cell(getattr(app, "job_id", ""))
        company = title = channel = ""
    return {
        "job_key": job_key,
        "company": company,
        "title": title,
        "applied_at": _cell(getattr(app, "applied_at", None)),
        "channel": channel,
        "resume_path": _cell(getattr(app, "resume_path", "")),
        "cover_letter_path": _cell(getattr(app, "cover_letter_path", "")),
        "verification_status": _cell(getattr(app, "verification_status", "")),
        "status": _cell(getattr(app, "status", "")),
        "outcome": "",
        "follow_up_at": "",
        "notes": "",
    }


def gig_to_row(gig) -> dict:
    """Convert a ``GigOpportunity`` ORM object to a gigs-sheet row."""
    network = _cell(getattr(gig, "network", ""))
    external_id = _cell(getattr(gig, "external_id", ""))
    return {
        "gig_key": f"{network}:{external_id}",
        "network": network,
        "title": _cell(getattr(gig, "title", "")),
        "track": _cell(getattr(gig, "track", "")),
        "pay_min": _cell(getattr(gig, "pay_min", None)),
        "pay_max": _cell(getattr(gig, "pay_max", None)),
        "currency": _cell(getattr(gig, "currency", "")),
        "pay_period": _cell(getattr(gig, "pay_period", "")),
        "employment_type": _cell(getattr(gig, "employment_type", "")),
        "remote": _cell(getattr(gig, "remote", None)),
        "url": _cell(getattr(gig, "url", "")),
        "eligibility": _cell(getattr(gig, "eligibility", "")),
        "status": _cell(getattr(gig, "status", "")),
        "decision": "",
        "notes": "",
    }


def _normalize_gig_rows(rows: list[dict]) -> list[dict]:
    """Accept gig rows shaped like the ORM model (``external_id``/``network``).

    Some producers (e.g. ``GigService.to_rows()``) emit rows keyed like the
    ``GigOpportunity`` columns rather than the sheet. Derive ``gig_key`` when it
    is missing so the upsert still has a stable identity. Any extra columns are
    preserved by the backend downstream.
    """
    out: list[dict] = []
    for row in rows:
        if "gig_key" not in row and row.get("network") and row.get("external_id"):
            row = {
                **row,
                "gig_key": f"{_cell(row['network'])}:{_cell(row['external_id'])}",
            }
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Configuration + backend factory
# ---------------------------------------------------------------------------


@dataclass
class SheetConfig:
    directory: Path
    backend: str = "csv"
    spreadsheet_id: str = ""
    credentials_path: str = ""
    token_path: str = "~/.hermes/google_token.json"

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)

    @classmethod
    def from_settings(cls, settings) -> "SheetConfig":
        sheets = settings.sheets
        return cls(
            directory=settings.sheets_dir,
            backend=sheets.backend,
            spreadsheet_id=sheets.spreadsheet_id,
            credentials_path=sheets.credentials_path,
            token_path=getattr(sheets, "token_path", "")
            or "~/.hermes/google_token.json",
        )


class SheetBackend:
    """Base class for sheet storage backends."""

    def read(self, sheet: str) -> list[dict]:
        raise NotImplementedError

    def upsert(self, sheet: str, key_field: str, rows: list[dict]) -> int:
        raise NotImplementedError

    def write_all(self, sheet: str, rows: list[dict]) -> int:
        raise NotImplementedError


class CsvBackend(SheetBackend):
    """One ``<sheet>.csv`` file per sheet inside ``directory``."""

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, sheet: str) -> Path:
        return self.directory / f"{sheet}.csv"

    @staticmethod
    def _spec(sheet: str) -> list[str]:
        if sheet not in SHEET_SPECS:
            raise KeyError(f"Unknown sheet: {sheet}")
        return SHEET_SPECS[sheet]["columns"]

    def _read_with_columns(
        self, path: Path, spec_cols: list[str]
    ) -> tuple[list[dict], list[str]]:
        if not path.exists() or path.stat().st_size == 0:
            return [], list(spec_cols)
        with open(path, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            file_cols = [c for c in (reader.fieldnames or []) if c]
            cols = _columns(spec_cols, file_cols)
            rows = [{c: _cell(raw.get(c)) for c in cols} for raw in reader]
            return rows, file_cols

    def _write(self, path: Path, cols: list[str], rows: list[dict]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.stem}.", suffix=".tmp", dir=str(self.directory)
        )
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                writer.writeheader()
                for row in rows:
                    writer.writerow({c: _cell(row.get(c)) for c in cols})
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def read(self, sheet: str) -> list[dict]:
        spec_cols = self._spec(sheet)
        rows, _ = self._read_with_columns(self._path(sheet), spec_cols)
        return rows

    def upsert(self, sheet: str, key_field: str, rows: list[dict]) -> int:
        path = self._path(sheet)
        spec_cols = self._spec(sheet)
        existing, file_cols = self._read_with_columns(path, spec_cols)
        cols = _columns(spec_cols, file_cols, _row_keys(rows))
        merged = _merge_rows(existing, cols, rows, key_field)
        self._write(path, cols, merged)
        return len(rows)

    def write_all(self, sheet: str, rows: list[dict]) -> int:
        spec_cols = self._spec(sheet)
        cols = _columns(spec_cols, _row_keys(rows))
        self._write(self._path(sheet), cols, rows)
        return len(rows)


def _google_credentials(config: SheetConfig, scopes: list[str]):
    """Resolve Google credentials: service account JSON, or OAuth user token.

    A service-account file works headless in CI; an installed-app OAuth file plus
    a saved token (``~/.hermes/google_token.json``) lets the agent act as the
    person, which is what a personal spreadsheet approval surface needs.
    """
    import json
    from pathlib import Path

    cred_path = (
        Path(config.credentials_path).expanduser() if config.credentials_path else None
    )
    if cred_path and cred_path.exists():
        payload = json.loads(cred_path.read_text())
        if payload.get("type") == "service_account":
            from google.oauth2.service_account import Credentials

            return Credentials.from_service_account_file(str(cred_path), scopes=scopes)

    token_path = Path(config.token_path or "~/.hermes/google_token.json").expanduser()
    if not token_path.exists():
        raise RuntimeError(
            f"No Google credentials: {cred_path or '(none)'} is not a service "
            f"account and no OAuth token at {token_path}."
        )
    data = json.loads(token_path.read_text())
    from google.oauth2.credentials import Credentials

    # Hermes stores the access token under "token" (not "access_token").
    return Credentials(
        token=data.get("token") or data.get("access_token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri") or "https://oauth2.googleapis.com/token",
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes") or scopes,
    )


class GoogleSheetsBackend(SheetBackend):
    """Google Sheets backend. Requires optional ``gspread`` + ``google-auth``.

    The imports are done inside ``__init__`` so that this class is always
    importable and the CSV backend never depends on the Google libraries.
    """

    def __init__(self, config: SheetConfig):
        try:
            import gspread
        except ImportError as exc:
            raise RuntimeError(
                "Google Sheets backend requires: uv pip install gspread google-auth ..."
            ) from exc
        self._gspread = gspread
        self._config = config
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = _google_credentials(config, scopes)
        self._client = gspread.authorize(creds)
        self._spreadsheet = self._client.open_by_key(config.spreadsheet_id)

    @staticmethod
    def _spec(sheet: str) -> list[str]:
        return SHEET_SPECS[sheet]["columns"]

    def _worksheet(self, sheet: str):
        try:
            return self._spreadsheet.worksheet(sheet)
        except self._gspread.WorksheetNotFound:
            cols = self._spec(sheet)
            ws = self._spreadsheet.add_worksheet(
                title=sheet, rows=1, cols=max(len(cols), 1)
            )
            ws.append_row(cols, value_input_option="RAW")
            return ws

    def _rewrite(self, sheet: str, cols: list[str], rows: list[dict]) -> None:
        ws = self._worksheet(sheet)
        ws.clear()
        ws.append_row(cols, value_input_option="RAW")
        if rows:
            ws.append_rows(
                [[_cell(r.get(c)) for c in cols] for r in rows],
                value_input_option="RAW",
            )

    def read(self, sheet: str) -> list[dict]:
        return [dict(r) for r in self._worksheet(sheet).get_all_records()]

    def upsert(self, sheet: str, key_field: str, rows: list[dict]) -> int:
        spec_cols = self._spec(sheet)
        existing = self.read(sheet)
        cols = _columns(spec_cols, _row_keys(existing), _row_keys(rows))
        merged = _merge_rows(existing, cols, rows, key_field)
        self._rewrite(sheet, cols, merged)
        return len(rows)

    def write_all(self, sheet: str, rows: list[dict]) -> int:
        spec_cols = self._spec(sheet)
        cols = _columns(spec_cols, _row_keys(rows))
        self._rewrite(sheet, cols, rows)
        return len(rows)


def google_backend_available() -> bool:
    """Return whether the optional Google Sheets dependencies are importable."""
    try:
        import gspread  # noqa: F401
        from google.oauth2.service_account import Credentials  # noqa: F401
    except ImportError:
        return False
    return True


def make_backend(config: SheetConfig) -> SheetBackend:
    """Build the backend selected by ``config.backend``."""
    if (config.backend or "csv").lower() == "google":
        return GoogleSheetsBackend(config)
    return CsvBackend(config.directory)


# ---------------------------------------------------------------------------
# High-level synchronisation + human decisions
# ---------------------------------------------------------------------------


class SheetSync:
    def __init__(self, config: SheetConfig):
        self.config = config
        self._backend: SheetBackend | None = None

    @property
    def backend(self) -> SheetBackend:
        # Lazy so local-only operations (e.g. ensure_templates) work even when
        # a google backend is configured but its optional deps are missing.
        if self._backend is None:
            self._backend = make_backend(self.config)
        return self._backend

    def ensure_templates(self) -> list[Path]:
        """Create header-only CSVs for every sheet if missing; return all paths."""
        self.config.directory.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for sheet, spec in SHEET_SPECS.items():
            path = self.config.directory / f"{sheet}.csv"
            if not path.exists():
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=spec["columns"])
                    writer.writeheader()
            paths.append(path)
        return paths

    def push_jobs(self, jobs: list[dict]) -> int:
        return self.backend.upsert("jobs", SHEET_SPECS["jobs"]["key"], jobs)

    def push_applications(self, apps: list[dict]) -> int:
        return self.backend.upsert(
            "applications", SHEET_SPECS["applications"]["key"], apps
        )

    def push_gigs(self, gigs: list[dict]) -> int:
        return self.backend.upsert(
            "gigs", SHEET_SPECS["gigs"]["key"], _normalize_gig_rows(gigs)
        )

    def pull_decisions(self) -> dict[str, str]:
        """Read decisions from the jobs and gigs sheets, normalised."""
        result: dict[str, str] = {}
        for sheet, key_field in (("jobs", "job_key"), ("gigs", "gig_key")):
            for row in self.backend.read(sheet):
                key = str(row.get(key_field, "") or "").strip()
                if not key:
                    continue
                canonical = _normalize_decision(row.get("decision", ""))
                if canonical:
                    # Keys not present in the DB are intentionally kept; the
                    # caller (apply_decisions) simply ignores unknown ones.
                    result[key] = canonical
        return result

    def read_search_profiles(self) -> list[dict]:
        return self.backend.read("search_profiles")

    def sync_from_session(self, session, *, limit: int = 1000) -> dict[str, int]:
        """Mirror jobs, applications and gigs from a DB session into the sheet."""
        self.ensure_templates()
        job_rows = [job_to_row(j) for j in JobRepository(session).list_all(limit=limit)]
        app_rows = [
            application_to_row(a)
            for a in ApplicationRepository(session).list_all(limit=limit)
        ]
        try:
            gigs = list(session.scalars(select(GigOpportunity).limit(limit)).all())
        except Exception as exc:  # gigs table may be absent on older schemas
            log.warning("sheets.gigs_query_failed", error=str(exc))
            gigs = []
        gig_rows = [gig_to_row(g) for g in gigs]
        return {
            "jobs": self.push_jobs(job_rows),
            "applications": self.push_applications(app_rows),
            "gigs": self.push_gigs(gig_rows),
        }

    def apply_decisions(
        self, session, decisions: dict[str, str] | None = None
    ) -> dict[str, int]:
        """Apply human decisions to queued/undecided jobs in the database."""
        if decisions is None:
            decisions = self.pull_decisions()
        counts = {
            "approved": 0,
            "rejected": 0,
            "skipped": 0,
            "unchanged": 0,
            "unknown": 0,
        }
        for key, value in decisions.items():
            canonical = _normalize_decision(value) if value else ""
            if canonical not in _TARGET_STATUSES:
                counts["unchanged"] += 1
                continue
            parsed = _parse_job_key(key)
            if parsed is None:
                counts["unknown"] += 1
                continue
            platform, external_id = parsed
            job = session.scalars(
                select(Job).where(
                    Job.external_id == external_id, Job.platform == platform
                )
            ).first()
            if job is None:
                counts["unknown"] += 1
                continue
            if job.status not in _MODIFIABLE_STATUSES:
                counts["unchanged"] += 1
                continue
            job.status = _TARGET_STATUSES[canonical]
            counts[_COUNT_KEYS[canonical]] += 1
        session.commit()
        return counts
