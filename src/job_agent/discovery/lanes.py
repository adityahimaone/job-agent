"""Muse-primary / Ego-fallback discovery lanes.

Why this module exists
-----------------------
Aditya's job search has two discovery lanes that find jobs a feed never sees:

* **Muse (primary)** — the Muse agent searches the web / LinkedIn with its
  own browser, writes the jobs it finds to a JSON/CSV file, and job-agent
  imports that file through the fusion lane
  (:mod:`job_agent.discovery.muse`). The Mac has no residential IP — its
  egress is the same Tencent Singapore IP as the VPS — and Muse's cloud
  browser cannot be pointed at a custom proxy, so there is no way for
  job-agent code to call Muse's browser directly. The Muse lane is therefore
  driven from *outside* job-agent (a Muse cron run), and this module is the
  in-repo half of that contract: import the file, report the outcome.

* **Ego Lite (fallback)** — the ego(lite) browser on Aditya's Mac, driven
  over SSH/Tailscale (:mod:`job_agent.platforms.ego`). It is the fallback
  because it needs the Mac online, is guest-only (LinkedIn sign-in/apply
  hits reCAPTCHA / password checkpoints), and is easier to flag as automation
  than a Muse search.

Fallback rule (explicit, no silent behaviour)
---------------------------------------------
Ego only runs when the Muse import yields **zero new importable jobs**:
parsed rows that are all duplicates / cross-platform duplicates, an empty
file, or a missing file. If Muse imports even one new job, Ego does not run
— the primary lane did its job. The fallback is also a deliberate choice,
not a default: it only runs when ``discovery.fallback_enabled`` is true
*and* Ego is enabled in config (``ego.enabled``) *and* the caller asks for
it (``--with-ego-fallback`` on the CLI, or ``fallback_enabled`` here).

Safety invariants, unchanged from the fusion lane:

* Import never applies. ``import_muse_jobs`` always decides with
  ``require_approval=True``; the best outcome is QUEUED for a human.
* Ego fallback is discovery-only: it calls ``discover_jobs`` (search +
  store as DISCOVERED), never an applicator, and ``ego.require_human_submit``
  stays true.
* Nothing here signs in to LinkedIn, submits a form, or retries a
  checkpoint. An Ego search that hits a checkpoint returns no rows and the
  run ends with a report, not a retry.
* In ``--dry-run`` the fallback is *reported*, never executed: a dry run
  writes nothing, so Ego searches (which store jobs) are skipped.
"""

from __future__ import annotations

from typing import Any, Callable

from job_agent.config import Settings

# Default search terms for the Muse cron run and for the Ego fallback.
# They mirror config/profiles/aditya.yaml: Design Engineer / Frontend
# Engineer (React, Design Systems), remote-first, SEA-first. Hard profile
# exclusions (India / Pakistan / Bangladesh / Sri Lanka / Saudi Arabia)
# are enforced by the profile filters at match time, not by the queries.
DEFAULT_MUSE_QUERIES: list[str] = [
    "Frontend Engineer React",
    "Design Engineer",
    "React Developer",
    "Frontend Engineer Design Systems",
]

DEFAULT_MUSE_LOCATIONS: list[str] = [
    "Singapore",
    "Remote",
    "Jakarta, Indonesia",
    "Kuala Lumpur, Malaysia",
    "Bangkok, Thailand",
    "Vietnam",
    "Philippines",
]

DEFAULT_EGO_QUERIES: list[str] = [
    "Frontend Engineer",
    "Design Engineer",
    "React Developer",
]

DEFAULT_EGO_LOCATIONS: list[str] = [
    "Singapore",
    "Remote",
]

DEFAULT_EGO_PLATFORMS: list[str] = ["linkedin", "dealls"]

LANE_MUSE = "muse"
LANE_EGO = "ego"


def new_muse_jobs(muse_stats: dict[str, Any], *, dry_run: bool = False) -> int:
    """How many *new* jobs a Muse import yielded.

    A real import reports ``imported``; a dry run reports ``would_import``
    instead (``imported`` stays 0 because nothing was written). Either way,
    duplicates and cross-platform duplicates do not count — they are jobs
    job-agent already knew about.
    """
    if dry_run:
        return int(muse_stats.get("would_import") or 0)
    return int(muse_stats.get("imported") or 0)


