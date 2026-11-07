"""Sync job-agent DB matches into the 'hermes' research tab (bidirectional-safe).

Rules:
  * excluded locations (profile exclusions.locations) are purged from the tab
  * existing rows are updated in place (keyed by Link) for derived columns only
    — Verdict / Status stay Adit's, they are never overwritten
  * new DB rows (fit >= MIN_FIT) are appended
  * empty Cocok / Gap columns are backfilled from the description before sync

Usage: PYTHONPATH=src .venv/bin/python scripts/sync_hermes_tab.py [--dry-run]
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, "src")
from job_agent.integrations.spreadsheet import GoogleSheetsBackend, SheetConfig  # noqa: E402

SHEET_ID = "1qzDe71Wl7RviTVUYalDGOVmvHy4yAxrWtE_wty8Dx68"
DB = "/home/adityahimaone/.job-agent/agent.db"
PROFILE = "config/profiles/aditya.yaml"
MIN_FIT = 30
LIMIT = 120
DRY = "--dry-run" in sys.argv

profile = yaml.safe_load(open(PROFILE))
excluded_locations = [s.lower() for s in profile.get("exclusions", {}).get("locations", [])]
location_priority = [s.lower() for s in
                    profile.get("search", {}).get("location_priority", [])]
skills_cfg = profile.get("skills", {})
all_skills = list(skills_cfg.get("required", [])) + list(skills_cfg.get("preferred", []))


def excluded(location: str | None) -> bool:
    loc = (location or "").lower()
    return any(x in loc for x in excluded_locations)


def location_rank(location: str | None) -> int:
    """SEA-first tier: 0 = Jakarta, 1 = Indonesia, 2 = SG, ... big = unknown."""
    loc = (location or "").lower()
    for i, needle in enumerate(location_priority):
        if needle in loc:
            return i
    return len(location_priority) + 1


def work_mode(title: str | None, location: str | None,
              remote: int | bool | None, policy: str | None) -> str:
    """'Remote' / 'Hybrid' / 'WFO' from the stored fields, text scan as fallback."""
    p = (policy or "").lower()
    blob = f"{title or ''} {location or ''} {policy or ''}".lower()
    if p in ("remote",) or remote in (1, True, "1", "true"):
        return "Remote"
    if p in ("hybrid",) or "hybrid" in blob or "wfh" in blob:
        return "Hybrid"
    if p in ("onsite", "on_site", "on-site", "office") or "on-site" in blob \
            or "on site" in blob or "work from office" in blob:
        return "WFO"
    if "remote" in blob:
        return "Remote"
    return "WFO" if (location or "").strip() else "-"


def key_for(title: str | None, company: str | None, url: str | None) -> str:
    """Dedupe key: Link when present, else title|company (41 feed jobs have no URL)."""
    u = (url or "").strip()
    return u if u else f"{(title or '').strip().lower()}|{(company or '').strip().lower()}"


def render_skills(raw: str | None) -> str:
    """match_results stores JSON lists OR legacy comma strings — render readable."""
    if not raw or raw == "[]":
        return "-"
    try:
        items = json.loads(raw)
        return ", ".join(items) if isinstance(items, list) and items else "-"
    except (ValueError, TypeError):
        return raw


# ---------------------------------------------------------------- DB side
con = sqlite3.connect(DB)

# 1. excluded-location jobs: score 0 + SKIPPED so they never resurface
rows = con.execute("select id, location from jobs").fetchall()
banned = [jid for jid, loc in rows if excluded(loc)]
for jid in banned:
    con.execute("update jobs set status='SKIPPED' where id=? and status!='APPLIED'", (jid,))
    con.execute(
        "update match_results set score=0.0, red_flags='[\"excluded_location\"]',"
        " reasoning='Location excluded by profile.' where job_id=?",
        (jid,),
    )

# 2. backfill empty matched/missing skills from description+title (no AI).
#    Canonical form: JSON array (what the dataclass serializes to work against).
filled = 0
q = """select m.id, j.description, j.title, m.matched_skills, m.missing_skills
       from match_results m join jobs j on j.id=m.job_id"""
for mid, desc, title, mk, ms in con.execute(q).fetchall():
    need = render_skills(mk) == "-" or render_skills(ms) == "-"
    if not need:
        continue
    d = ((desc or "") + " " + (title or "")).lower()
    if not d.strip():
        continue
    matched = [s for s in all_skills if s.lower() in d]
    missing = [s for s in all_skills if s.lower() not in d]
    if not matched:
        continue  # nothing real to show in Cocok; sheet renders '-'
    con.execute(
        "update match_results set matched_skills=?, missing_skills=? where id=?",
        (json.dumps(matched), json.dumps(missing), mid),
    )
    filled += 1
con.commit()

# 3. collect rows for the tab
q = """
SELECT j.title, j.company, j.location, j.url, j.status, j.discovered_at,
       m.score, m.matched_skills, m.missing_skills, m.red_flags, m.reasoning,
       j.remote, j.remote_policy
