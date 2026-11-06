"""Discovery → Dedup → Match → Decide → Apply pipeline."""

from __future__ import annotations

import time
from contextlib import contextmanager

from job_agent.ai.client import AIClient, build_ai_client
from job_agent.ai.cover_letter import CoverLetterGenerator
from job_agent.ai.job_matcher import JobMatcher
from job_agent.ai.resume_tailor import ResumeTailor
from job_agent.browser.manager import BrowserManager
from job_agent.config import Settings, load_profile
from job_agent.db.models import (
    ApplicationStatus,
    Job,
    JobStatus,
    Platform,
)
from job_agent.db.repository import (
    ApplicationRepository,
    CredentialRepository,
    JobRepository,
    MatchResultRepository,
)
from job_agent.db.session import get_session
from job_agent.orchestrator.pipeline_steps import (
    apply_to_job,
    build_candidate_summary,
    decide_job,
    generate_cold_email_draft,
    get_matched_skills_for_job,
    match_job,
)
from job_agent.orchestrator.timing import StageTimer
from job_agent.platforms.base import JobPosting, PlatformDriver
from job_agent.platforms.linkedin.driver import LinkedInDriver
from job_agent.utils.crypto import decrypt
from job_agent.utils.logging import bind_contextvars, get_logger

log = get_logger(__name__)


def _load_content(settings: Settings) -> tuple[object | None, object | None]:
    """Load the human-editable candidate profile and Markdown templates."""
    if settings.content.mode != "grounded":
        return None, None
    try:
        from job_agent.content.candidate import load_candidate_profile
        from job_agent.content.templates import load_template_set

        candidate = load_candidate_profile(
            settings.resolve_path(settings.content.candidate_profile)
        )
        templates = load_template_set(
            settings.resolve_path(settings.content.templates_dir)
        )
        return candidate, templates
    except Exception as exc:
        log.warning("content_templates_unavailable", error=str(exc)[:200])
        return None, None


def _sheet_sync(settings: Settings):
    """Return a SheetSync instance, or None when sheets are disabled/unavailable."""
    if not settings.sheets.enabled:
        return None
    try:
        from job_agent.integrations.spreadsheet import SheetConfig, SheetSync

        return SheetSync(SheetConfig.from_settings(settings))
    except Exception as exc:
        log.warning("sheet_sync_unavailable", error=str(exc)[:200])
        return None


def _pull_sheet_decisions(settings: Settings, session) -> dict[str, int]:
    """Read the human approval column from the sheet before doing any work."""
    if not settings.sheets.pull_decisions:
        return {}
    sync = _sheet_sync(settings)
    if sync is None:
        return {}
    try:
        counts = sync.apply_decisions(session)
        log.info("sheet_decisions_applied", **counts)
        return counts
    except Exception as exc:
        log.warning("sheet_decision_pull_failed", error=str(exc)[:200])
        return {}


def _apply_gig_decisions(settings: Settings, session) -> dict[str, int]:
    """Apply the sheet's decision column to AI-training gig rows too."""
    sync = _sheet_sync(settings)
    if sync is None:
        return {}
    try:
        from sqlalchemy import select

        from job_agent.db.models import GigOpportunity, GigStatus

        decisions = sync.pull_decisions()
        mapping = {
            "approve": GigStatus.ELIGIBLE,
            "reject": GigStatus.REJECTED,
            "skip": GigStatus.NOT_ELIGIBLE,
        }
        counts = {"approved": 0, "rejected": 0, "skipped": 0}
        for key, decision in decisions.items():
            network, sep, external_id = str(key).partition(":")
            if not sep or decision not in mapping:
                continue
            gig = session.scalar(
                select(GigOpportunity).where(
                    GigOpportunity.network == network,
                    GigOpportunity.external_id == external_id,
                )
            )
            if gig is None:
                continue  # a normal job key, handled by SheetSync.apply_decisions
            gig.status = mapping[decision]
            counts[
                "approved"
                if decision == "approve"
                else "rejected"
                if decision == "reject"
                else "skipped"
            ] += 1
        session.commit()
        return counts
    except Exception as exc:
        log.warning("gig_decision_pull_failed", error=str(exc)[:200])
        return {}


