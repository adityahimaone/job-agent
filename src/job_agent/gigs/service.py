"""Gig-service: refresh, eligibility, persistence and spreadsheet rows.

.. important::

   **Gigs are NOT job applications.** Onboarding for Alignerr / Outlier /
   DataAnnotation / Mercor is ``account + assessment``: create an account,
   pass a qualification/assessment, then get activated on a project. There is
   no resume, cover letter, easy-apply or ATS form involved. This service
   NEVER sends a CV or cover letter, and must never be wired into the
   application pipeline. It only discovers, filters and records gig rows for
   human review in the spreadsheet / dashboard.

Eligibility (applied by :meth:`GigService.eligible`):

* keep ``coding`` / ``general`` / ``design`` tracks; drop ``audio`` and any
  voice-only roles (audio keywords with no coding/design keywords);
* apply ``gigs.min_hourly_rate`` by comparing ``pay_min`` *or* ``pay_max``
  (only when pay is hourly-denominated; unknown pay is kept leniently);
* respect ``gigs.remote_only``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from job_agent.db.models import GigOpportunity, GigStatus
from job_agent.gigs.networks import GigListing, get_network
from job_agent.utils.logging import get_logger

log = get_logger(__name__)

_KEEP_TRACKS = {"coding", "general", "design"}
_AUDIO_WORDS = (
    "voice",
    "audio",
    "transcription",
    "transcribe",
    "speech",
    "linguist",
    "linguistic",
    "translator",
    "translation",
    "transcript",
    "pronunciation",
    "accent",
    "dubbing",
    "narration",
)
_RELEVANT_WORDS = (
    "developer",
    "software",
    "coding",
    "engineer",
    "frontend",
    "front-end",
    "react",
    "typescript",
    "javascript",
    "design system",
    "ui engineer",
    "ux engineer",
    "web developer",
    "code",
    "coder",
    "fullstack",
)
# Advisory / operations / language roles pay well but are not relevant to a
# hands-on design engineer. They are kept only when a technical word appears.
_NON_TECH_TITLE_WORDS = (
    "data lead",
    "program management",
    "account management",
    "client delivery",
    "growth operations",
    "talent acquisition",
    "recruiting",
    "sourcing",
    "client success",
    "partnerships",
    "account growth",
    "social media analyst",
    "operations manager",
    "transcription",
    "voice ai evaluator",
    "audio",
)

# Columns emitted by to_rows(). The gig sheet's canonical header is
# (gig_key, network, title, track, pay_min, pay_max, currency, pay_period,
#  employment_type, remote, url, eligibility, status, decision, notes);
# the extra ORM columns below are preserved by the sheet backend automatically.
# NOTE: `decision` and `notes` are HUMAN-OWNED sheet columns - they are emitted
# blank and the sheet backend never overwrites existing human values. `notes`
# here is NOT the GigOpportunity.notes DB field (which stays blank on discovery).
SHEET_COLUMNS = (
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
    "external_id",
    "description",
    "location",
    "discovered_at",
)


class GigService:
    """Orchestrates AI-training gig discovery and persistence."""

    def __init__(self, settings: Any, session: Any = None) -> None:
        self.settings = settings
        self.session = session
        self._listings: list[GigListing] = []
        self._eligible_keys: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------
    # Config access (defensive; settings may be a stub in tests)
    # ------------------------------------------------------------------

    def _cfg(self, name: str, default: Any) -> Any:
        gigs = getattr(self.settings, "gigs", None)
        return getattr(gigs, name, default) if gigs is not None else default

    def _cfg_float(self, name: str, default: float = 0.0) -> float:
        try:
            return float(self._cfg(name, default))
        except (TypeError, ValueError):
            return default

    def _cfg_bool(self, name: str, default: bool = True) -> bool:
        value = self._cfg(name, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def _cfg_networks(self) -> list[str]:
        raw = self._cfg("networks", None)
        if isinstance(raw, (list, tuple, set)):
            return [str(n) for n in raw if n]
        return ["alignerr"]

    def _cfg_timeout(self) -> float:
        feeds = getattr(self.settings, "feeds", None)
        try:
            return float(getattr(feeds, "timeout", 12) or 12)
        except (TypeError, ValueError):
            return 12.0

    # ------------------------------------------------------------------
    # Eligibility
    # ------------------------------------------------------------------

    def _is_eligible(self, gig: GigListing) -> bool:
        track = (getattr(gig, "track", "") or "general").strip().lower()
        if track not in _KEEP_TRACKS:
            return False

        title = (getattr(gig, "title", "") or "").lower()
        blob = f"{title} {(getattr(gig, 'description', '') or '').lower()}"
        # Drop audio/voice-only roles even when a source labelled them "general".
        if any(w in blob for w in _AUDIO_WORDS) and not any(
            w in blob for w in _RELEVANT_WORDS
        ):
            return False
        # Drop advisory/ops/language roles that merely mention "AI" or "data".
        if any(w in title for w in _NON_TECH_TITLE_WORDS) and not any(
            w in title for w in _RELEVANT_WORDS
        ):
            return False

        min_rate = self._cfg_float("min_hourly_rate", 0.0)
        if min_rate > 0:
            period = (getattr(gig, "pay_period", "") or "hour").strip().lower()
            if period in ("", "hour", "hourly", "hr", "h"):
                rates = [
                    float(r)
                    for r in (
                        getattr(gig, "pay_min", None),
                        getattr(gig, "pay_max", None),
                    )
                    if r is not None
                ]
                if rates and max(rates) < min_rate:
                    return False

        if self._cfg_bool("remote_only", True) and not getattr(gig, "remote", True):
            return False

        return True

    def eligible(self, listings: list[GigListing] | None = None) -> list[GigListing]:
        """Return eligible gigs.

        With no argument, filters the listings from the last :meth:`refresh`.
        An explicit ``listings`` argument (handy for tests / spreadsheet push)
        is also accepted; it is a backwards-compatible convenience, not a
        change to the wired ``eligible()`` call.
        """
        source = listings if listings is not None else self._listings
        return [g for g in (source or []) if self._is_eligible(g)]

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    @staticmethod
    def _dedupe(listings: list[GigListing]) -> list[GigListing]:
        seen: set[tuple[str, str]] = set()
        out: list[GigListing] = []
        for g in listings:
            key = (getattr(g, "network", "") or "", getattr(g, "external_id", "") or "")
            if key in seen:
                continue
            seen.add(key)
            out.append(g)
        return out

    def refresh(self, *, networks: list[str] | None = None) -> dict[str, int]:
        """Fetch listings from configured networks and (optionally) persist.

        Returns ``{"discovered": n, "eligible": n, "skipped": n, "errors": n}``.
        Works with ``session=None``: counts are computed and listings are stored
        in memory (for :meth:`eligible` / :meth:`to_rows`) but no DB writes
        happen. A failing network increments ``errors`` and never aborts.
        """
        names = [str(n) for n in networks] if networks else self._cfg_networks()
        timeout = self._cfg_timeout()
        counters = {"discovered": 0, "eligible": 0, "skipped": 0, "errors": 0}

        collected: list[GigListing] = []
        for name in names:
            try:
                network = get_network(name)
                collected.extend(network.fetch(timeout=timeout) or [])
            except Exception as exc:
                counters["errors"] += 1
                log.warning("gig_network_error", network=name, error=str(exc)[:200])

        collected = self._dedupe(collected)
        self._listings = collected
        eligible = self.eligible(collected)
        self._eligible_keys = {
            (getattr(g, "network", "") or "", getattr(g, "external_id", "") or "")
            for g in eligible
        }

        counters["discovered"] = len(collected)
        counters["eligible"] = len(eligible)
        counters["skipped"] = len(collected) - len(eligible)

        if self.session is not None:
            try:
                self.upsert(collected, self.session)
            except Exception as exc:
                counters["errors"] += 1
                log.warning("gig_upsert_failed", error=str(exc)[:200])

        return counters

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def upsert(self, listings: list[GigListing], session: Any) -> int:
        """Idempotently upsert into ``GigOpportunity`` by (network, external_id).

        Uses ``select(GigOpportunity).where(...)`` and commits exactly once.
        Newly discovered rows are marked ``ELIGIBLE`` when they passed
        eligibility during :meth:`refresh`, otherwise ``DISCOVERED``. Existing
        rows have their data refreshed but keep their status (so human progress
        such as APPLIED / ACTIVE is not overwritten); a still-DISCOVERED row is
        upgraded to ELIGIBLE when appropriate.
        """
        if session is None:
            return 0
        count = 0
        try:
            for g in listings or []:
                network = getattr(g, "network", "") or ""
                eid = getattr(g, "external_id", "") or ""
                existing = (
                    session.execute(
                        select(GigOpportunity).where(
                            GigOpportunity.network == network,
                            GigOpportunity.external_id == eid,
                        )
                    )
                    .scalars()
                    .first()
                )

                is_eligible = (network, eid) in self._eligible_keys
                if existing is None:
                    session.add(
                        GigOpportunity(
                            external_id=eid,
                            network=network,
                            title=getattr(g, "title", "") or "",
                            track=getattr(g, "track", "") or "",
                            description=getattr(g, "description", "") or "",
                            url=getattr(g, "url", "") or "",
                            location=getattr(g, "location", "") or "",
                            remote=bool(getattr(g, "remote", True)),
                            pay_min=getattr(g, "pay_min", None),
                            pay_max=getattr(g, "pay_max", None),
                            currency=getattr(g, "currency", "") or "USD",
                            pay_period=getattr(g, "pay_period", "") or "hour",
                            employment_type=getattr(g, "employment_type", "")
                            or "part_time",
                            eligibility=getattr(g, "eligibility", "") or "",
                            status=GigStatus.ELIGIBLE
                            if is_eligible
                            else GigStatus.DISCOVERED,
                        )
                    )
                else:
                    existing.title = getattr(g, "title", "") or ""
                    existing.track = getattr(g, "track", "") or ""
                    existing.description = getattr(g, "description", "") or ""
                    existing.url = getattr(g, "url", "") or ""
                    existing.location = getattr(g, "location", "") or ""
                    existing.remote = bool(getattr(g, "remote", True))
                    existing.pay_min = getattr(g, "pay_min", None)
                    existing.pay_max = getattr(g, "pay_max", None)
                    existing.currency = getattr(g, "currency", "") or "USD"
                    existing.pay_period = getattr(g, "pay_period", "") or "hour"
                    existing.employment_type = (
                        getattr(g, "employment_type", "") or "part_time"
                    )
                    existing.eligibility = getattr(g, "eligibility", "") or ""
                    # Keep human progress (APPLIED / ASSESSMENT / ACTIVE), but
                    # re-evaluate eligibility when the filters change so stale
                    # rows drop out of the "eligible" list.
                    if existing.status in (GigStatus.ELIGIBLE, GigStatus.DISCOVERED):
                        existing.status = (
                            GigStatus.ELIGIBLE if is_eligible else GigStatus.DISCOVERED
                        )
                count += 1
            session.commit()
            return count
        except Exception:
            try:
                session.rollback()
            except Exception:
                pass
            raise

    # ------------------------------------------------------------------
    # Spreadsheet rows
    # ------------------------------------------------------------------

    def to_rows(self, listings: list[GigListing]) -> list[dict]:
        """Return sheet-ready dicts whose keys match the gig sheet.

        ``gig_key`` = ``"<network>:<external_id>"`` is the sheet's canonical
        upsert identity (the sheet backend can also derive it from
        ``network`` + ``external_id``). ``status`` is ``"eligible"`` /
        ``"discovered"`` from the last :meth:`refresh` pass. ``decision`` and
        ``notes`` are HUMAN-OWNED columns: emitted blank and never overwritten
        by the sheet backend. Extra ORM columns (``external_id``,
        ``description``, ``location``, ``discovered_at``) are kept for lossless
        round-tripping - the sheet backend adds them to the header.
        """
        source = listings if listings else self._listings
        now = datetime.now(timezone.utc).isoformat()
        rows: list[dict] = []
        for g in source or []:
            network = getattr(g, "network", "") or ""
            eid = getattr(g, "external_id", "") or ""
            key = (network, eid)
            rows.append(
                {
                    "gig_key": f"{network}:{eid}",
                    "network": network,
                    "title": getattr(g, "title", "") or "",
                    "track": getattr(g, "track", "") or "",
                    "description": getattr(g, "description", "") or "",
                    "url": getattr(g, "url", "") or "",
                    "location": getattr(g, "location", "") or "",
                    "remote": bool(getattr(g, "remote", True)),
                    "pay_min": getattr(g, "pay_min", None),
                    "pay_max": getattr(g, "pay_max", None),
                    "currency": getattr(g, "currency", "") or "USD",
                    "pay_period": getattr(g, "pay_period", "") or "hour",
                    "employment_type": getattr(g, "employment_type", "") or "part_time",
                    "eligibility": getattr(g, "eligibility", "") or "",
                    "status": "eligible"
                    if key in self._eligible_keys
                    else "discovered",
                    "decision": "",  # human-owned; preserved by the sheet backend
                    "notes": "",  # human-owned; preserved by the sheet backend
                    "external_id": eid,
                    "discovered_at": now,
                }
            )
        return rows
