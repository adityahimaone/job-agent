"""Append job-agent DB results into the 'hermes' research tab."""

import sqlite3
import sys
from datetime import date

sys.path.insert(0, "src")
from job_agent.integrations.spreadsheet import GoogleSheetsBackend, SheetConfig  # noqa: E402

SHEET_ID = "1qzDe71Wl7RviTVUYalDGOVmvHy4yAxrWtE_wty8Dx68"
DB = "/home/adityahimaone/.job-agent/agent.db"
MIN_FIT = 30  # percent; lower matches add nothing to a research table
LIMIT = 60

cfg = SheetConfig(
    directory="~/.job-agent/sheets",
    backend="google",
    spreadsheet_id=SHEET_ID,
    credentials_path="~/.hermes/google_client_secret.json",
    token_path="~/.hermes/google_token.json",
)
ws = GoogleSheetsBackend(cfg)._spreadsheet.worksheet("hermes")
existing = ws.get_all_values()
header, rows = existing[0], existing[1:]
known_links = {r[14].strip() for r in rows if len(r) > 14 and r[14].strip()}
next_no = max([int(r[0]) for r in rows if r and str(r[0]).isdigit()] or [0]) + 1

con = sqlite3.connect(DB)
q = """
SELECT j.title, j.company, j.location, j.url, j.status, j.employment_type,
       j.discovered_at, m.score, m.reasoning, m.matched_skills, m.missing_skills,
       m.red_flags
FROM jobs j JOIN match_results m ON m.job_id = j.id
ORDER BY m.score DESC
"""
out = []
for (title, company, loc, url, status, emp, disc, score, reason, matched,
     missing, flags) in con.execute(q):
    fit = round(float(score or 0) * 100)
    if fit < MIN_FIT or (url or "").strip() in known_links:
        continue
    low = (title or "").lower()
    level = "senior" if "senior" in low or "lead" in low else "mid"
    out.append([
        str(next_no), date.today().isoformat(), title or "", company or "",
        loc or "", (disc or "")[:10], str(fit), level,
        "queue" if fit >= 55 else "skip",
        (matched or "")[:120], (missing or "")[:120], (flags or "")[:80],
        (reason or "")[:240], (status or "").lower(), url or "",
    ])
    next_no += 1
    if len(out) >= LIMIT:
        break

if out:
    ws.append_rows(out, value_input_option="RAW")
print(f"appended {len(out)} rows (fit >= {MIN_FIT}), numbering {rows and next_no - len(out)}..{next_no - 1}")