def _push_sheet(settings: Settings, session) -> None:
    """Mirror current DB state into the human-editable spreadsheet."""
    sync = _sheet_sync(settings)
    if sync is None:
        return
    try:
        counts = sync.sync_from_session(session)
        log.info("sheet_pushed", **counts)
    except Exception as exc:
        log.warning("sheet_push_failed", error=str(exc)[:200])


def _discover_from_feeds(settings: Settings, profile: dict) -> list[JobPosting]:
    """Fast lane: structured public feeds, fetched concurrently."""
    if not settings.feeds.enabled:
        return []
    try:
        from job_agent.discovery.feeds import FeedHub, load_feed_specs
        from job_agent.matching.filters import build_filters

        specs = load_feed_specs(settings.resolve_path(settings.feeds.config_path))
        hub = FeedHub(
            specs,
            timeout=settings.feeds.timeout,
            max_workers=settings.feeds.max_workers,
        )
        search = profile.get("search", {})
        queries = search.get("keywords") or []
        locations = search.get("locations") or []
        postings = hub.discover(
            list(queries), list(locations), limit=200, filters=build_filters(profile)
        )
        log.info(
            "feed_discovery_complete", count=len(postings), feeds=len(hub.results())
        )
        return postings
    except Exception as exc:
        log.warning("feed_discovery_failed", error=str(exc)[:200])
        return []


def _store_postings(
    postings: list[JobPosting],
    *,
    settings: Settings,
    session,
    job_repo: JobRepository,
    profile: dict,
) -> list[Job]:
    """Bulk-dedup and store discovered postings in a single commit."""
    if not postings:
        return []
    keys = [(p.external_id, p.platform) for p in postings]
    existing = job_repo.filter_existing(keys)
    seen: set[tuple[str, str]] = set()
    created: list[Job] = []
    for posting in postings:
        platform_value = posting.platform.value
        key = (posting.external_id, platform_value)
        if key in existing or key in seen:
            continue
        seen.add(key)
        created.append(
            job_repo.create(
                external_id=posting.external_id,
                platform=posting.platform,
                title=posting.title,
                company=posting.company,
                location=posting.location,
                description=posting.description,
                url=posting.url,
                salary=posting.salary,
                easy_apply=posting.easy_apply,
                remote=posting.remote,
                employment_type=posting.employment_type or "",
                remote_policy=posting.remote_policy or "",
                timezone=posting.timezone or "",
                hours_per_week=posting.hours_per_week,
                currency=posting.currency or "",
                salary_period=posting.salary_period or "",
                source=posting.source or "",
                profile_name=profile.get("name", ""),
            )
        )
    session.commit()
    return created


def _refresh_gigs(settings: Settings, session) -> None:
    """Refresh the AI-training gig lane (separate workflow, no cover letters)."""
    if not settings.gigs.enabled:
        return
    try:
        from job_agent.gigs.service import GigService

        counts = GigService(settings, session).refresh()
        log.info("gigs_refreshed", **counts)
    except Exception as exc:
        log.warning("gig_refresh_failed", error=str(exc)[:200])


def _budget_ok(ai_client: AIClient, settings: Settings, used: dict[str, int]) -> bool:
    """Separate match / generation budgets plus a hard cost ceiling."""
    if used["match"] >= settings.matching.max_match_calls_per_run:
        return False
    if used["generation"] >= settings.matching.max_generation_calls_per_run:
        return False
    cost = getattr(ai_client, "estimated_cost_usd", 0.0)
    return cost < settings.matching.max_cost_usd_per_run


def _ego_enabled(platform_name: str, settings: Settings) -> bool:
    """True when this platform is driven through ego(lite) instead of Playwright."""
    plat_cfg = getattr(settings.platforms, platform_name, None)
    ego_cfg = getattr(settings, "ego", None)
    return bool(plat_cfg and ego_cfg and plat_cfg.use_ego and ego_cfg.enabled)


@contextmanager
def open_driver(platform_name: str, settings: Settings):
    """Yield a driver for the platform.

    ego(lite) is a browser that already exists on the person's own machine, so no
    BrowserManager is started for it.
    """
    if _ego_enabled(platform_name, settings):
        from job_agent.platforms.ego.driver import EgoDriver

        yield EgoDriver(settings, platform_name)
        return

    browser_opts = _resolve_browser_opts(platform_name, settings)
    with BrowserManager(settings, **browser_opts) as browser:
        yield get_platform_driver(platform_name, settings, browser)


