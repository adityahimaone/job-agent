"""CLI entry point for job-agent."""

from __future__ import annotations

import getpass
from pathlib import Path

import click

from job_agent.config import load_settings
from job_agent.db.models import Platform
from job_agent.db.session import get_session, init_db
from job_agent.utils.crypto import encrypt
from job_agent.utils.logging import setup_logging


@click.group()
@click.option("--config", "-c", default=None, help="Path to config YAML override.")
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
)
@click.pass_context
def cli(ctx: click.Context, config: str | None, log_level: str) -> None:
    """Job Agent - Autonomous job application agent."""
    setup_logging(log_level=log_level)
    ctx.ensure_object(dict)
    ctx.obj["settings"] = load_settings(config)


@cli.command("init-db")
@click.pass_context
def cmd_init_db(ctx: click.Context) -> None:
    """Initialize the database (create all tables)."""
    settings = ctx.obj["settings"]
    init_db(settings)
    click.echo(f"Database initialized at: {settings.db_path}")


@cli.command("add-credential")
@click.argument(
    "platform",
    type=click.Choice(
        ["linkedin", "indeed", "glassdoor", "ziprecruiter", "dice", "wellfound"]
    ),
)
@click.option("--username", "-u", prompt=True, help="Platform username/email.")
@click.pass_context
def cmd_add_credential(ctx: click.Context, platform: str, username: str) -> None:
    """Store encrypted credentials for a platform."""
    from job_agent.db.repository import CredentialRepository

    password = getpass.getpass("Password: ")
    encrypted = encrypt(password)

    settings = ctx.obj["settings"]
    init_db(settings)
    session = get_session(settings)
    try:
        repo = CredentialRepository(session)
        repo.upsert(
            platform=Platform(platform),
            username=username,
            encrypted_password=encrypted,
        )
        session.commit()
        click.echo(f"Credentials stored for {platform}.")
    finally:
        session.close()


@cli.command("run")
@click.option("--profile", "-p", required=True, help="Path to profile YAML.")
@click.option(
    "--platform",
    type=click.Choice(
        ["linkedin", "indeed", "glassdoor", "ziprecruiter", "dice", "wellfound"]
    ),
)
@click.option("--dry-run", is_flag=True, help="Discover and match only, don't apply.")
@click.option(
    "--live",
    is_flag=True,
    help="Opt out of dry-run. Submissions still require an approval.",
)
@click.option("--once", is_flag=True, help="Run once instead of on schedule.")
@click.pass_context
def cmd_run(
    ctx: click.Context,
    profile: str,
    platform: str | None,
    dry_run: bool,
    live: bool,
    once: bool,
) -> None:
    """Run the job agent pipeline."""
    from job_agent.orchestrator.engine import OrchestratorEngine

    settings = ctx.obj["settings"]
    if dry_run and live:
        raise click.UsageError("--dry-run and --live are mutually exclusive")
    if dry_run:
        settings.agent.dry_run = True
    elif live:
        settings.agent.dry_run = False
        click.echo(
            "Live mode: jobs will be submitted only after an explicit approval "
            "(dashboard, sheet, or bot)."
        )

    init_db(settings)
    engine = OrchestratorEngine(settings)
    if once:
        engine.run_once(
            profile_path=profile, platform=platform, skip_activity_check=True
        )
    else:
        engine.start(profile_path=profile, platform=platform)


@cli.command("apply-approved")
@click.option(
    "--dry-run", is_flag=True, help="Report what would be submitted, change nothing."
)
@click.option(
    "--live", is_flag=True, help="Opt out of dry-run and submit approved jobs."
)
@click.pass_context
def cmd_apply_approved(ctx: click.Context, dry_run: bool, live: bool) -> None:
    """Apply to all approved jobs from the review queue (skips discovery)."""
    from job_agent.orchestrator.pipeline import apply_approved

    settings = ctx.obj["settings"]
    if dry_run and live:
        raise click.UsageError("--dry-run and --live are mutually exclusive")
    if dry_run:
        settings.agent.dry_run = True
    elif live:
        settings.agent.dry_run = False
    init_db(settings)
    stats = apply_approved(settings)
    click.echo(
        f"Done: {stats['applied']} applied, {stats['failed']} failed, {stats['skipped']} skipped"
    )


