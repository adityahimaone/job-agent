"""Phase 1 columns and gig opportunities table.

Adds structured employment metadata, submission verification fields, run
observability columns, and the AI-training gig lane. Every change is guarded so
the migration is safe on databases created by 0001 (which already contains all
columns via create_all) as well as on older pre-Alembic databases.

Revision ID: 0002_phase1_columns
Revises: 0001_baseline
Create Date: 2026-11-06
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import Boolean, Column, DateTime, Enum, Float, Integer, String, Text, inspect

from job_agent.db.models import GigStatus

revision = "0002_phase1_columns"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

COLUMNS = {
    "jobs": [
        ("employment_type", String(64), "''"),
        ("remote_policy", String(64), "''"),
        ("timezone", String(64), "''"),
        ("hours_per_week", Float(), "NULL"),
        ("currency", String(16), "''"),
        ("salary_period", String(32), "''"),
        ("source", String(64), "''"),
    ],
    "applications": [
        ("verification_status", String(32), "''"),
        ("verification_note", Text(), "''"),
        ("approval_source", String(64), "''"),
        ("prompt_version", String(64), "''"),
        ("documents_manifest", Text(), "''"),
    ],
    "agent_runs": [
        ("timings", Text(), "''"),
        ("ai_calls", Integer(), "0"),
        ("estimated_cost_usd", Float(), "0.0"),
    ],
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    for table, columns in COLUMNS.items():
        if table not in tables:
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        for name, type_, server_default in columns:
            if name in existing:
                continue
            col = Column(name, type_, server_default=server_default)
            with op.batch_alter_table(table) as batch:
                batch.add_column(col)

    if "gig_opportunities" not in tables:
        op.create_table(
            "gig_opportunities",
            Column("id", Integer(), primary_key=True, autoincrement=True),
            Column("external_id", String(255), index=True),
            Column("network", String(64), index=True),
            Column("title", String(500)),
            Column("track", String(128), default=""),
            Column("description", Text(), default=""),
            Column("url", String(2048), default=""),
            Column("location", String(255), default=""),
            Column("remote", Boolean(), default=True),
            Column("pay_min", Float(), nullable=True),
            Column("pay_max", Float(), nullable=True),
            Column("currency", String(16), default="USD"),
            Column("pay_period", String(32), default="hour"),
            Column("employment_type", String(64), default="part_time"),
            Column("eligibility", Text(), default=""),
            Column("status", Enum(GigStatus), default=GigStatus.DISCOVERED),
            Column("notes", Text(), default=""),
            Column("discovered_at", DateTime()),
            Column("updated_at", DateTime()),
        )

    # Helpful indexes for the new query paths.
    for table, column, index_name in [
        ("jobs", "status", "ix_jobs_status"),
        ("jobs", "employment_type", "ix_jobs_employment_type"),
        ("applications", "applied_at", "ix_applications_applied_at"),
        ("agent_runs", "started_at", "ix_agent_runs_started_at"),
    ]:
        if table not in tables:
            continue
        names = {i["name"] for i in inspector.get_indexes(table)}
        if index_name not in names and column in {
            c["name"] for c in inspector.get_columns(table)
        }:
            try:
                op.create_index(index_name, table, [column])
            except Exception:
                # Older SQLite files may already have an unnamed index.
                pass


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "gig_opportunities" in inspector.get_table_names():
        op.drop_table("gig_opportunities")
    # Column drops are intentionally omitted: SQLite batch drops are destructive
    # and the extra columns are harmless if a user rolls back.