def _resolve_browser_opts(platform_name: str, settings: Settings) -> dict:
    """Resolve per-platform browser options (use_camoufox, use_cdp)."""
    plat_cfg = getattr(settings.platforms, platform_name, None)
    opts: dict = {}
    if plat_cfg:
        if plat_cfg.use_cdp:
            opts["use_cdp"] = True
        if plat_cfg.use_camoufox is not None:
            opts["use_camoufox"] = plat_cfg.use_camoufox
    return opts


def get_platform_driver(
    platform_name: str, settings: Settings, browser_manager: BrowserManager
) -> PlatformDriver:
    """Factory for platform drivers."""
    from job_agent.platforms.indeed.driver import IndeedDriver
    from job_agent.platforms.glassdoor.driver import GlassdoorDriver
    from job_agent.platforms.ziprecruiter.driver import ZipRecruiterDriver
    from job_agent.platforms.dice.driver import DiceDriver
    from job_agent.platforms.wellfound.driver import WellfoundDriver

    drivers = {
        "linkedin": lambda: LinkedInDriver(settings, browser_manager),
        "indeed": lambda: IndeedDriver(settings, browser_manager),
        "glassdoor": lambda: GlassdoorDriver(settings, browser_manager),
        "ziprecruiter": lambda: ZipRecruiterDriver(settings, browser_manager),
        "dice": lambda: DiceDriver(settings, browser_manager),
        "wellfound": lambda: WellfoundDriver(settings, browser_manager),
    }
    factory = drivers.get(platform_name)
    if not factory:
        raise ValueError(f"Unsupported platform: {platform_name}")
    return factory()


def _login_driver(
    driver: PlatformDriver,
    platform_name: str,
    session,
    ai_client: AIClient,
    profile: dict,
) -> bool:
    """Login the driver using stored credentials. Returns False if no credentials found."""
    if getattr(driver, "requires_login", True) is False:
        if hasattr(driver, "set_ai_context"):
            driver.set_ai_context(ai_client, profile)
        return True

    cred = CredentialRepository(session).get(Platform(platform_name))
    if not cred:
        log.warning("no_credentials", platform=platform_name)
        return False
    driver.login(cred.username, decrypt(cred.encrypted_password))
    if hasattr(driver, "set_ai_context"):
        driver.set_ai_context(ai_client, profile)
    return True


def _discover_postings(
    driver: PlatformDriver,
    search: dict,
    job_repo: JobRepository,
) -> list[tuple[JobPosting, bool]]:
    """Discover job postings, batch-dedup, then apply employment filters.

    One SELECT replaces a per-posting ``exists()``. Filtering after discovery
    keeps every board driver unchanged while still enforcing part-time / remote
    / timezone requirements from the profile.
    """
    collected: list[JobPosting] = []
    consecutive_search_failures = 0

    for kw in search.get("keywords", []):
        if consecutive_search_failures >= 3:
            log.error("too_many_search_failures_stopping_discovery")
            break
        for loc in search.get("locations", [""]):
            try:
                postings = driver.search_jobs(
                    query=kw,
                    location=loc,
                    remote=search.get("remote_preference", "") == "remote_only",
                    experience_level=search.get("experience_level", ""),
                    limit=25,
                )
                consecutive_search_failures = 0
                collected.extend(postings)
            except Exception as e:
                consecutive_search_failures += 1
                log.warning(
                    "search_failed",
                    query=kw,
                    location=loc,
                    error=str(e)[:200],
                )

    try:
        from job_agent.matching.filters import apply_filters, build_filters

        filtered = apply_filters(
            collected,
            build_filters({"search": search, "filters": search.get("filters")}),
        )
        for posting, reason in filtered.rejected[:20]:
            log.debug("job_filtered_out", title=posting.title, reason=reason)
        collected = filtered.kept
    except Exception as exc:
        log.warning("filters_unavailable", error=str(exc)[:200])

    existing = job_repo.filter_existing(
        [(p.external_id, p.platform) for p in collected]
    )
    seen: set[tuple[str, str]] = set()
    results: list[tuple[JobPosting, bool]] = []
    for posting in collected:
        key = (posting.external_id, posting.platform.value)
        if key in existing or key in seen:
            continue
        seen.add(key)
        results.append((posting, not posting.description))
    return results


