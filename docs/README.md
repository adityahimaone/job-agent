# Documentation index & implementation status

Last updated: 2026-11-06 · Repo: `job-agent` · Tests: **416 passing**

This page is the map. If you only read one thing, read the quick start below and
then [`TEMPLATES.md`](TEMPLATES.md) — that is where you edit your own facts,
cover letter, email and resume by hand.

---

## What is implemented

| Area | Status | How it was verified | What still needs you |
|---|---|---|---|
| Safe defaults (dry-run, approval gate, daily caps) | ✅ Done | `tests/unit/test_phase0_safety.py`, full suite | Nothing |
| Dashboard auth + CSRF + debugger off | ✅ Done | Same test file; token printed on `job-agent dashboard` | Keep the token private |
| Versioned DB migrations | ✅ Done | `job-agent init-db` runs Alembic `0001` → `0002` | Nothing |
| `applied_at`, follow-ups, daily caps | ✅ Done | Set on every successful submission | Nothing |
| Honest submission verification | ✅ Done | `orchestrator/verification.py` + tests; legacy drivers record `unverified` | Ego Lite confirms via snapshot |
| Speed: batch dedup, one commit/platform, no per-card sleeps | ✅ Done | 24 postings in ~3s from feeds; timings stored per run | Live board timings vary |
| LLM response cache + cost accounting + separate budgets | ✅ Done | `ai/client.py` usage(), `agent_runs.timings/ai_calls/estimated_cost_usd` | Needs an API key for real calls |
| Grounded documents from Markdown templates | ✅ Done | `job-agent content check/preview`, 18 content tests | Edit `config/templates/*.md` |
| Real candidate profile from your resume/site | ✅ Done | `config/templates/candidate_profile.md`, `config/profiles/aditya.yaml` | Review the 4+ vs 6+ years note |
| Spreadsheet approval surface (CSV + optional Google) | ✅ Done | `job-agent sheet template/push/decisions/pull`, 14 tests | For Google: install `gspread google-auth` |
| Fast feed lane (Remotive/RemoteOK/Arbeitnow/Greenhouse/Lever/Ashby/LinkedIn guest) | ✅ Done | `job-agent feeds` returned live results | Some feeds are region/rate limited |
| Part-time / remote / timezone / rate filters | ✅ Done | `matching/filters.py`, applied in pipeline + feeds | Tune `filters` in your profile |
| SEA boards (JobStreet, Glints, Kalibrr, Seek) | ✅ Specs + Ego lane | Board specs + generic Ego discovery, offline tests | Live DOM selectors may need a tweak |
| Ego Lite apply with human handoff | ✅ Done | 22 offline tests + `scripts/ego_selfcheck.py` (8/8) | A live run with your logged-in session |
| AI-training gig lane (Alignerr) | ✅ Done | `job-agent gigs refresh/list` → 13 eligible coding roles | Create account + pass assessment |
| Scheduler overlap protection | ✅ Done | `max_instances=1`, `coalesce=True`, run deadline | Nothing |
| Docs for every subsystem | ✅ Done | This file + the four guides below | Nothing |

**Not automated on purpose:** final submission on external sites (you approve
it), CAPTCHA solving, Alignerr-style assessments, and anything that would need
your password. See the compliance note at the end.

---

## Quick start

```bash
# 1. Your facts (the only file that should contain claims about you)
open config/templates/candidate_profile.md

# 2. Your search (keywords, part-time/remote filters, exclusions)
open config/profiles/aditya.yaml

# 3. Validate everything
uv run job-agent content check
uv run job-agent content preview --company "Acme" --title "Part-time Design Engineer"

# 4. Fast, no-browser discovery (safe)
uv run job-agent feeds -q "design engineer" -n 20

# 5. Create the approval sheet and push current jobs
uv run job-agent sheet template
uv run job-agent sheet push

# 6. Dry-run the pipeline (default is already dry-run)
uv run job-agent run --profile config/profiles/aditya.yaml --once

# 7. Approve in the sheet: set decision=approve on the rows you want
uv run job-agent sheet pull

# 8. Submit only what you approved
uv run job-agent apply-approved --live
```

