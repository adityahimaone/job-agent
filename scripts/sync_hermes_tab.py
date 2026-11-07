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
skills_cfg = profile.get("skills", {})
all_skills = list(skills_cfg.get("required", [])) + list(skills_cfg.get("preferred", []))


def excluded(location: str | None) -> bool:
    loc = (location or "").lower()
    return any(x in loc for x in excluded_locations)


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

# 2. backfill empty matched/missing skills from the description (no AI)
filled = 0
q = """select m.id, j.description from match_results m join jobs j on j.id=m.job_id
       where coalesce(m.matched_skills,'') in ('','[]')"""
for mid, desc in con.execute(q).fetchall():
    d = (desc or "").lower()
    if not d:
        continue
    matched = [s for s in all_skills if s.lower() in d]
    missing = [s for s in all_skills if s.lower() not in d]
    if not matched:
        continue
    con.execute(
        "update match_results set matched_skills=?, missing_skills=? where id=?",
        (", ".join(matched), ", ".join(missing), mid),
    )
    filled += 1
con.commit()

# 3. collect rows for the tab
q = """
SELECT j.title, j.company, j.location, j.url, j.status, j.discovered_at,
       m.score, m.matched_skills, m.missing_skills, m.red_flags, m.reasoning
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

# purge excluded-location rows: rebuild the whole tab in ONE write (Sheets
# write quota is 60/min/user — 29 row deletes used to blow it)
purge = {i for i, r in enumerate(body) if len(r) > 4 and excluded(r[4])}
body = [r for i, r in enumerate(body) if i not in purge]

by_link = {}
for i, r in enumerate(body):
    link = (r[14] if len(r) > 14 else "").strip()
    if link:
        by_link[link] = i

updates: list[tuple[int, list[str]]] = []  # 0-based body index -> full 15-col row
appends: list[list[str]] = []
next_no = max([int(r[0]) for r in body if r and str(r[0]).isdigit()] or [0]) + 1

for title, company, loc, url, status, disc, score, mk, ms, flags, reason in db_rows:
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
        (mk or "")[:120],
        (ms or "")[:120],
        (flags or "")[:80],
        (reason or "")[:240],
        (status or "").lower(),
    ]
    key = (url or "").strip()
    if key and key in by_link:
        old = body[by_link[key]] + [""] * (15 - len(body[by_link[key]]))
        full = [
            old[0], old[1], gen[0], gen[1], gen[2], gen[3], gen[4], gen[5],
            old[8] if old[8] else ("queue" if fit >= 55 else "skip"),
            gen[6], gen[7], gen[8], gen[9],
            old[13] if old[13] else gen[10], key,
        ]
        updates.append((by_link[key], full))
    elif len(appends) < LIMIT:
        appends.append(
            [str(next_no), date.today().isoformat(),
             gen[0], gen[1], gen[2], gen[3], gen[4], gen[5],
             "queue" if fit >= 55 else "skip",
             gen[6], gen[7], gen[8], gen[9], gen[10], key]
        )
        next_no += 1

if not DRY:
    # one write: rewrite the whole tab (header + survivors-with-updates + appends)
    merged_by_index = dict(updates)
    final: list[list[str]] = [header]
    last = 2
    for i, r in enumerate(body):
        row = merged_by_index.get(i, r)
        row = row + [""] * (15 - len(row))
        row[0] = str(last)
        final.append(row)
        last += 1
    for row in appends:
        row[0] = str(last)
        final.append(row)
        last += 1
    ws.update(values=final, value_input_option="RAW")
    if len(final) < len(vals):
        ws.resize(rows=len(final))  # shrink trailing rows if any were purged

print(
    f"purged_detail_rows={len(purge)} db_backfilled={filled} "
    f"updated={len(updates)} appended={len(appends)} "
    f"banned_jobs={len(banned)} dry_run={DRY}"
)