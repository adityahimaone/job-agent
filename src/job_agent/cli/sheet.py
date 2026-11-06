"""CLI commands for the spreadsheet mirror and human approval surface.

The group is built by :func:`build_cli_group` and registered into the main CLI
by the application entry point. It is intentionally kept independent of
``main.py`` so it can be imported (and tested) on its own.
"""

from __future__ import annotations

import click

from job_agent.db.session import get_session, init_db
from job_agent.integrations.spreadsheet import (
    SheetConfig,
    SheetSync,
    google_backend_available,
)
from job_agent.utils.logging import get_logger

log = get_logger(__name__)


def _settings(ctx: click.Context):
    """Resolve Settings from the root context, with a safe standalone fallback."""
    obj = ctx.find_root().obj or {}
    settings = obj.get("settings")
    if settings is None:
        from job_agent.config import load_settings

        settings = load_settings()
    return settings


def _sync(ctx: click.Context) -> SheetSync:
    return SheetSync(SheetConfig.from_settings(_settings(ctx)))


def build_cli_group() -> click.Group:
    @click.group("sheet")
    def sheet() -> None:
        """Spreadsheet mirror and human approval surface."""

    @sheet.command("template")
    @click.pass_context
    def template(ctx: click.Context) -> None:
        """Create header-only CSVs in the configured directory."""
        sync = _sync(ctx)
        paths = sync.ensure_templates()
        click.echo(f"Sheets directory: {sync.config.directory}")
        for path in paths:
            click.echo(f"  {path}")

    @sheet.command("push")
    @click.option(
        "--limit",
        "-n",
        default=1000,
        show_default=True,
        help="Max rows pushed per sheet.",
    )
    @click.pass_context
    def push(ctx: click.Context, limit: int) -> None:
        """Push DB rows (jobs / applications / gigs) into the sheet."""
        settings = _settings(ctx)
        init_db(settings)
        session = get_session(settings)
        try:
            sync = _sync(ctx)
            counts = sync.sync_from_session(session, limit=limit)
            click.echo(
                f"Pushed {counts['jobs']} jobs, "
                f"{counts['applications']} applications, "
                f"{counts['gigs']} gigs."
            )
        finally:
            session.close()

    @sheet.command("pull")
    @click.pass_context
    def pull(ctx: click.Context) -> None:
        """Apply decisions from the sheet to the database."""
        settings = _settings(ctx)
        init_db(settings)
        session = get_session(settings)
        try:
            sync = _sync(ctx)
            counts = sync.apply_decisions(session)
            click.echo(
                f"approved={counts['approved']} rejected={counts['rejected']} "
                f"skipped={counts['skipped']} unchanged={counts['unchanged']} "
                f"unknown={counts['unknown']}"
            )
        finally:
            session.close()

    @sheet.command("decisions")
    @click.pass_context
    def decisions(ctx: click.Context) -> None:
        """Print decisions found in the sheet without changing the DB."""
        sync = _sync(ctx)
        found = sync.pull_decisions()
        if not found:
            click.echo("No decisions found.")
            return
        for key, value in found.items():
            click.echo(f"  {key}: {value}")

    @sheet.command("path")
    @click.pass_context
    def path(ctx: click.Context) -> None:
        """Print the configured sheet directory."""
        click.echo(str(_settings(ctx).sheets_dir))

    @sheet.command("google-check")
    @click.pass_context
    def google_check(ctx: click.Context) -> None:
        """Report whether the optional Google Sheets backend is available."""
        if google_backend_available():
            click.echo(
                "Google Sheets backend available (gspread + google-auth installed)."
            )
        else:
            click.echo(
                "Google Sheets backend NOT available. "
                "Install with: uv pip install gspread google-auth"
            )

    return sheet
