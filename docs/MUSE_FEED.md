# Muse fusion lane — LinkedIn timeline/feed jobs

Jobs shared as LinkedIn *timeline posts* ("We're hiring a Frontend
Engineer…", "Looking for a React dev…") never appear in LinkedIn's formal
job search, so the normal Muse search misses them. This lane imports them
through the same fusion contract as `docs/MUSE_FUSION.md`: Muse reads the
timeline in the browser, writes a feed file, and `job-agent` imports it.

## Why a file, not a scraper

There is no public API for a signed-in LinkedIn timeline, and job-agent
cannot call Muse's browser. The Muse lane is therefore driven by an
external Muse run that writes a jobs file — same as `docs/MUSE_CRON.md`.
A scraper in the repo would also be the part LinkedIn rate-limits first;
a human-paced browser read is not.

## Import

```bash
# Dry run first: parse + extract + dedup + score in memory, write nothing.
job-agent muse import-feed docs/muse_feed_example.json \
  --profile config/profiles/aditya.yaml --dry-run

# Real import: store, score (JEV first, OpenRouter chat fallback),
# decide with the normal thresholds. Best outcome is QUEUED — import
# never applies to a job, never sends a connection, never sends a message.
job-agent muse import-feed docs/muse_feed_example.json \
  --profile config/profiles/aditya.yaml
```

Options: `--dry-run`, `--no-score` (store as DISCOVERED, match on the
next cron run), `--limit N`.

## Input format

JSON (a list, `{"posts": [...]}`, `{"jobs": [...]}`, or a single post
object) or CSV. One row per LinkedIn post. Only the post text is
required; everything else helps extraction.

```json
[
  {
    "post_url": "https://www.linkedin.com/posts/code-ninety_hiring-activity-1",
    "post_text": "HIRING – Frontend Developer | Remote. Code Ninety is looking for a Frontend Developer to build intuitive user interfaces. Location: Remote. Experience: 2+ years.",
    "author": "Code Ninety",
    "author_company": "Code Ninety",
    "posted_at": "2026-11-03"
  },
  {
    "post_url": "https://www.linkedin.com/posts/someone_activity-2",
    "post_text": "We're hiring! Details in the comments. Apply now.",
    "author": "Hiring Manager",
    "title": "React Frontend Developer",
    "company": "Acme Studio",
    "location": "Remote, Singapore",
    "apply_url": "https://example.com/jobs/react-frontend-acme"
  }
]
```

Accepted aliases: `post_link` / `url` / `link` for `post_url`, `text` /
`content` / `body` for `post_text`, `poster` / `posted_by` for `author`,
`company_name` / `employer` for `company`, `role` / `job_title` for
`title`, `job_url` for `apply_url`, `job_description` for `description`,
`job_type` for `employment_type`, `id` for `external_id`, `date` for
`posted_at`.

Explicit `title` + `company` always win over extraction — use them when
the post text is vague ("DM me for details") but the role is known from
the post's comments, the poster's profile, or the linked job page. Never
invent a title, company, salary, or requirement that was not visible;
leave the field out instead and the post is reported as incomplete.

## How a post becomes a job (or doesn't)

1. **Classify** (`classify_post`) — the post text must carry a hiring
   signal (`hiring`, `looking for`, `seeking`, `job opening`, `open role`,
   `vacancy`, `join our team`, `apply now`, …). Launch posts, release
   notes, and congratulations are counted as `non_job` and never stored.
2. **Extract** (`extract_job_details`) — title/company/location/apply URL
   from the text, in order: `"<Title> at <Company>"`, `"hiring … <Title>"`,
   `"Role: <Title>"`, `"join our team as <Title>"`; location from known
   tokens (Remote, Singapore, Jakarta, Kuala Lumpur, …); apply URL is the
   first non-post URL in the text, else the post URL.
   An auto-extracted title must be a frontend/design role — a post hiring
   a "Head of People" is rejected, not imported. Explicit title/company
   rows skip this check (Muse already judged them).
3. **Normalise** with the feeds' `_normalize` helper; stamp
   `platform=muse`, `source="muse-feed"`,
   `external_id="muse-feed:<sha1(post_url)>"` (or the given id). The post
   URL, author, and posted date are kept in `extra` (`post_url`,
   `post_author`, `posted_at`, `origin_platform="linkedin_feed"`).
4. **Dedup** — within the batch, against existing `(external_id, muse)`
   rows, and against the same title+company on another platform (a formal
   posting the feed lane already found is counted, not re-created).
5. **Score** with the existing `JobMatcher` (JEV first, chat fallback)
   and **decide** with `decide_job(..., require_approval=True)`.
   `>= review_threshold` (0.40 in `config/default.yaml`) → QUEUED,
   below → SKIPPED. The thin-description floor applies: most feed posts
   are one-liners, so a frontend-titled post scores at least 0.55 (0.60
   with 2+ stack skills) instead of hard-zeroing. No AI provider → left
   DISCOVERED for a later run.

## Connecting to the poster — preparation only

A feed post's value is often the person, not just the role: the poster is
usually the hiring manager. The app already has the grounded connection
template for exactly this — `config/templates/connection_note.md`
(<300 characters, rendered from `candidate_profile.md` by the content
generator, grounding-checked). Preview it for any imported feed job:

```bash
job-agent muse feed-note --job-id <id>
```

It prints the note and any grounding warnings, plus a `NOT SENT` banner.
**Nothing is sent by this command.** The note must be approved by Aditya
word-for-word before anyone clicks Connect in a browser. The programmatic
outreach in `src/job_agent/platforms/linkedin/outreach.py`
(`LinkedInOutreach.send_connection_request`, with its own dedup and daily
limit) is deliberately NOT wired into this lane — sending a connection is
a human decision, and the Muse browser session is the place to do it.

## How the daily cron covers the feed (proposed)

`docs/MUSE_CRON.md` gains a feed step (proposal, not yet live): after the
normal search, Muse scrolls the signed-in LinkedIn home feed
(`linkedin.com/feed`) once, human-paced, and records hiring posts it has
not seen before into `/tmp/muse_feed_<YYYY-MM-DD>.json`, then runs
`job-agent muse import-feed` on it. Same rules as the search step: at most
~10 posts kept per run, never invent details, never click Connect, stop
on a checkpoint/CAPTCHA. The live cron is unchanged until Aditya approves
the addition.

## Config

`config/feeds.yaml` lists a `muse_feed` feed (`kind: muse_feed`,
import-only). Its parser in `src/job_agent/discovery/feeds.py` returns
`[]` — it exists so the source is visible in feed listings and so a cron
feed run never fails on an unknown kind. No DB migration was needed:
`Platform.MUSE` is a plain string value and SQLite has no CHECK
constraint on `jobs.platform`.

## Tests

```bash
uv run pytest tests/unit/test_muse_feed.py -q
```

Covers classification, extraction (`"<Title> at <Company>"`, `"hiring …
<Title>"`, apply-URL preference), explicit-field precedence, non-job and
incomplete rejection, non-frontend rejection, JSON/CSV parsing with
`non_job`/`incomplete` counts, import + scoring + queueing, re-import
dedup, cross-platform dedup against a formal LinkedIn posting, dry-run
writing nothing, the thin-description floor on a one-line feed post, the
grounded connection-note render (≤300 chars, no grounding warnings), and
the import-only feed parser registration.
