"""Deterministic employment / remote / timezone / pay filters.

This module is the *policy layer* shared by the fast structured-feed lane
(:mod:`job_agent.discovery.feeds`) and any other discovery source that needs
to narrow a list of :class:`~job_agent.platforms.base.JobPosting` objects
before matching/scoring. It has **no** network, database or AI dependencies so
it is safe to use in unit tests and inside worker threads.

Normalised filter keys (produced by :func:`build_filters`, consumed by
:func:`apply_filters` and :func:`score_employment_fit`)::

    employment_types                list[str]  e.g. ["part_time", "contract"]
    require_known_employment_type   bool       default False
    remote_only                     bool
    allowed_remote_policies         list[str]  e.g. ["remote", "hybrid"]
    timezone_alignment              str        raw, e.g. "GMT+7 ±4"
    timezone_offset                 float|None parsed base UTC offset (hours)
    timezone_delta                  float      allowed +/- delta (hours)
    min_hourly_rate                 float|None
    min_annual_salary               float|None
    max_hours_per_week              float|None
    exclude_keywords                list[str]  (lowercased)
    exclude_companies               list[str]  (lowercased)
    include_keywords                list[str]  (lowercased)
    max_age_days                    int|None

Rejection rules (applied in a fixed order; the *first* failing rule wins and
its reason string is returned alongside the rejected posting):

* ``include_keywords``   - at least one keyword must appear in the searchable
  blob, else reject.
* ``exclude_keywords``    - any keyword present in the blob rejects.
* ``exclude_companies``   - case-insensitive substring match on company rejects.
* ``employment_types``    - when non-empty, a *known* employment_type must be in
  the list. Unknown ("" or None) is kept by default ("treat as unknown") and is
  only rejected when ``require_known_employment_type`` is true.
* ``allowed_remote_policies`` - when non-empty, a known ``remote_policy`` not
  in the list rejects (an explicit ``remote=True`` flag overrides this).
* ``remote_only``         - accept if ``remote is True`` OR ``remote_policy``
  is in ``allowed_remote_policies`` OR "remote" appears in the title /
  description; otherwise reject.
* ``timezone_alignment``  - when a base offset is parsed and the posting has a
  parseable ``timezone``, reject postings outside ``offset ± delta``. Unknown
  posting timezones are kept (never rejected).
* ``min_hourly_rate`` / ``min_annual_salary`` - reject only when a pay value can
  be derived; unknown pay is kept (avoids false negatives).
* ``max_hours_per_week``  - reject when ``hours_per_week`` is known and exceeds
  the cap.
* ``max_age_days``        - reject when a parseable publication date (from
  ``extra``) is older than the cap; missing dates are kept.

All rules are defensive: missing attributes, ``None`` and malformed values
never raise; a posting that cannot be evaluated for a rule is kept rather than
dropped. ``filters=None`` (or ``{}``) keeps everything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from job_agent.platforms.base import JobPosting
from job_agent.utils.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class FilterResult:
    kept: list[JobPosting] = field(default_factory=list)
    rejected: list[tuple[JobPosting, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Coercion helpers (never raise)
# ---------------------------------------------------------------------------


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "y", "on"):
            return True
        if v in ("0", "false", "no", "n", "off", ""):
            return False
    return default


def _as_list(value: Any) -> list[str]:
    """Coerce scalars / lists / comma-separated strings into a clean list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value]
    elif isinstance(value, str):
        items = re.split(r"[,\|;]", value)
    else:
        items = [str(value)]
    out: list[str] = []
    for item in items:
        item = item.strip()
        if item:
            out.append(item)
    return out


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    f = _as_float(value)
    return int(f) if f is not None else None


_ET_ALIASES = {
    "part-time": "part_time",
    "part time": "part_time",
    "parttime": "part_time",
    "part_time": "part_time",
    "full-time": "full_time",
    "full time": "full_time",
    "fulltime": "full_time",
    "full_time": "full_time",
    "contract": "contract",
    "contractor": "contract",
    "freelance": "contract",
    "intern": "internship",
    "internship": "internship",
    "temporary": "contract",
    "temp": "contract",
}