def _process_approved_queue(
    *,
    approved_jobs: list[Job],
    settings: Settings,
    session,
    app_repo: ApplicationRepository,
    ai_client: AIClient,
    resume_tailor: ResumeTailor,
    cover_letter_gen: CoverLetterGenerator,
    candidate_summary: str,
    profile: dict,
    stats: dict,
    candidate=None,
    template_set=None,
) -> None:
    """Process all APPROVED jobs grouped by platform, each with its own browser."""
    if not approved_jobs:
        return

    log.info("processing_approved_jobs", count=len(approved_jobs))

    by_platform: dict[str, list[Job]] = {}
    for job in approved_jobs:
        by_platform.setdefault(job.platform.value, []).append(job)

    for plat, jobs in by_platform.items():
        bind_contextvars(platform=plat)
        cred = CredentialRepository(session).get(Platform(plat))
        if not cred and not _ego_enabled(plat, settings):
            log.warning("no_credentials_for_approved")
            continue

        try:
            with open_driver(plat, settings) as driver:
                if cred:
                    driver.login(cred.username, decrypt(cred.encrypted_password))
                if hasattr(driver, "set_ai_context"):
                    driver.set_ai_context(ai_client, profile)

                for job in jobs:
                    bind_contextvars(job_id=job.id)
                    if settings.agent.dry_run:
                        # Dry-run is strictly read-only: no status changes, no
                        # Application rows, no commits, no document generation.
                        log.info(
                            "dry_run_approved_would_apply",
                            job_id=job.id,
                            title=job.title,
                        )
                        continue

                    try:
                        posting = JobPosting(
                            external_id=job.external_id,
                            platform=job.platform,
                            title=job.title,
                            company=job.company,
                            location=job.location,
                            description=job.description or "",
                            url=job.url,
                            easy_apply=True,
                            remote=job.remote,
                            salary=job.salary,
                            employment_type=job.employment_type or "",
                            remote_policy=job.remote_policy or "",
                            timezone=job.timezone or "",
                            hours_per_week=job.hours_per_week,
                            currency=job.currency or "",
                            salary_period=job.salary_period or "",
                            source=job.source or "",
                        )
                        matched_skills = get_matched_skills_for_job(job)
                        success = apply_to_job(
                            job=job,
                            posting=posting,
                            driver=driver,
                            resume_tailor=resume_tailor,
                            cover_letter_gen=cover_letter_gen,
                            app_repo=app_repo,
                            candidate_summary=candidate_summary,
                            matched_skills=matched_skills,
                            settings=settings,
                            profile=profile,
                            ai_client=ai_client,
                            candidate=candidate,
                            template_set=template_set,
                        )
                        if success:
                            stats["applied"] += 1
                            log.info(
                                "approved_job_applied",
                                job_id=job.id,
                                title=job.title,
                                company=job.company,
                            )
                            generate_cold_email_draft(
                                job=job,
                                ai_client=ai_client,
                                settings=settings,
                                profile=profile,
                                session=session,
                                candidate=candidate,
                            )
                        else:
                            log.warning(
                                "approved_job_apply_failed",
                                job_id=job.id,
                                title=job.title,
                                company=job.company,
                                platform=plat,
                            )
                    except Exception as e:
                        log.error(
                            "approved_job_error",
                            job_id=job.id,
                            title=job.title,
                            company=job.company,
                            platform=plat,
                            error=str(e),
                        )
                        job.status = JobStatus.APPLY_FAILED
                        app_repo.create_or_update(
                            job_id=job.id,
                            resume_path="",
                            cover_letter_path="",
                            status=ApplicationStatus.FAILED,
                            error_message=str(e)[:500],
                        )

                    session.commit()

                driver.close()
        except Exception as e:
            log.error("approved_platform_error", platform=plat, error=str(e))


