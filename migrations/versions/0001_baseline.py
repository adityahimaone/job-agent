"""Baseline schema.

Idempotent on purpose: existing installs created all tables with
``Base.metadata.create_all()`` before Alembic was wired up. Using the current
metadata here means a fresh database gets every column from day one, while an
existing database is simply stamped at this revision and picked up by 0002.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-06
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

from job_agent.db.models import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in inspector.get_table_names():
            table.drop(bind, checkfirst=True)