def _norm_et(value: Any) -> str:
    v = str(value or "").strip().lower()
    return _ET_ALIASES.get(v, v)


# ---------------------------------------------------------------------------
# Timezone helpers
# ---------------------------------------------------------------------------

_NAMED_TZ = {
    "utc": 0.0,
    "gmt": 0.0,
    "wib": 7.0,  # Western Indonesian Time (Jakarta)
    "wita": 8.0,
    "wit": 9.0,
    "pst": -8.0,
    "pdt": -7.0,
    "mst": -7.0,
    "mdt": -6.0,
    "cst": -6.0,
    "cdt": -5.0,
    "est": -5.0,
    "edt": -4.0,
    "bst": 1.0,
    "cet": 1.0,
    "cest": 2.0,
    "ist": 5.5,
    "jst": 9.0,
    "sgt": 8.0,
    "aest": 10.0,
}


def _parse_tz_offset(value: Any) -> float | None:
    """Return a UTC offset in hours from strings like 'GMT+7', 'UTC-5', 'WIB'."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    m = re.search(r"(?:GMT|UTC)\s*([+-]?)\s*(\d+(?:\.\d+)?)", s, re.IGNORECASE)
    if m:
        return float(m.group(2)) * (-1.0 if m.group(1) == "-" else 1.0)
    low = s.lower()
    for key, off in _NAMED_TZ.items():
        if re.search(rf"\b{re.escape(key)}\b", low):
            return off
    return None


def _parse_alignment(raw: Any) -> tuple[float | None, float]:
    """Parse 'GMT+7 ±4' / 'UTC-5 +/-3' into (offset, delta)."""
    if not raw:
        return (None, 0.0)
    s = str(raw)
    m = re.search(r"(?:GMT|UTC)\s*([+-])\s*(\d+(?:\.\d+)?)", s, re.IGNORECASE)
    offset = float(m.group(2)) * (1.0 if m.group(1) == "+" else -1.0) if m else None
    dm = (
        re.search(r"±\s*(\d+(?:\.\d+)?)", s)
        or re.search(r"\+/-\s*(\d+(?:\.\d+)?)", s)
        or re.search(r"plus\s*/?\s*minus\s*(\d+(?:\.\d+)?)", s, re.IGNORECASE)
    )
    if dm:
        delta = float(dm.group(1))
    elif offset is not None:
        delta = 4.0  # lenient default when only the base offset is given
    else:
        delta = 0.0
    return (offset, delta)


# ---------------------------------------------------------------------------
# Pay / date helpers
# ---------------------------------------------------------------------------

_PERIOD_TO_HOUR = {"hour": 1.0, "hourly": 1.0, "hr": 1.0, "h": 1.0}
_PERIOD_TO_ANNUAL = {
    "hour": 2080.0,
    "hourly": 2080.0,
    "hr": 2080.0,
    "h": 2080.0,
    "day": 260.0,
    "daily": 260.0,
    "week": 52.0,
    "weekly": 52.0,
    "month": 12.0,
    "monthly": 12.0,
    "year": 1.0,
    "annual": 1.0,
    "annually": 1.0,
    "yr": 1.0,
}


def _first_number(text: str) -> float | None:
    if not text:
        return None
    cleaned = text.replace(",", "")
    m = re.search(r"\d+(?:\.\d+)?", cleaned)
    return float(m.group(0)) if m else None


def _pay_from_posting(posting: JobPosting) -> tuple[float | None, str]:
    """Best-effort (amount, normalised period) for a posting.

    Looks at structured ``extra`` pay fields first, then the free-text
    ``salary`` string. Returns (None, "") when nothing is parseable.
    """
    extra = getattr(posting, "extra", None) or {}
    if isinstance(extra, dict):
        for key in ("pay_min", "salary_min", "min_salary", "hourly_rate"):
            val = _as_float(extra.get(key))
            if val is not None:
                period = str(
                    extra.get("pay_period") or extra.get("salary_period") or ""
                )
                return (val, period.strip().lower())
    salary = getattr(posting, "salary", None)
    if salary:
        amount = _first_number(str(salary))
        if amount is not None:
            period = str(getattr(posting, "salary_period", "") or "").strip().lower()
            if not period:
                low = str(salary).lower()
                if any(k in low for k in ("/hr", "per hour", "hourly", "/hour")):
                    period = "hour"
                elif any(
                    k in low
                    for k in ("/year", "per year", "annual", "annum", "/yr", "yearly")
                ):
                    period = "year"
                elif "month" in low:
                    period = "month"
                elif "week" in low:
                    period = "week"
                elif "day" in low or "daily" in low:
                    period = "day"
            return (amount, period)
    return (None, "")


def _hourly_rate(posting: JobPosting) -> float | None:
    amount, period = _pay_from_posting(posting)
    if amount is None:
        return None
    if not period:
        return None  # unknown period -> cannot convert reliably
    factor = _PERIOD_TO_HOUR.get(period)
    if factor is not None:
        return amount * factor
    # e.g. annual -> hourly
    annual = _PERIOD_TO_ANNUAL.get(period)
    if annual:
        return (amount * annual) / 2080.0
    return None


def _annual_salary(posting: JobPosting) -> float | None:
    amount, period = _pay_from_posting(posting)
    if amount is None:
        return None
    if not period:
        return None
    annual = _PERIOD_TO_ANNUAL.get(period)
    return amount * annual if annual else None


def _parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = float(value)
        if ts > 1e11:  # milliseconds
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(value, str):
        s = value.strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d %b %Y"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _posting_date(posting: JobPosting) -> datetime | None:
    extra = getattr(posting, "extra", None) or {}
    if isinstance(extra, dict):
        for key in (
            "publication_date",
            "published_at",
            "created_at",
            "date",
            "discovered_at",
        ):
            dt = _parse_date(extra.get(key))
            if dt is not None:
                return dt
    return None


# ---------------------------------------------------------------------------
# build_filters
# ---------------------------------------------------------------------------

_FILTER_KEYS = (
    "employment_types",
    "employment_type",
    "require_known_employment_type",
    "remote_only",
    "allowed_remote_policies",
    "timezone_alignment",
    "min_hourly_rate",
    "min_annual_salary",
    "max_hours_per_week",
    "exclude_keywords",
    "exclude_companies",
    "include_keywords",
    "max_age_days",
)


def _pick_filter_source(profile: dict) -> dict:
    """Find the raw filter dict from a profile, in priority order."""
    if not isinstance(profile, dict):
        return {}
    search = profile.get("search")
    if isinstance(search, dict) and isinstance(search.get("filters"), dict):
        return search["filters"]
    if isinstance(profile.get("filters"), dict):
        return profile["filters"]
    # Fallback: filter keys supplied directly at the top level.
    if any(k in profile for k in _FILTER_KEYS):
        return {k: profile[k] for k in _FILTER_KEYS if k in profile}
    return {}


def build_filters(profile: dict) -> dict:
    """Normalise a job-search profile into a flat filter dict.

    Accepts ``{"search": {"filters": {...}}}``, ``{"filters": {...}}`` or a
    flat dict of filter keys. All values are coerced defensively.
    """
    raw = _pick_filter_source(profile or {})

    # employment_types may also arrive as a singular ``employment_type``.
    et_raw = raw.get("employment_types", raw.get("employment_type"))
    employment_types = [_norm_et(v) for v in _as_list(et_raw) if _norm_et(v)]

    offset, delta = _parse_alignment(raw.get("timezone_alignment"))

    return {
        "employment_types": employment_types,
        "require_known_employment_type": _as_bool(
            raw.get("require_known_employment_type"), False
        ),
        "remote_only": _as_bool(raw.get("remote_only"), False),
        "allowed_remote_policies": [
            v.strip().lower()
            for v in _as_list(raw.get("allowed_remote_policies"))
            if v.strip()
        ],
        "timezone_alignment": str(raw.get("timezone_alignment") or ""),
        "timezone_offset": offset,
        "timezone_delta": delta,
        "min_hourly_rate": _as_float(raw.get("min_hourly_rate")),
        "min_annual_salary": _as_float(raw.get("min_annual_salary")),
        "max_hours_per_week": _as_float(raw.get("max_hours_per_week")),
        "exclude_keywords": [v.lower() for v in _as_list(raw.get("exclude_keywords"))],
        "exclude_companies": [
            v.lower() for v in _as_list(raw.get("exclude_companies"))
        ],
        "include_keywords": [v.lower() for v in _as_list(raw.get("include_keywords"))],
        "max_age_days": _as_int(raw.get("max_age_days")),
    }


# ---------------------------------------------------------------------------
# apply_filters
# ---------------------------------------------------------------------------


def _blob(posting: JobPosting) -> str:
    parts = [
        getattr(posting, "title", "") or "",
        getattr(posting, "company", "") or "",
        getattr(posting, "description", "") or "",
        getattr(posting, "requirements", "") or "",
        getattr(posting, "location", "") or "",
        getattr(posting, "salary", "") or "",
    ]
    return " ".join(str(p) for p in parts).lower()


def _remote_ok(posting: JobPosting, allowed: list[str], text: str) -> bool:
    if getattr(posting, "remote", False):
        return True
    policy = str(getattr(posting, "remote_policy", "") or "").strip().lower()
    if policy and policy in allowed:
        return True
    return "remote" in text


def _reject_reason(posting: JobPosting, f: dict) -> str | None:
    """Return the first failing rule's reason, or None when the posting passes."""
    text = _blob(posting)
    company = str(getattr(posting, "company", "") or "").lower()
    et = _norm_et(getattr(posting, "employment_type", ""))

    include = f.get("include_keywords") or []
    if include and not any(k in text for k in include):
        return f"include_keywords: none of {include} found"

    for kw in f.get("exclude_keywords") or []:
        if kw in text:
            return f"exclude_keywords: matched '{kw}'"

    for c in f.get("exclude_companies") or []:
        if c and c in company:
            return f"exclude_companies: matched '{c}'"

    types = f.get("employment_types") or []
    require_known = bool(f.get("require_known_employment_type"))
    if require_known and not et:
        return "employment_type unknown (require_known_employment_type=True)"
    if types:
        if et and et not in types:
            return f"employment_type={et} not in {types}"
        if not et and require_known:
            return "employment_type unknown (require_known_employment_type=True)"

    allowed = f.get("allowed_remote_policies") or []
    policy = str(getattr(posting, "remote_policy", "") or "").strip().lower()
    if (
        allowed
        and policy
        and policy not in allowed
        and not getattr(posting, "remote", False)
    ):
        return f"remote_policy={policy} not in {allowed}"

    if f.get("remote_only"):
        if not _remote_ok(posting, allowed, text):
            return (
                "remote_only: not remote "
                f"(remote={getattr(posting, 'remote', False)}, remote_policy={policy!r})"
            )

    base = f.get("timezone_offset")
    if isinstance(base, (int, float)):
        p_off = _parse_tz_offset(getattr(posting, "timezone", ""))
        if p_off is not None:
            delta = float(f.get("timezone_delta") or 0.0)
            diff = abs(float(p_off) - float(base))
            diff = min(diff, 24.0 - diff)  # wrap around the date line
            if diff > delta + 1e-6:
                return (
                    f"timezone={getattr(posting, 'timezone', '')!r} outside "
                    f"{f.get('timezone_alignment') or ('GMT%+g' % float(base))} "
                    f"±{delta:g}"
                )

    min_hourly = f.get("min_hourly_rate")
    if isinstance(min_hourly, (int, float)) and min_hourly > 0:
        rate = _hourly_rate(posting)
        if rate is not None and rate < float(min_hourly):
            return f"hourly_rate={rate:.2f} < min_hourly_rate={float(min_hourly):.2f}"

    min_annual = f.get("min_annual_salary")
    if isinstance(min_annual, (int, float)) and min_annual > 0:
        annual = _annual_salary(posting)
        if annual is not None and annual < float(min_annual):
            return f"annual_salary={annual:.0f} < min_annual_salary={float(min_annual):.0f}"

    max_hours = f.get("max_hours_per_week")
    if isinstance(max_hours, (int, float)) and max_hours > 0:
        hours = _as_float(getattr(posting, "hours_per_week", None))
        if hours is not None and hours > float(max_hours):
            return f"hours_per_week={hours:g} > max_hours_per_week={float(max_hours):g}"

    max_age = f.get("max_age_days")
    if isinstance(max_age, int) and max_age > 0:
        dt = _posting_date(posting)
        if dt is not None:
            age = (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
            if age > max_age:
                return f"age={age:.0f}d > max_age_days={max_age}"

    return None


def _ensure_filters(filters: dict | None) -> dict:
    """Accept a normalised filter dict, a raw profile filter block, or None.

    Normalised dicts (from :func:`build_filters`) always carry a
    ``timezone_offset`` key. A raw block (e.g. ``{"employment_type":
    "part-time"}``) is normalised on the fly so callers can pass either form.
    """
    if not isinstance(filters, dict):
        return {}
    if "timezone_offset" in filters:
        return filters
    if "search" in filters or "filters" in filters:
        return build_filters(filters)
    if any(k in filters for k in ("employment_type", "timezone_alignment")):
        return build_filters({"filters": filters})
    return filters


def apply_filters(postings: list[JobPosting], filters: dict | None) -> FilterResult:
    """Apply normalised ``filters`` to ``postings`` deterministically.

    Never raises: ``filters=None`` keeps everything, and any per-posting error
    results in the posting being *kept* (fail-open) with a debug log. A raw
    (un-normalised) filter block is normalised automatically.
    """
    result = FilterResult()
    f = _ensure_filters(filters)
    for posting in postings or []:
        try:
            reason = _reject_reason(posting, f)
        except Exception as exc:  # defensive: never drop a posting on an error
            log.debug("filter_rule_error", error=str(exc)[:160])
            reason = None
        if reason:
            result.rejected.append((posting, reason))
        else:
            result.kept.append(posting)
    return result


# ---------------------------------------------------------------------------
# score_employment_fit
# ---------------------------------------------------------------------------


def score_employment_fit(posting: JobPosting, filters: dict) -> float:
    """Return a 0..1 fit score for ranking (not a hard filter).

    Starts from a neutral 0.5 and adds/subtracts small weights for employment
    type, remote fit, include keywords and timezone alignment. Never raises;
    returns 0.5 on any unexpected error.
    """
    try:
        f = _ensure_filters(filters)
        score = 0.5
        et = _norm_et(getattr(posting, "employment_type", ""))
        types = f.get("employment_types") or []
        if not types:
            score += 0.1
        elif et in types:
            score += 0.25
        elif not et:
            score += 0.05  # unknown stays neutral
        else:
            score -= 0.2

        if f.get("remote_only"):
            allowed = f.get("allowed_remote_policies") or []
            score += 0.2 if _remote_ok(posting, allowed, _blob(posting)) else -0.2

        include = f.get("include_keywords") or []
        if include and any(k in _blob(posting) for k in include):
            score += 0.15

        base = f.get("timezone_offset")
        if isinstance(base, (int, float)):
            p_off = _parse_tz_offset(getattr(posting, "timezone", ""))
            if p_off is not None:
                delta = float(f.get("timezone_delta") or 0.0)
                diff = abs(float(p_off) - float(base))
                diff = min(diff, 24.0 - diff)
                score += 0.1 if diff <= delta + 1e-6 else -0.1

        return max(0.0, min(1.0, score))
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("score_employment_fit_error", error=str(exc)[:160])
        return 0.5