def discover_jobs(
    settings: Settings,
    platform_name: str,
    query: str,
    location: str = "",
    limit: int = 25,
) -> list[Job]:
    """Discover and store jobs from a platform."""
    session = get_session(settings)
    job_repo = JobRepository(session)

    try:
        with open_driver(platform_name, settings) as driver:
            cred = CredentialRepository(session).get(Platform(platform_name))
            if not cred and not _ego_enabled(platform_name, settings):
                raise RuntimeError(
                    f"No credentials for {platform_name}. Run: job-agent add-credential {platform_name}"
                )

            if cred:
                driver.login(cred.username, decrypt(cred.encrypted_password))

            # Search
            postings = driver.search_jobs(query=query, location=location, limit=limit)

            # Store in DB (batch dedup, one commit)
            stored: list[Job] = []
            existing = job_repo.filter_existing(
                [(p.external_id, p.platform) for p in postings]
            )
            for posting in postings:
                key = (posting.external_id, posting.platform.value)
                if key in existing:
                    continue
                existing.add(key)
                duplicate_of = job_repo.find_cross_platform_duplicate(
                    posting.title, posting.company, posting.platform
                )
                job = job_repo.create(
                    external_id=posting.external_id,
                    platform=posting.platform,
                    title=posting.title,
                    company=posting.company,
                    location=posting.location,
                    description=posting.description,
                    url=posting.url,
                    salary=posting.salary,
                    easy_apply=posting.easy_apply,
                    remote=posting.remote,
                    employment_type=posting.employment_type or "",
                    remote_policy=posting.remote_policy or "",
                    timezone=posting.timezone or "",
                    hours_per_week=posting.hours_per_week,
                    currency=posting.currency or "",
                    salary_period=posting.salary_period or "",
                    source=posting.source or "",
                    duplicate_of_id=duplicate_of.id if duplicate_of else None,
                )
                if duplicate_of:
                    job.status = JobStatus.REJECTED
                    log.info(
                        "job_duplicate_skipped",
                        title=job.title,
                        company=job.company,
                        original_platform=duplicate_of.platform.value,
                    )
                else:
                    stored.append(job)
                    log.info("job_discovered", title=job.title, company=job.company)

            session.commit()
            driver.close()

        log.info("discovery_complete", total=len(postings), new=len(stored))
        return stored

    except Exception as e:
        session.rollback()
        log.error("discovery_failed", error=str(e))
        raise
    finally:
        session.close()


def _match_pending_jobs(
    *,
    session,
    job_repo: JobRepository,
    match_repo: MatchResultRepository,
    matcher: JobMatcher,
    ai_client: AIClient,
    settings: Settings,
    profile: dict,
    used: dict[str, int],
    deadline: float,
) -> dict[str, int]:
    """Match jobs left at DISCOVERED by a previous run's exhausted budget."""
    counts = {"matched": 0, "queued": 0, "skipped": 0}
    if getattr(ai_client, "provider", "") == "none":
        # No API key configured: leave jobs DISCOVERED so they can be matched
        # later instead of permanently marking them SKIPPED with a 0.0 score.
        log.info("pending_matches_deferred_no_ai_provider")
        return counts
    pending = job_repo.list_by_status(JobStatus.DISCOVERED, limit=100)
    for job in pending:
        if time.monotonic() > deadline or not _budget_ok(ai_client, settings, used):
            break
        posting = JobPosting(
            external_id=job.external_id,
            platform=job.platform,
            title=job.title,
            company=job.company,
            location=job.location,
            description=job.description or "",
            url=job.url,
            remote=job.remote,
            employment_type=job.employment_type or "",
            remote_policy=job.remote_policy or "",
            timezone=job.timezone or "",
        )
        try:
            score = match_job(
                job=job,
                posting=posting,
                matcher=matcher,
                match_repo=match_repo,
                profile=profile,
            )
            used["match"] += 1
            counts["matched"] += 1
            decision = decide_job(
                job,
                score,
                settings,
                require_approval=settings.agent.require_approval_for_all,
            )
            counts["queued" if decision == "queue" else "skipped"] += 1
        except Exception as exc:
            log.warning("pending_match_failed", job_id=job.id, error=str(exc)[:200])
    session.commit()
    return counts