def should_fallback_to_ego(
    muse_stats: dict[str, Any],
    *,
    dry_run: bool = False,
    fallback_requested: bool = True,
    fallback_enabled: bool = True,
    fallback_on_zero_new: bool = True,
    ego_enabled: bool = True,
) -> tuple[bool, str]:
    """Decide whether the Ego fallback should run after a Muse import.

    Returns ``(should_run, reason)``. The reason string is for the run
    report, so a skipped fallback is always explained, never silent.
    """
    if not fallback_requested:
        return False, "fallback not requested (pass --with-ego-fallback)"
    if not fallback_enabled:
        return False, "fallback disabled (discovery.fallback_enabled=false)"
    if not fallback_on_zero_new:
        return False, "fallback disabled (discovery.fallback_on_zero_new=false)"
    if not ego_enabled:
        return False, "fallback skipped: ego lane is disabled (ego.enabled=false)"
    if new_muse_jobs(muse_stats, dry_run=dry_run) > 0:
        return False, "muse lane yielded new jobs; ego fallback not needed"
    if dry_run:
        return True, "dry-run: muse yielded zero new jobs; ego fallback would run"
    return True, "muse yielded zero new jobs; falling back to ego"


def resolve_lane_order(settings: Settings) -> list[str]:
    """Ordered discovery lanes from config, primary first.

    Unknown lane names are dropped (never crash a run on a typo); the
    fallback is appended only when it is enabled and Ego is enabled.
    Defaults: ``["muse", "ego"]``.
    """
    cfg = settings.discovery
    order: list[str] = []
    for name in (cfg.primary, cfg.fallback):
        lane = str(name or "").strip().lower()
        if lane in (LANE_MUSE, LANE_EGO) and lane not in order:
            order.append(lane)
    if LANE_MUSE not in order:
        order.insert(0, LANE_MUSE)
    if LANE_EGO in order and not (cfg.fallback_enabled and settings.ego.enabled):
        order.remove(LANE_EGO)
    return order


def run_muse_first(
    *,
    muse_import: Callable[[], dict[str, Any]],
    ego_search: Callable[[], int],
    dry_run: bool = False,
    fallback_requested: bool = True,
    fallback_enabled: bool = True,
    fallback_on_zero_new: bool = True,
    ego_enabled: bool = True,
) -> dict[str, Any]:
    """Run the Muse lane, then the Ego fallback only if Muse found nothing new.

    ``muse_import`` is a zero-arg callable returning the ``import_muse_jobs``
    stats dict. ``ego_search`` is a zero-arg callable that runs the Ego
    discovery searches and returns how many jobs it stored (it is only
    *called* when the fallback triggers, and never in dry-run mode).

    Returns a report dict::

        {
            "lane": "muse" | "ego" | "muse+ego",
            "muse_stats": {...},
            "fallback_triggered": bool,
            "fallback_reason": str,
            "ego_jobs": int,        # 0 unless the fallback actually ran
            "ego_ran": bool,
        }
    """
    muse_stats = muse_import()
    trigger, reason = should_fallback_to_ego(
        muse_stats,
        dry_run=dry_run,
        fallback_requested=fallback_requested,
        fallback_enabled=fallback_enabled,
        fallback_on_zero_new=fallback_on_zero_new,
        ego_enabled=ego_enabled,
    )
    report: dict[str, Any] = {
        "lane": LANE_MUSE,
        "muse_stats": muse_stats,
        "fallback_triggered": trigger,
        "fallback_reason": reason,
        "ego_jobs": 0,
        "ego_ran": False,
    }
    if not trigger:
        return report
    if dry_run:
        # A dry run writes nothing; Ego searches store jobs, so report the
        # fallback instead of executing it.
        report["lane"] = f"{LANE_MUSE}+{LANE_EGO} (dry-run)"
        return report
    report["ego_jobs"] = int(ego_search())
    report["ego_ran"] = True
    report["lane"] = f"{LANE_MUSE}+{LANE_EGO}"
    return report
