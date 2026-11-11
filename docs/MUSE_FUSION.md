# Muse fusion lane

Jobs Muse finds (in chat, email, or the web) enter job-agent through an
import file — there is no HTTP feed to poll, because Muse is the source.

## Import

```bash
# Dry run first: parse + dedup + score in memory, write nothing.
job-agent muse import docs/muse_jobs_example.json \
  --profile config/profiles/aditya.yaml --dry-run

# Real import: store, score (JEV first, OpenRouter chat fallback),
# decide with the normal thresholds. Best outcome is QUEUED — import
# never applies to a job.
job-agent muse import docs/muse_jobs_example.json \
  --profile config/profiles/aditya.yaml
```

Options: `--dry-run`, `--no-score` (store as DISCOVERED, match on the
next cron run), `--limit N`.

## Input format

JSON (a list, `{"jobs": [...]}`, or a single object) or CSV. Required
per job: `title`, `company`. Everything else is optional.

```json
[
  {
    "title": "React Frontend Developer",
    "company": "Acme Studio",
    "location": "Remote, Singapore",
    "url": "https://example.com/jobs/react-frontend-acme",
    "description": "Build React and TypeScript front ends…",
    "salary": "$30-40/hr",
    "employment_type": "contract",
    "external_id": "acme-react-1"
  }
]
```

Accepted aliases: `link` / `apply_url` for `url`, `job_description` for
`description`, `job_type` for `employment_type`, `id` for `external_id`,
`origin_platform` for where Muse originally saw the posting.

## How it flows

1. **Parse** (`src/job_agent/discovery/muse.py:parse_muse_file`) —
   invalid rows are reported, never fatal.
2. **Normalise** with the feeds' `_normalize` helper; stamp
   `platform=muse`, `source="muse"`, `external_id="muse:<id|url-hash>"`.
3. **Dedup** — within the batch, against existing `(external_id, muse)`
   rows, and against the same title+company on another platform (the
   feed lane already found those; they are counted, not re-created).
4. **Score** with the existing `JobMatcher` (JEV first, chat fallback)
   and **decide** with `decide_job(..., require_approval=True)`.
   `>= review_threshold` (0.55 in `config/default.yaml`) → QUEUED,
   below → SKIPPED. No AI provider → left DISCOVERED for a later run.
5. **Surface** — `scripts/sync_hermes_tab.py` selects by score/status,
   not platform, so a muse job scoring >= MIN_FIT (30) appears in the
   hermes tab like any other job. The next 6-hourly cron run also
   matches any DISCOVERED muse jobs left by `--no-score`.

## Config

`config/feeds.yaml` lists a `muse` feed (`kind: muse`, import-only).
Its parser in `src/job_agent/discovery/feeds.py` returns `[]` — it
exists so the source is visible in feed listings and so a cron feed
run never fails on an unknown kind. No DB migration was needed:
`Platform.MUSE` is a plain string value and SQLite has no CHECK
constraint on `jobs.platform`.

## Tests

```bash
uv run pytest tests/unit/test_muse_fusion.py -q
```

Covers parsing (JSON/CSV, invalid rows), external-id namespacing,
import + scoring + queueing, re-import dedup, cross-platform dedup,
dry-run writing nothing, and the import-only feed parser.

## LinkedIn timeline/feed jobs

Jobs shared as timeline posts (not formal postings) enter through a
sibling lane: `job-agent muse import-feed <file>`. See
`docs/MUSE_FEED.md` — same contract (file in, QUEUED-only import out),
plus post classification/extraction and a grounded connection-note
preview (`muse feed-note --job-id <id>`, preparation only, never sent).