def run_pipeline(
    settings: Settings,
    profile_path: str,
    platform_name: str | None = None,
    *,
    discover_only: bool = False,
) -> dict[str, int]:
    """Run the full discover → match → decide → apply pipeline.

    Fast lane (feeds) → human approvals (sheet) → board discovery → grounded
    matching → approval-gated submission → sheet mirror. All work is bounded by
    a wall-clock deadline and separate match/generation AI budgets.
    """
    profile = load_profile(profile_path)
    search = profile.get("search", {})
    stats = {"discovered": 0, "matched": 0, "applied": 0, "queued": 0, "skipped": 0}
    timer = StageTimer()
    deadline = time.monotonic() + max(1, settings.agent.max_run_minutes) * 60
    candidate, template_set = _load_content(settings)
    candidate_summary = build_candidate_summary(profile, candidate)
    used = {"match": 0, "generation": 0}

    platforms_to_run = []
    if platform_name:
        platforms_to_run = [platform_name]
    else:
        for p in [
            "linkedin",
            "indeed",
            "glassdoor",
            "ziprecruiter",
            "dice",
            "wellfound",
        ]:
            cfg = getattr(settings.platforms, p)
            if cfg.enabled:
                platforms_to_run.append(p)

    session = get_session(settings)
    job_repo = JobRepository(session)
    match_repo = MatchResultRepository(session)
    app_repo = ApplicationRepository(session)
    ai_client = build_ai_client(settings)
    matcher = JobMatcher(ai_client)
    resume_tailor = ResumeTailor(ai_client, settings)
    cover_letter_gen = CoverLetterGenerator(ai_client, settings)

    try:
        with timer.stage("approvals_and_gigs"):
            _pull_sheet_decisions(settings, session)
            _refresh_gigs(settings, session)
            _apply_gig_decisions(settings, session)

        with timer.stage("pending_matches"):
            pending = _match_pending_jobs(
                session=session,
                job_repo=job_repo,
                match_repo=match_repo,
                matcher=matcher,
                ai_client=ai_client,
                settings=settings,
                profile=profile,
                used=used,
                deadline=deadline,
            )
            stats["matched"] += pending["matched"]
            stats["queued"] += pending["queued"]
            stats["skipped"] += pending["skipped"]

        with timer.stage("feeds"):
            feed_postings = _discover_from_feeds(settings, profile)
            stored = _store_postings(
                feed_postings,
                settings=settings,
                session=session,
                job_repo=job_repo,
                profile=profile,
            )
            stats["discovered"] += len(stored)

        for plat in platforms_to_run:
            if time.monotonic() > deadline:
                log.warning("run_deadline_reached_before_platform", platform=plat)
                break
            with timer.stage(f"platform:{plat}"):
                with open_driver(plat, settings) as driver:
                    bind_contextvars(platform=plat)
                    if not _login_driver(driver, plat, session, ai_client, profile):
                        continue

                    postings_with_flags = _discover_postings(driver, search, job_repo)

                    for posting, needs_detail in postings_with_flags:
                        if time.monotonic() > deadline:
                            log.warning("run_deadline_reached", platform=plat)
                            break
                        if needs_detail and posting.url:
                            try:
                                detailed = driver.get_job_details(posting.url)
                                posting.description = detailed.description
                            except Exception as e:
                                log.warning(
                                    "detail_fetch_failed",
                                    url=posting.url,
                                    error=str(e),
                                )

                        job = job_repo.create(
                            external_id=posting.external_id,
                            platform=posting.platform,
                            title=posting.title,
                            company=posting.company,
                            location=posting.location,
                            description=posting.description,
                            url=posting.url,
                            salary=posting.salary,
                            easy_apply=posting.easy_apply,
                            remote=posting.remote,
                            employment_type=posting.employment_type or "",
                            remote_policy=posting.remote_policy or "",
                            timezone=posting.timezone or "",
                            hours_per_week=posting.hours_per_week,
                            currency=posting.currency or "",
                            salary_period=posting.salary_period or "",
                            source=posting.source or "",
                            profile_name=profile.get("name", ""),
                        )
                        stats["discovered"] += 1
                        bind_contextvars(job_id=job.id)

                        # A job that exceeds the budget stays DISCOVERED and is
                        # matched at the start of the next run — never orphaned.
                        if getattr(ai_client, "provider", "") == "none":
                            job.status = JobStatus.DISCOVERED
                            continue
                        if not _budget_ok(ai_client, settings, used):
                            log.info("ai_budget_deferring_match", job_id=job.id)
                            job.status = JobStatus.DISCOVERED
                            continue

                        used["match"] += 1
                        score = match_job(
                            job=job,
                            posting=posting,
                            matcher=matcher,
                            match_repo=match_repo,
                            profile=profile,
                        )
                        stats["matched"] += 1

                        decision = decide_job(
                            job,
                            score,
                            settings,
                            require_approval=(
                                settings.agent.require_approval_for_all or discover_only
                            ),
                        )

                        if decision == "auto_apply":
                            if settings.agent.dry_run:
                                log.info(
                                    "dry_run_would_apply",
                                    job_id=job.id,
                                    title=job.title,
                                )
                                job.status = JobStatus.QUEUED
                                stats["queued"] += 1
                            elif (
                                used["generation"] + 3
                                > settings.matching.max_generation_calls_per_run
                            ):
                                job.status = JobStatus.QUEUED
                                stats["queued"] += 1
                            else:
                                used["generation"] += 3
                                try:
                                    success = apply_to_job(
                                        job=job,
                                        posting=posting,
                                        driver=driver,
                                        resume_tailor=resume_tailor,
                                        cover_letter_gen=cover_letter_gen,
                                        app_repo=app_repo,
                                        candidate_summary=candidate_summary,
                                        matched_skills=get_matched_skills_for_job(job),
                                        settings=settings,
                                        profile=profile,
                                        ai_client=ai_client,
                                        candidate=candidate,
                                        template_set=template_set,
                                    )
                                    if success:
                                        stats["applied"] += 1
                                        generate_cold_email_draft(
                                            job=job,
                                            ai_client=ai_client,
                                            settings=settings,
                                            profile=profile,
                                            session=session,
                                            candidate=candidate,
                                        )
                                except Exception as e:
                                    log.error(
                                        "apply_error",
                                        job_id=job.id,
                                        title=job.title,
                                        platform=plat,
                                        error=str(e),
                                    )
                                    job.status = JobStatus.APPLY_FAILED
                                    app_repo.create_or_update(
                                        job_id=job.id,
                                        resume_path="",
                                        cover_letter_path="",
                                        status=ApplicationStatus.FAILED,
                                        error_message=str(e)[:500],
                                    )
                        elif decision == "queue":
                            stats["queued"] += 1
                        else:
                            stats["skipped"] += 1

                    # One commit per platform instead of one per job.
                    session.commit()
                    driver.close()

        # Process approved jobs from the review queue (skip in discover-only mode)
        if discover_only:
            log.info("discover_only_skipping_apply_queue")
        else:
            approved_jobs = job_repo.list_by_status(JobStatus.APPROVED)
            if approved_jobs:
                with timer.stage("approved_queue"):
                    _process_approved_queue(
                        approved_jobs=approved_jobs,
                        settings=settings,
                        session=session,
                        app_repo=app_repo,
                        ai_client=ai_client,
                        resume_tailor=resume_tailor,
                        cover_letter_gen=cover_letter_gen,
                        candidate_summary=candidate_summary,
                        profile=profile,
                        stats=stats,
                        candidate=candidate,
                        template_set=template_set,
                    )

        with timer.stage("sheet_push"):
            _push_sheet(settings, session)

        usage = ai_client.usage() if hasattr(ai_client, "usage") else {}
        stats["_meta"] = {
            "timings": timer.snapshot(),
            "ai_calls": usage.get("calls", ai_client.call_count),
            "estimated_cost_usd": usage.get("estimated_cost_usd", 0.0),
            "cache_hits": usage.get("cache_hits", 0),
            "total_ms": timer.total_ms(),
        }
        log.info(
            "pipeline_complete",
            **{k: v for k, v in stats.items() if k != "_meta"},
        )
        return stats

    except Exception as e:
        session.rollback()
        log.error("pipeline_failed", error=str(e))
        raise
    finally:
        session.close()