@cli.command("search")
@click.option(
    "--platform",
    "-P",
    default="linkedin",
    type=click.Choice(
        ["linkedin", "indeed", "glassdoor", "ziprecruiter", "dice", "wellfound"]
    ),
)
@click.option("--query", "-q", required=True, help="Search query.")
@click.option("--location", "-l", default="", help="Location filter.")
@click.option("--limit", "-n", default=25, help="Max results.")
@click.pass_context
def cmd_search(
    ctx: click.Context,
    platform: str,
    query: str,
    location: str,
    limit: int,
) -> None:
    """Search for jobs on a platform (without applying)."""
    click.echo(f"Searching {platform} for: {query}")
    # Imports deferred to avoid circular / heavy imports at startup
    from job_agent.orchestrator.pipeline import discover_jobs

    settings = ctx.obj["settings"]
    init_db(settings)
    jobs = discover_jobs(
        settings=settings,
        platform_name=platform,
        query=query,
        location=location,
        limit=limit,
    )
    click.echo(f"Found {len(jobs)} jobs:")
    for j in jobs:
        click.echo(f"  [{j.platform.value}] {j.title} @ {j.company} - {j.url}")


@cli.command("ego")
@click.argument("action", type=click.Choice(["doctor", "search"]))
@click.option("--platform", "-P", default="linkedin", help="Platform to search.")
@click.option("--query", "-q", default="", help="Search query.")
@click.option("--location", "-l", default="", help="Location filter.")
@click.option("--limit", "-n", default=10, help="Max results.")
@click.pass_context
def cmd_ego(ctx, action, platform, query, location, limit):
    """Talk to ego(lite): prove the bridge, or run one search."""
    from job_agent.platforms.ego import client as ego_client

    settings = ctx.obj["settings"]
    cfg = settings.ego
    resolver = getattr(ego_client, "resolve_ego_binary", None)
    binary = cfg.binary or (resolver() if resolver else ego_client.DEFAULT_BINARY)
    client = ego_client.EgoClient(
        host=cfg.host or None,
        user=cfg.user,
        binary=binary,
        timeout=cfg.timeout,
    )
    try:
        if action == "doctor":
            click.echo(f"target: {client.target or 'local'}")
            click.echo(f"binary: {client.binary}")
            info = client.doctor()
            click.echo(f"title : {info.get('title')}")
            click.echo(f"url   : {info.get('url')}")
            click.echo(f"space : {info.get('spaceId')}")
            click.echo("ego bridge OK")
            return
        from job_agent.platforms.ego.discovery import EgoDiscovery

        if not query:
            raise click.UsageError("--query is required for search")
        found = EgoDiscovery(
            client, platform, task_space=cfg.task_space, settle_ms=cfg.settle_ms
        ).search(query=query, location=location, limit=limit)
        click.echo(f"found {len(found)} postings")
        for j in found:
            click.echo(f"  {j.title} @ {j.company} - {j.url}")
    except ego_client.EgoError as exc:
        click.echo(f"ego error: {exc}")
        raise SystemExit(1)


@cli.command("dashboard")
@click.pass_context
def cmd_dashboard(ctx: click.Context) -> None:
    """Start the web dashboard."""
    from job_agent.dashboard.app import create_app

    settings = ctx.obj["settings"]
    init_db(settings)
    app = create_app(settings)
    if settings.dashboard.auth_enabled:
        click.echo(f"Dashboard token: {settings.dashboard_token()}")
        click.echo("  (also stored at ~/.job-agent/dashboard_token)")
    app.run(
        host=settings.dashboard.host,
        port=settings.dashboard.port,
        debug=settings.dashboard.debug,
    )