The dashboard is optional:

```bash
uv run job-agent dashboard      # prints the token on first start
# open http://127.0.0.1:5000 and sign in with that token
```

### AI-training gigs

```bash
uv run job-agent gigs refresh
uv run job-agent gigs list
```

Gigs are a **separate lane**: no resume, no cover letter. You create an account
and pass an assessment on the network itself; the agent only finds, filters and
tracks them (rate, track, eligibility, status).

---

## Guides

| Doc | What it covers |
|---|---|
| [`TEMPLATES.md`](TEMPLATES.md) | Every editable Markdown template, every variable, how to prevent fabrication |
| [`SPREADSHEET.md`](SPREADSHEET.md) | CSV/Google Sheets setup, the `decision` workflow, custom columns |
| [`EGO_LITE.md`](EGO_LITE.md) | Ego Lite install/verify, human handoff, adding boards, troubleshooting |
| [`COVERAGE.md`](COVERAGE.md) | The three discovery lanes, all boards, filter semantics, gig lane, compliance |
| [`AUDIT-2026-11-06.md`](AUDIT-2026-11-06.md) | The original audit with evidence and the phased plan |

---

## Where your data lives

| Path | Contents |
|---|---|
| `config/templates/candidate_profile.md` | Your verified facts, roles, projects, constraints |
| `config/templates/{cover_letter,email_apply,connection_note,gig_profile,resume_master}.md` | Editable document templates |
| `config/profiles/aditya.yaml` | Search keywords, filters, exclusions |
| `~/.job-agent/documents/` | Generated resume PDF/Markdown, cover letter, email, manifest per job |
| `~/.job-agent/sheets/` | `jobs.csv`, `applications.csv`, `gigs.csv`, `search_profiles.csv` |
| `~/.job-agent/agent.db` | SQLite: jobs, matches, applications, gigs, runs |
| `~/.job-agent/dashboard_token` | Dashboard sign-in token (0600) |
| `~/.job-agent/browser_state/` | Playwright cookies/localStorage |
| `~/.job-agent/fernet.key` | Credential encryption key (0600) |

All of it is outside the repo except the templates and profile, which are meant
to be reviewed and edited in git.

---

## Known limitations (honest list)

1. **No live end-to-end application has been submitted by this build.** I did
   not log into your accounts or submit anything. Ego Lite was verified at the
   bridge level (`ego-browser nodejs` round-trip) and with offline tests, but the
   first real application should be watched in the Ego Space.
2. **Google Sheets is optional.** The CSV backend works with zero extra
   dependencies; Google needs `uv pip install gspread google-auth` and a service
   account shared with your sheet.
3. **Playwright board drivers still use CSS selectors.** LinkedIn/Indeed/
   Glassdoor/Dice/Wellfound markup changes will break them; the feed lane and
   Ego Lite lane are the resilient paths.
4. **Platform runs are sequential.** Feeds run concurrently, but browser
   platforms run one at a time to avoid SQLite/browser-session races. The
   wall-clock budget (`agent.max_run_minutes`) bounds a run either way.
5. **LLM features need a key.** Without one, matching returns 0.0 and documents
   fall back to deterministic template rendering (still grounded, just less
   fluent).
6. **Alignerr and similar networks are account + assessment.** The agent cannot
   (and should not) do the assessment for you.
7. **Compliance.** Automated applications on LinkedIn/Indeed/JobStreet are
   against most platforms' terms. The defaults (approval, daily caps, no
   unattended submit) are deliberately conservative; keep them that way.

---

## Verification commands

```bash
uv run pytest -q                                   # 416 passed
uv run ruff check src/ tests/ scripts/             # clean
uv run ruff format --check src/ tests/             # clean
PYTHONPATH=src uv run python scripts/ego_selfcheck.py   # 8/8
uv run job-agent content check                     # profile + templates
uv run job-agent ego doctor                        # live Ego bridge check
```