def apply_approved(settings: Settings, profile_path: str = "") -> dict[str, int]:
    """Apply to all APPROVED jobs from the review queue (skips discovery)."""
    stats = {"applied": 0, "failed": 0, "skipped": 0}

    session = get_session(settings)
    job_repo = JobRepository(session)
    app_repo = ApplicationRepository(session)
    ai_client = build_ai_client(settings)
    resume_tailor = ResumeTailor(ai_client, settings)
    cover_letter_gen = CoverLetterGenerator(ai_client, settings)
    profile = load_profile(profile_path) if profile_path else {}
    candidate, template_set = _load_content(settings)
    candidate_summary = build_candidate_summary(profile, candidate)

    try:
        _pull_sheet_decisions(settings, session)
        approved_jobs = job_repo.list_by_status(JobStatus.APPROVED)
        if not approved_jobs:
            log.info("no_approved_jobs")
            return stats

        log.info("applying_approved_jobs", count=len(approved_jobs))

        # Group by platform
        by_platform: dict[str, list] = {}
        for job in approved_jobs:
            by_platform.setdefault(job.platform.value, []).append(job)

        for plat, jobs in by_platform.items():
            bind_contextvars(platform=plat)
            if settings.agent.dry_run:
                for job in jobs:
                    log.info(
                        "dry_run_would_apply_approved",
                        job_id=job.id,
                        title=job.title,
                    )
                continue
            cred = CredentialRepository(session).get(Platform(plat))
            if not cred and not _ego_enabled(plat, settings):
                log.warning("no_credentials_for_approved", platform=plat)
                continue

            try:
                with open_driver(plat, settings) as driver:
                    if cred:
                        driver.login(cred.username, decrypt(cred.encrypted_password))

                    if profile and hasattr(driver, "set_ai_context"):
                        driver.set_ai_context(ai_client, profile)

                    for job in jobs:
                        bind_contextvars(job_id=job.id)
                        try:
                            posting = JobPosting(
                                external_id=job.external_id,
                                platform=job.platform,
                                title=job.title,
                                company=job.company,
                                location=job.location,
                                description=job.description or "",
                                url=job.url,
                                easy_apply=True,
                                remote=job.remote,
                                salary=job.salary,
                                employment_type=job.employment_type or "",
                                remote_policy=job.remote_policy or "",
                                timezone=job.timezone or "",
                                hours_per_week=job.hours_per_week,
                                currency=job.currency or "",
                                salary_period=job.salary_period or "",
                                source=job.source or "",
                            )
                            matched_skills = get_matched_skills_for_job(job)
                            success = apply_to_job(
                                job=job,
                                posting=posting,
                                driver=driver,
                                resume_tailor=resume_tailor,
                                cover_letter_gen=cover_letter_gen,
                                app_repo=app_repo,
                                candidate_summary=candidate_summary,
                                matched_skills=matched_skills,
                                settings=settings,
                                profile=profile,
                                ai_client=ai_client,
                                candidate=candidate,
                                template_set=template_set,
                            )
                            if success:
                                stats["applied"] += 1
                                log.info(
                                    "approved_job_applied",
                                    job_id=job.id,
                                    title=job.title,
                                    company=job.company,
                                )
                                generate_cold_email_draft(
                                    job=job,
                                    ai_client=ai_client,
                                    settings=settings,
                                    profile=profile,
                                    session=session,
                                    candidate=candidate,
                                )
                            else:
                                stats["failed"] += 1
                                log.warning(
                                    "approved_job_apply_failed",
                                    job_id=job.id,
                                    title=job.title,
                                    company=job.company,
                                    platform=plat,
                                )
                        except Exception as e:
                            session.rollback()
                            log.error(
                                "approved_job_error",
                                job_id=job.id,
                                title=job.title,
                                company=job.company,
                                platform=plat,
                                error=str(e),
                            )
                            job.status = JobStatus.APPLY_FAILED
                            stats["failed"] += 1

                        session.commit()

                    driver.close()
            except Exception as e:
                log.error("approved_platform_error", platform=plat, error=str(e))

        _push_sheet(settings, session)
        log.info("apply_approved_complete", **stats)
        return stats

    except Exception as e:
        session.rollback()
        log.error("apply_approved_failed", error=str(e))
        raise
    finally:
        session.close()