@cli.command("launch-browser")
@click.option("--port", default=9222, help="CDP debugging port.")
@click.pass_context
def cmd_launch_browser(ctx: click.Context, port: int) -> None:
    """Launch Chrome with remote debugging for CDP-based platforms (Indeed, Glassdoor).

    Log into Indeed/Glassdoor in the opened browser, then run job-agent
    commands in another terminal. The agent will connect to this browser
    instead of launching its own, bypassing Cloudflare bot detection.
    """
    import shutil
    import subprocess
    import sys

    settings = ctx.obj["settings"]
    profile_dir = Path(settings.browser.state_dir).expanduser() / "cdp_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if not chrome:
        click.echo("Chrome not found. Install Google Chrome first.", err=True)
        sys.exit(1)

    cmd = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://www.indeed.com",
        "https://www.glassdoor.com",
        "https://www.dice.com",
        "https://wellfound.com",
    ]
    click.echo(f"Launching Chrome with CDP on port {port}...")
    click.echo("Log into each site, then run job-agent in another terminal.")
    click.echo(f"CDP URL: http://localhost:{port}")
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        click.echo("\nBrowser closed.")


@cli.group("bot")
@click.pass_context
def cmd_bot(ctx: click.Context) -> None:
    """Manage interactive bots (Telegram, Discord)."""
    pass


@cmd_bot.command("telegram")
@click.pass_context
def cmd_bot_telegram(ctx: click.Context) -> None:
    """Start the interactive Telegram bot (long-polling)."""
    from job_agent.bots.telegram_bot import TelegramBot

    settings = ctx.obj["settings"]
    init_db(settings)
    bot = TelegramBot(settings)
    click.echo("Starting Telegram bot... (Ctrl+C to stop)")
    bot.start()


@cmd_bot.command("setup")
@click.pass_context
def cmd_bot_setup(ctx: click.Context) -> None:
    """Interactive setup wizard for Telegram and Discord bots."""
    click.echo("=== Bot Setup ===\n")

    # --- Telegram ---
    click.echo("-- Telegram Bot --")
    click.echo("  1. Message @BotFather on Telegram and send /newbot")
    click.echo("  2. Follow the prompts to name your bot")
    click.echo("  3. Copy the bot token BotFather gives you\n")

    if click.confirm("Set up Telegram bot now?", default=True):
        token = click.prompt("  Bot token (from BotFather)")
        click.echo("\n  To get your chat ID:")
        click.echo("  1. Send /start to your new bot in Telegram")
        click.echo(f"  2. Visit: https://api.telegram.org/bot{token}/getUpdates")
        click.echo('  3. Look for "chat":{"id":XXXXXXX} in the response\n')
        chat_id = click.prompt("  Your chat ID")

        click.echo("\n  Add these to your .env file:")
        click.echo(f"    JOB_AGENT_TELEGRAM_BOT_TOKEN={token}")
        click.echo(f"    JOB_AGENT_TELEGRAM_CHAT_ID={chat_id}")

        env_path = Path(".env")
        if click.confirm("\n  Write to .env automatically?", default=True):
            lines = []
            if env_path.exists():
                existing = env_path.read_text()
                for line in existing.splitlines():
                    if not line.startswith(
                        ("JOB_AGENT_TELEGRAM_BOT_TOKEN=", "JOB_AGENT_TELEGRAM_CHAT_ID=")
                    ):
                        lines.append(line)
            lines.append(f"JOB_AGENT_TELEGRAM_BOT_TOKEN={token}")
            lines.append(f"JOB_AGENT_TELEGRAM_CHAT_ID={chat_id}")
            env_path.write_text("\n".join(lines) + "\n")
            click.echo("  Saved to .env\n")
        click.echo("  Start with: job-agent bot telegram\n")

    # --- Discord ---
    click.echo("-- Discord Bot --")
    click.echo("  1. Go to https://discord.com/developers/applications")
    click.echo("  2. Create a New Application")
    click.echo("  3. Go to Bot tab and copy the token")
    click.echo("  4. Copy Application ID and Public Key from General tab")
    click.echo(
        "  5. Invite bot: OAuth2 > URL Generator > scopes: bot, applications.commands\n"
    )

    if click.confirm("Set up Discord bot now?", default=False):
        bot_token = click.prompt("  Bot token")
        app_id = click.prompt("  Application ID")
        pub_key = click.prompt("  Public Key")

        click.echo("\n  Add these to your .env file:")
        click.echo(f"    JOB_AGENT_DISCORD_BOT_TOKEN={bot_token}")
        click.echo(f"    JOB_AGENT_DISCORD_APPLICATION_ID={app_id}")
        click.echo(f"    JOB_AGENT_DISCORD_PUBLIC_KEY={pub_key}")

        env_path = Path(".env")
        if click.confirm("\n  Write to .env automatically?", default=True):
            lines = []
            if env_path.exists():
                existing = env_path.read_text()
                for line in existing.splitlines():
                    if not line.startswith(
                        (
                            "JOB_AGENT_DISCORD_BOT_TOKEN=",
                            "JOB_AGENT_DISCORD_APPLICATION_ID=",
                            "JOB_AGENT_DISCORD_PUBLIC_KEY=",
                        )
                    ):
                        lines.append(line)
            lines.append(f"JOB_AGENT_DISCORD_BOT_TOKEN={bot_token}")
            lines.append(f"JOB_AGENT_DISCORD_APPLICATION_ID={app_id}")
            lines.append(f"JOB_AGENT_DISCORD_PUBLIC_KEY={pub_key}")
            env_path.write_text("\n".join(lines) + "\n")
            click.echo("  Saved to .env\n")

        click.echo("  Next steps:")
        click.echo("    1. Set Interactions Endpoint URL in Discord Developer Portal")
        click.echo("       to: https://your-domain/discord/interactions")
        click.echo(
            "    2. Register commands: job-agent bot discord-register --guild-id YOUR_GUILD_ID"
        )
        click.echo("    3. Start the dashboard: job-agent dashboard\n")

    click.echo(
        "Done! Run 'job-agent bot telegram' or 'job-agent dashboard' to get started."
    )


