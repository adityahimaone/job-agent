# Muse discovery cron — spec (not yet created)

This is the spec for the Muse cron that drives the Muse-primary /
Ego-fallback lane (`docs/MUSE_PRIMARY_EGO_FALLBACK.md`). The cron itself
is **not** created by job-agent and is not in this repo — the parent agent
creates it in Muse. This file exists so the cron's behaviour is written
down, reviewable, and identical every run.

## Recommended schedule

**Daily, 07:30 Asia/Jakarta** (after the 07:21 daily Gmail summary, so
job news and mail news do not collide), 7 days a week.

Why daily, not every 6 hours:

* The existing 6-hour `run-cron.sh` already polls the fast structured feeds
  (Remotive / RemoteOK / Arbeitnow / LinkedIn guest). A Muse search is a
  *human-paced, signed-in* browser session — running it 4× a day would
  look automated and burn the LinkedIn session that took a reCAPTCHA to
  establish.
* Frontend roles in the target market do not turn over every 6 hours;
  daily is enough to catch new postings within a day, and the fallback
  exists for the days Muse finds nothing.
* One run a day keeps the report to Aditya to one scannable message.

If Aditya later wants more volume, raise it to twice daily (07:30 and
19:30 WIB) — never to every 6 hours, and never with more than ~10
queries per run.

## What one cron run does, in order

1. **Search (Muse, primary).** Using Muse's own browser/search, signed
   in to LinkedIn where a session exists, search the queries below.
   Keep it human-paced: open postings one at a time, at most ~10 queries,
   at most 10 results kept per query. Skip anything that needs a new
   account, a checkpoint, or a CAPTCHA — record it and move on.
2. **Write the file.** Save the jobs found to
   `/tmp/muse_jobs_<YYYY-MM-DD>.json` on the VPS (or the Muse workspace,
   then copy it over), in the fusion-lane format: each row needs
   `title` + `company`; `url`, `description`, `location`, `salary`,
   `employment_type` are optional but should be filled when visible.
   Never invent a description, salary, or requirement that was not on
   the posting — leave the field out instead.
3. **Import.** On the VPS, run:
   ```bash
   cd ~/apps/job-agent
   .venv/bin/job-agent muse run /tmp/muse_jobs_<YYYY-MM-DD>.json \
     --profile config/profiles/aditya.yaml --with-ego-fallback
   ```
   Dry-run first on any day the file format changed; otherwise the real
   run above is enough (import is QUEUED-only, never applies).
4. **Fallback (automatic, inside the command).** If the Muse import
   yields zero new jobs, `muse run` runs the Ego fallback itself
   (LinkedIn + Dealls, `ego_queries` / `ego_locations` from config) —
   but only if the Mac is online. If the Mac is offline, the run ends
   with that reported, not retried.
5. **Report to Aditya in the main chat.** One short message: how many
   new jobs Muse found, how many queued, whether the Ego fallback ran
   and what it found, and the top 2–3 queued jobs by score with title,
   company, location, and link. If nothing new was found anywhere, say
   so in one line — no empty report, no hype.

## LinkedIn feed step (proposed, not yet live)

The daily cron does not yet read the LinkedIn timeline. Proposed addition,
to be approved by Aditya before it runs:

* After step 2, Muse scrolls the signed-in LinkedIn home feed
  (`linkedin.com/feed`) once, human-paced, and records hiring posts it has
  not seen before into `/tmp/muse_feed_<YYYY-MM-DD>.json` (format in
  `docs/MUSE_FEED.md`), then runs
  `job-agent muse import-feed /tmp/muse_feed_<YYYY-MM-DD>.json --profile
  config/profiles/aditya.yaml` on the VPS.
* At most ~10 posts kept per run; never invent title/company/salary;
  never click Connect or send a message; stop on a checkpoint/CAPTCHA.
* The report adds one line: how many feed posts were seen, how many
  became jobs, and the top queued feed job (with the poster, so Aditya can
  decide about connecting — see `muse feed-note`).

## Queries (from `config/profiles/aditya.yaml`)

Primary queries — Design Engineer / Frontend Engineer (React, Design
Systems), remote-first, SEA-first:

| # | Query | Locations |
|---|---|---|
| 1 | Frontend Engineer React | Singapore, Remote |
| 2 | Design Engineer | Singapore, Remote |
| 3 | React Developer | Singapore, Remote, Jakarta |
| 4 | Frontend Engineer Design Systems | Singapore, Remote |

Fallback (Ego) queries — shorter, because Ego searches are guest pages:

| # | Query | Locations |
|---|---|---|
| 1 | Frontend Engineer | Singapore, Remote |
| 2 | Design Engineer | Singapore, Remote |
| 3 | React Developer | Singapore, Remote |

Platforms: LinkedIn first, Dealls second (Dealls blocks the VPS IP, so
it only works through the Mac/Ego lane). Do **not** add Indeed /
ZipRecruiter / Dice to the Muse cron — they have no credentials in
job-agent and their lanes log `no_credentials`.

Hard exclusions (enforced by the profile at match time; the cron must not
import them knowingly): India, Pakistan, Bangladesh, Sri Lanka,
Saudi Arabia. Also skip internships and Staff/Principal+ senior roles.

## What the cron must NEVER do

* Apply to a job, submit any form, or upload a resume. Preparation only.
* Sign in to LinkedIn (or any site) with a password — use the existing
  signed-in session; if it has expired, report it and stop the LinkedIn
  part instead of signing in from the cron.
* Retry a CAPTCHA / security checkpoint, or solve one on Aditya's behalf
  without him present. One attempt, then record and move on.
* Invent job details, salaries, or requirements.
* Run the Ego fallback without `--with-ego-fallback`, or run Ego searches
  when the Muse lane already found new jobs.
* Touch the live DB except through `job-agent muse run` / `muse import`.
* Commit, push, or deploy anything.

## Failure modes

| Situation | Expected behaviour |
|---|---|
| Muse finds new jobs | Import them; Ego does not run; report the queued ones |
| Muse finds nothing new | Ego fallback runs (Mac online) or is reported skipped (Mac offline) |
| Muse file missing/empty | Counts as zero new; fallback triggers |
| LinkedIn session expired | Report it; LinkedIn searches are skipped that day, other queries still run |
| LinkedIn checkpoint / CAPTCHA | Stop that search, record it, do not retry |
| Mac offline | Ego fallback skipped with reason; run still reports the Muse result |
| No AI provider available | Jobs stay DISCOVERED; the next pipeline run scores them |
