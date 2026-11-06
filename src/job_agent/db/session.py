"""Database engine, session factory and versioned migrations."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from job_agent.config import Settings
from job_agent.db.models import Base
from job_agent.utils.logging import get_logger

log = get_logger(__name__)

_engine = None
_session_factory: sessionmaker[Session] | None = None


def get_engine(settings: Settings | None = None):
    """Get or create the SQLAlchemy engine."""
    global _engine
    if _engine is None:
        if settings is None:
            from job_agent.config import load_settings

            settings = load_settings()
        kwargs = {"echo": False, "pool_pre_ping": True}
        if settings.db_path.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        _engine = create_engine(settings.db_path, **kwargs)
    return _engine


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    """Get or create the session factory."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine(settings)
        _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return _session_factory


def get_session(settings: Settings | None = None) -> Session:
    """Create a new database session."""
    factory = get_session_factory(settings)
    return factory()


def init_db(settings: Settings | None = None) -> None:
    """Apply versioned Alembic migrations; fall back to create_all on failure.

    Alembic is the source of truth for schema changes. The legacy path remains
    as a safety net for in-memory test databases and stripped-down installs.
    """
    engine = get_engine(settings)
    try:
        run_migrations(settings)
        return
    except Exception as exc:  # pragma: no cover - defensive fallback
        log.warning("alembic_migration_fallback", error=str(exc)[:300])
        Base.metadata.create_all(engine)
        _migrate(engine)


def run_migrations(settings: Settings | None = None) -> None:
    """Run `alembic upgrade head` against the configured database."""
    import logging

    # Suppress Alembic's plugin-registration chatter BEFORE importing it.
    logging.getLogger("alembic").setLevel(logging.WARNING)

    from alembic import command
    from alembic.config import Config

    from job_agent.config import load_settings

    if settings is None:
        settings = load_settings()

    root = Path(__file__).resolve().parents[3]
    ini_path = root / "alembic.ini"
    if not ini_path.exists():
        raise FileNotFoundError(f"alembic.ini not found at {ini_path}")

    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.db_path)
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")


# Columns added after the baseline. Kept here so the non-Alembic fallback and
# older SQLite files still work. Alembic revisions are authoritative.
_LEGACY_COLUMNS: list[tuple[str, str, str]] = [
    ("jobs", "bookmarked", "BOOLEAN DEFAULT 0"),
    ("jobs", "duplicate_of_id", "INTEGER REFERENCES jobs(id)"),
    ("jobs", "employment_type", "VARCHAR(64) DEFAULT ''"),
    ("jobs", "remote_policy", "VARCHAR(64) DEFAULT ''"),
    ("jobs", "timezone", "VARCHAR(64) DEFAULT ''"),
    ("jobs", "hours_per_week", "FLOAT"),
    ("jobs", "currency", "VARCHAR(16) DEFAULT ''"),
    ("jobs", "salary_period", "VARCHAR(32) DEFAULT ''"),
    ("jobs", "source", "VARCHAR(64) DEFAULT ''"),
    ("applications", "verification_status", "VARCHAR(32) DEFAULT ''"),
    ("applications", "verification_note", "TEXT DEFAULT ''"),
    ("applications", "approval_source", "VARCHAR(64) DEFAULT ''"),
    ("applications", "prompt_version", "VARCHAR(64) DEFAULT ''"),
    ("applications", "documents_manifest", "TEXT DEFAULT ''"),
    ("agent_runs", "timings", "TEXT DEFAULT ''"),
    ("agent_runs", "ai_calls", "INTEGER DEFAULT 0"),
    ("agent_runs", "estimated_cost_usd", "FLOAT DEFAULT 0.0"),
]


def _migrate(engine) -> None:
    """Add columns that may be missing from an older schema."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, col_def in _LEGACY_COLUMNS:
            if table not in existing_tables:
                continue
            columns = {c["name"] for c in inspector.get_columns(table)}
            if column in columns:
                continue
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"))
            except Exception as exc:
                log.warning(
                    "legacy_column_add_failed",
                    table=table,
                    column=column,
                    error=str(exc)[:200],
                )


def reset_engine() -> None:
    """Reset the engine and session factory (for testing)."""
    global _engine, _session_factory
    if _engine:
        _engine.dispose()
    _engine = None
    _session_factory = None