@cmd_bot.command("discord-register")
@click.option(
    "--guild-id", default=None, help="Guild ID for instant command availability."
)
@click.pass_context
def cmd_bot_discord_register(ctx: click.Context, guild_id: str | None) -> None:
    """Register Discord slash commands."""
    from job_agent.bots.discord_interactions import DiscordInteractionHandler

    settings = ctx.obj["settings"]
    handler = DiscordInteractionHandler(settings)
    if handler.register_commands(guild_id=guild_id):
        scope = f"guild {guild_id}" if guild_id else "global (may take up to 1h)"
        click.echo(f"Discord commands registered ({scope}).")
    else:
        click.echo(
            "Failed to register Discord commands. Check bot_token and application_id."
        )


@cli.command("setup")
@click.pass_context
def cmd_setup(ctx: click.Context) -> None:
    """Interactive setup wizard for new users."""
    settings = ctx.obj["settings"]
    click.echo("=== Job Agent Setup Wizard ===\n")

    # Step 1: Init DB
    click.echo("Step 1: Initializing database...")
    init_db(settings)
    click.echo(f"  Database created at: {settings.db_path}\n")

    # Step 2: API key check
    click.echo("Step 2: Checking API key...")
    if settings.anthropic_api_key:
        click.echo("  Anthropic API key found in environment.\n")
    else:
        click.echo("  WARNING: ANTHROPIC_API_KEY not set in .env or environment.")
        click.echo("  Set it before running the agent.\n")

    # Step 3: Add credentials
    if click.confirm("Step 3: Would you like to add platform credentials now?"):
        for p in ["linkedin", "indeed", "glassdoor"]:
            if click.confirm(f"  Add {p} credentials?", default=(p == "linkedin")):
                username = click.prompt(f"    {p} username/email")
                password = getpass.getpass(f"    {p} password: ")
                encrypted = encrypt(password)
                session = get_session(settings)
                try:
                    from job_agent.db.repository import CredentialRepository

                    CredentialRepository(session).upsert(
                        Platform(p), username, encrypted
                    )
                    session.commit()
                    click.echo(f"    {p} credentials saved.\n")
                finally:
                    session.close()

    # Step 4: Bot setup
    if click.confirm("Step 4: Set up Telegram or Discord bot notifications?"):
        ctx.invoke(cmd_bot_setup)

    click.echo("\nSetup complete! Next steps:")
    click.echo("  1. Edit config/templates/candidate_profile.md (your verified facts)")
    click.echo(
        "  2. Edit config/templates/*.md (cover letter, email, resume templates)"
    )
    click.echo("  3. Review config/profiles/aditya.yaml (search profile)")
    click.echo("  4. Run: job-agent content check")
    click.echo(
        "  5. Run: job-agent run --profile config/profiles/aditya.yaml --once --dry-run"
    )
    click.echo(
        "  6. Review jobs in the sheet: job-agent sheet template && job-agent sheet push"
    )
    click.echo("  See docs/TEMPLATES.md, docs/SPREADSHEET.md and docs/COVERAGE.md")