FROM jobs j JOIN match_results m ON m.job_id = j.id
WHERE m.score >= ? OR j.status = 'QUEUED'
ORDER BY m.score DESC
"""
db_rows = [r for r in con.execute(q, (MIN_FIT / 100.0,)).fetchall() if not excluded(r[2])]
con.close()

# ---------------------------------------------------------------- sheet side
cfg = SheetConfig(
    directory=Path("~/.job-agent/sheets").expanduser(),
    backend="google",
    spreadsheet_id=SHEET_ID,
    credentials_path="~/.hermes/google_client_secret.json",
    token_path="~/.hermes/google_token.json",
)
ws = GoogleSheetsBackend(cfg)._spreadsheet.worksheet("hermes")
vals = ws.get_all_values()
header, body = vals[0], vals[1:]
if len(header) < 16:
    header = header + [""] * (16 - len(header))
    header[15] = "WFO/Remote"

# purge India rows + dedupe the tab (earlier appends duplicated URL-less feed
# jobs): keep first occurrence, prefer the row that has a Link)
purge = {i for i, r in enumerate(body) if len(r) > 4 and excluded(r[4])}
body = [r for i, r in enumerate(body) if i not in purge]
seen: dict[str, int] = {}
deduped: list[list[str]] = []
dups = 0
for r in body:
    k = key_for(r[2] if len(r) > 2 else None, r[3] if len(r) > 3 else None,
                r[14] if len(r) > 14 else None)
    if k in seen:
        dups += 1
        idx = seen[k]
        if (deduped[idx][14] if len(deduped[idx]) > 14 else "").strip() == "" \
                and len(r) > 14 and r[14].strip():
            deduped[idx] = r  # keep the version that has a link
        continue
    seen[k] = len(deduped)
    deduped.append(r)
body = deduped

by_key = {}
for i, r in enumerate(body):
    by_key[key_for(r[2] if len(r) > 2 else None, r[3] if len(r) > 3 else None,
                   r[14] if len(r) > 14 else None)] = i

updates: list[tuple[int, list[str]]] = []  # 0-based body index -> full 15-col row
appends: list[list[str]] = []
next_no = max([int(r[0]) for r in body if r and str(r[0]).isdigit()] or [0]) + 1

for title, company, loc, url, status, disc, score, mk, ms, flags, reason, \
        remote, policy in db_rows:
    fit = round(float(score or 0) * 100)
    if fit < MIN_FIT:
        continue
    low = (title or "").lower()
    level = "senior" if "senior" in low or "lead" in low else "mid"
    gen = [
        title or "",
        company or "",
        loc or "",
        (disc or "")[:10],
        str(fit),
        level,
        render_skills(mk)[:120],
        render_skills(ms)[:120],
        (flags or "")[:80],
        (reason or "")[:240],
        (status or "").lower(),
        work_mode(title, loc, remote, policy),
    ]
    key = key_for(title, company, url)
    if key in by_key:
        old = body[by_key[key]] + [""] * (16 - len(body[by_key[key]]))
        full = [
            old[0], old[1], gen[0], gen[1], gen[2], gen[3], gen[4], gen[5],
            old[8] if old[8] else ("queue" if fit >= 55 else "skip"),
            gen[6], gen[7], gen[8], gen[9],
            old[13] if old[13] else gen[10], (url or "").strip(), gen[11],
        ]
        updates.append((by_key[key], full))
    elif len(appends) < LIMIT:
        appends.append(
            [str(next_no), date.today().isoformat(),
             gen[0], gen[1], gen[2], gen[3], gen[4], gen[5],
             "queue" if fit >= 55 else "skip",
             gen[6], gen[7], gen[8], gen[9], gen[10], (url or "").strip(), gen[11]]
        )
        next_no += 1

if not DRY:
    # one write: rewrite the whole tab (header + survivors-with-updates + appends)
    merged_by_index = dict(updates)
    final: list[list[str]] = [header]
    last = 1
    for i, r in enumerate(body):
        row = merged_by_index.get(i, r)
        row = row + [""] * (16 - len(row))
        if not row[15].strip():  # rows with no DB match: derive from own text
            row[15] = work_mode(row[2] if len(row) > 2 else None,
                                row[4] if len(row) > 4 else None, None, None)
        row[0] = str(last)
        final.append(row)
        last += 1
    for row in appends:
        row[0] = str(last)
        final.append(row)
        last += 1
    body_rows = final[1:]
    body_rows.sort(key=lambda r: (location_rank(r[4] if len(r) > 4 else None),
                                  -(int(r[6]) if len(r) > 6 and str(r[6]).isdigit() else 0)))
    final = [header] + body_rows
    for n, row in enumerate(final[1:], start=1):
        row[0] = str(n)
    ws.update(values=final, value_input_option="RAW")
    if len(final) < len(vals):
        ws.resize(rows=len(final))  # shrink trailing rows if any were purged

print(
    f"purged_detail_rows={len(purge)} dups_removed={dups} db_backfilled={filled} "
    f"updated={len(updates)} appended={len(appends)} "
    f"banned_jobs={len(banned)} dry_run={DRY}"
)