@cli.group("content")
@click.pass_context
def cmd_content(ctx: click.Context) -> None:
    """Inspect and preview the human-editable document templates."""
    pass


@cmd_content.command("check")
@click.pass_context
def cmd_content_check(ctx: click.Context) -> None:
    """Validate the candidate profile and every Markdown template."""
    from job_agent.content.candidate import load_candidate_profile
    from job_agent.content.templates import load_template_set

    settings = ctx.obj["settings"]
    profile_path = settings.resolve_path(settings.content.candidate_profile)
    candidate = load_candidate_profile(profile_path)
    templates = load_template_set(settings.resolve_path(settings.content.templates_dir))
    click.echo(f"Candidate : {candidate.name} <{candidate.email}>")
    click.echo(f"Facts     : {len(candidate.facts)} verified claims")
    click.echo(
        f"Roles     : {len(candidate.roles)}  Projects: {len(candidate.projects)}"
    )
    click.echo(f"Profile   : {profile_path}")
    click.echo(f"Templates : {templates.directory}")
    for name in (
        "resume_master",
        "cover_letter",
        "email_apply",
        "connection_note",
        "gig_profile",
    ):
        click.echo(f"  {'OK ' if templates.has(name) else 'MISSING '}{name}.md")
    click.echo(f"Documents : {settings.documents_dir}")


@cmd_content.command("preview")
@click.option(
    "--job-id", type=int, default=None, help="Preview for a job already in the DB."
)
@click.option("--title", default="Design Engineer", help="Fallback job title.")
@click.option("--company", default="Example Co", help="Fallback company.")
@click.option("--description", default="", help="Fallback job description text.")
@click.pass_context
def cmd_content_preview(ctx, job_id, title, company, description) -> None:
    """Render grounded documents for a job without sending anything."""
    from job_agent.content.candidate import load_candidate_profile
    from job_agent.content.generator import generate_documents, save_documents
    from job_agent.content.templates import load_template_set
    from job_agent.db.repository import JobRepository
    from job_agent.platforms.base import JobPosting

    settings = ctx.obj["settings"]
    posting = JobPosting(
        external_id="preview",
        platform=Platform.OTHER,
        title=title,
        company=company,
        description=description or "",
        employment_type="part_time",
        remote=True,
    )
    if job_id:
        session = get_session(settings)
        try:
            job = JobRepository(session).get_by_id(job_id)
            if not job:
                raise click.ClickException(f"Job {job_id} not found")
            posting = JobPosting(
                external_id=job.external_id,
                platform=job.platform,
                title=job.title,
                company=job.company,
                description=job.description or "",
                url=job.url or "",
                location=job.location or "",
                employment_type=job.employment_type or "",
                remote=bool(job.remote),
            )
        finally:
            session.close()

    candidate = load_candidate_profile(
        settings.resolve_path(settings.content.candidate_profile)
    )
    templates = load_template_set(settings.resolve_path(settings.content.templates_dir))
    docs = generate_documents(
        posting=posting,
        profile={},
        candidate=candidate,
        template_set=templates,
        ai_client=None,
        prompt_version=settings.content.prompt_version,
    )
    paths = save_documents(docs, settings.documents_dir, f"preview_{posting.company}")
    for key, path in paths.items():
        click.echo(f"{key}: {path}")
    if docs.warnings:
        click.echo("Grounding warnings: " + "; ".join(docs.warnings))


@cli.command("feeds")
@click.option("--query", "-q", multiple=True, help="Search keywords (repeatable).")
@click.option("--location", "-l", multiple=True, help="Locations (repeatable).")
@click.option("--limit", "-n", default=50, help="Max results.")
@click.option(
    "--profile",
    "-p",
    default="config/profiles/aditya.yaml",
    help="Profile whose part-time/remote filters should be applied.",
)
@click.pass_context
def cmd_feeds(ctx, query, location, limit, profile) -> None:
    """Fast discovery from structured public job feeds."""
    from job_agent.config import load_profile
    from job_agent.discovery.feeds import FeedHub, load_feed_specs
    from job_agent.matching.filters import build_filters

    settings = ctx.obj["settings"]
    specs = load_feed_specs(settings.resolve_path(settings.feeds.config_path))
    hub = FeedHub(
        specs,
        timeout=settings.feeds.timeout,
        max_workers=settings.feeds.max_workers,
    )
    profile_data = {}
    profile_path = Path(profile)
    if profile_path.exists():
        profile_data = load_profile(profile_path)
    queries = (
        list(query)
        or profile_data.get("search", {}).get("keywords")
        or [
            "frontend",
            "design engineer",
        ]
    )
    locations = list(location) or profile_data.get("search", {}).get("locations") or []
    results = hub.discover(
        list(queries), list(locations), limit=limit, filters=build_filters(profile_data)
    )
    click.echo(f"Found {len(results)} postings from {len(hub.results())} feeds")
    for j in results[:25]:
        click.echo(f"  [{j.platform.value}] {j.title} @ {j.company} — {j.url}")


@cli.group("gigs")
@click.pass_context
def cmd_gigs(ctx: click.Context) -> None:
    """AI-training / expert-network opportunities."""
    pass


@cmd_gigs.command("refresh")
@click.pass_context
def cmd_gigs_refresh(ctx: click.Context) -> None:
    """Fetch current gig listings and upsert them into the database."""
    from job_agent.gigs.service import GigService

    settings = ctx.obj["settings"]
    init_db(settings)
    session = get_session(settings)
    try:
        service = GigService(settings, session)
        counts = service.refresh()
        click.echo(f"Gigs: {counts}")
    finally:
        session.close()


@cmd_gigs.command("list")
@click.option("--limit", "-n", default=40, help="Max rows to show.")
@click.pass_context
def cmd_gigs_list(ctx: click.Context, limit: int) -> None:
    """List eligible gig opportunities from the local database."""
    from sqlalchemy import select

    from job_agent.db.models import GigOpportunity, GigStatus

    settings = ctx.obj["settings"]
    init_db(settings)
    session = get_session(settings)
    try:
        wanted = (
            GigStatus.ELIGIBLE,
            GigStatus.APPLIED,
            GigStatus.ASSESSMENT,
            GigStatus.ACTIVE,
        )
        rows = list(
            session.scalars(
                select(GigOpportunity)
                .where(GigOpportunity.status.in_(wanted))
                .order_by(GigOpportunity.pay_max.desc().nullslast())
                .limit(limit)
            ).all()
        )
        if not rows:
            click.echo("No eligible gigs yet. Run: job-agent gigs refresh")
            return
        # Alignerr publishes city-targeted variants of the same role; show one
        # representative row per (network, title) while keeping all DB rows.
        seen_titles: set[tuple[str, str]] = set()
        unique_rows = []
        for gig in rows:
            key = (gig.network, gig.title)
            if key in seen_titles:
                continue
            seen_titles.add(key)
            unique_rows.append(gig)
        click.echo(
            f"{len(unique_rows)} eligible gig(s) ({len(rows)} listing variants):"
        )
        for gig in unique_rows[:limit]:
            rate = (
                f"{gig.pay_min:g}-{gig.pay_max:g} {gig.currency}/{gig.pay_period}"
                if gig.pay_min
                else "rate n/a"
            )
            click.echo(
                f"  [{gig.network}/{gig.track}] {gig.title} — {rate} — {gig.url}"
            )
    finally:
        session.close()


# Spreadsheet commands live in their own module so they can be composed in tests.
try:
    from job_agent.cli.sheet import build_cli_group as _build_sheet_group

    cli.add_command(_build_sheet_group())
except ImportError as _exc:  # pragma: no cover - optional during partial installs
    import sys as _sys

    print(f"warning: spreadsheet CLI unavailable: {_exc}", file=_sys.stderr)


if __name__ == "__main__":
    cli()
