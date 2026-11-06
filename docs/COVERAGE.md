# Discovery Coverage Guide

How `job-agent` finds work, how the fast-lane filters narrow it, and how the
AI-training gig lane differs from ordinary job applications.

This document covers the modules owned by the **coverage** lane:

| Module | Responsibility |
| --- | --- |
| `src/job_agent/discovery/feeds.py` | Fast structured-feed discovery (the "fast lane") |
| `src/job_agent/discovery/boards.py` | Board definitions for the Ego Lite browser lane |
| `src/job_agent/matching/filters.py` | Deterministic employment / remote / timezone / pay filters |
| `src/job_agent/gigs/networks.py` | AI-training gig sources (Alignerr + stubs) |
| `src/job_agent/gigs/service.py` | Gig refresh, eligibility, persistence, sheet rows |
| `config/feeds.yaml` | Editable feed / board / gig source lists |

---

## 1. The three discovery lanes

Discovery is layered so the cheapest, most reliable source runs first and the
expensive, human-supervised ones are only used when needed.

### Lane 1 — Structured feeds (fast lane) — `FeedHub`

`job_agent.discovery.feeds` fetches public JSON/HTML endpoints concurrently
with one shared `httpx.Client`, a `ThreadPoolExecutor` (`max_workers`, default
6), per-request timeouts (`timeout`, default 12s) and a polite descriptive
`User-Agent`.

```python
from job_agent.discovery import FeedHub, load_feed_specs
from job_agent.matching import build_filters

specs = load_feed_specs("config/feeds.yaml")          # reads the `feeds:` list
hub = FeedHub(specs, timeout=12.0, max_workers=6)
jobs = hub.discover(
    queries=["product designer", "ux engineer"],
    locations=["Jakarta, Indonesia"],
    limit=100,
    filters=build_filters(profile),
)
for r in hub.results():                                # diagnostics
    print(r.source, len(r.postings), r.elapsed_ms, r.error)
```

What `discover()` does, in order:

1. Runs every **enabled** feed concurrently. A failing feed (network error,
   HTTP error, bad JSON, unknown board token) **never aborts** the run: the
   error is stored on `FeedResult.error`, a warning is logged, and the other
   feeds continue. With no network at all, `discover()` returns `[]`.
2. Applies `matching.filters.apply_filters(...)`.
3. Dedupes by `(external_id, platform)`; when `external_id` is missing it falls
   back to a hash of the posting URL.
4. Caps at `limit` and stamps `source` on every posting.

**Supported feed kinds / parsers**

| `kind` | Endpoint | Notes |
| --- | --- | --- |
| `remotive` | `https://remotive.com/api/remote-jobs` | `jobs[]`; `job_type`, `tags`, `salary`, all remote |
| `remoteok` | `https://remoteok.com/api` | JSON array; skips the leading metadata object; `salary_min/max` |
| `arbeitnow` | `https://www.arbeitnow.com/api/job-board-api` | `data[]`; `job_types`, `tags`, `remote` flag |
| `greenhouse` | `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | needs a board **token** |
| `lever` | `https://api.lever.co/v0/postings/{token}?mode=json` | `categories.commitment/location` |
| `ashby` | `https://api.ashbyhq.com/posting-api/job-board/{token}` | `employmentType`, `isRemote`, `descriptionPlain` |
| `linkedin_guest` | `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search` | regex-parsed HTML `<li>` cards; often rate-limited |

For Greenhouse / Lever / Ashby, `FeedSpec.url` is the company board token (or a
full endpoint URL). They ship **disabled** in `default_feed_specs()` until a
real token is configured. Unknown `platform` strings map to `Platform.OTHER`.

Each parsed job is normalised into a `JobPosting` with:

- `employment_type` inferred from text/tags: `part_time`, `contract`,
  `internship`, else `full_time` (a structured `job_type`/`commitment`/
  `employmentType` field wins);
- `remote_policy`: `remote` | `hybrid` | `onsite`;
- `timezone` when hinted (`GMT+7`, `UTC-5`, `WIB`, `PST`, ...);
- `currency` / `salary_period` when the salary string is parseable;
- `requirements` from tags + a short description excerpt;
- `hours_per_week` when a `20 hours/week` style phrase is present;
- a publication date in `extra` for `max_age_days`.

### Lane 2 — Ego Lite (signed-in, human-supervised) — `boards.py`

`job_agent.discovery.boards` **never scrapes**. It only supplies `BoardSpec`
URL templates + CSS selector hints consumed by the Ego Lite browser lane,
which drives the *person's own* signed-in browser. This is the lane for boards
that require login, CAPTCHA handling, or region-specific rendering
(LinkedIn, JobStreet, Glints, ...). A human stays in the loop; the agent does
not submit anything unattended.

```python
from job_agent.discovery import load_board_specs
for b in load_board_specs("config/feeds.yaml"):     # reads the `boards:` list
    url = b.search_url.format(query="product+designer", location="Jakarta")
    # Ego Lite navigates `url`, then uses b.card_selector / title_selector / ...
```

Every `search_url` contains `{query}` and `{location}` placeholders. Remote /
part-time friendly flags are already included where the site supports them
(e.g. LinkedIn `f_WT=2` = Remote, `f_JT=P` = Part-time; Seek
`worktype=parttime`; Glints `workArrangement=REMOTE`).

### Lane 3 — Playwright (fallback)

The existing per-platform Playwright drivers (`platforms/linkedin`,
`indeed`, `glassdoor`, `dice`, `wellfound`, ...) remain the last-resort lane
for anything the fast lane and Ego Lite cannot reach. They are heavier, need
browser state, and are rate-limited per platform. Prefer the fast lane for
breadth and Ego Lite for signed-in boards.

---

## 2. Supported boards

`default_board_specs()` ships ten entries (also duplicated, editable, in
`config/feeds.yaml`):

| Board | `platform` | Region / focus | Remote / part-time flags |
| --- | --- | --- | --- |
| LinkedIn | `linkedin` | Global | `f_WT=2` (remote), `f_JT=P` (part-time) |
| Wellfound | `wellfound` | Startups | `remote=true` |
| JobStreet Indonesia | `jobstreet` | SEA (ID) | `workFromHome=true` |
| JobStreet | `jobstreet` | SEA (SG/MY/PH) | `workFromHome=true` |
| Glints | `glints` | SEA | `workArrangement=REMOTE` |
| Kalibrr | `kalibrr` | SEA (ID/PH) | `remote=true` |
| Seek | `seek` | AU/NZ | `worktype=parttime` |
| RemoteOK | `remoteok` | Remote-first | remote-only by definition |
| We Work Remotely | `other` | Remote-first | remote-only by definition |
| Otta | `other` | Curated | `remote=true`, `employmentType=part_time` |

> `We Work Remotely` and `Otta` have no dedicated `Platform` enum member, so
> their `platform` is `other` → `Platform.OTHER`. This is expected.

### How to add a board

Add an entry under `boards:` in `config/feeds.yaml` (or append to
`default_board_specs()` in code):

```yaml
- name: My Board
  platform: other                 # unknown -> Platform.OTHER
  search_url: "https://example.com/jobs?q={query}&loc={location}&remote=true"
  card_selector: "article.job-card"
  title_selector: "h2"
  company_selector: ".company"
  location_selector: ".location"
  link_selector: "a.job-link"
  enabled: true
```

Rules: `name`, `platform`, `search_url` are required; `search_url` **must**
contain `{query}` and `{location}`; selectors are CSS run via
`document.querySelectorAll` in the Ego page. `load_board_specs()` skips
incomplete entries with a warning rather than raising. Re-verify selectors
against the live DOM when a site changes its markup.

> Two config files, on purpose: **`config/feeds.yaml`** holds the structured
> `feeds:`, `boards:` and `gigs:` definitions and is wired as
> `feeds.config_path`; **`config/discovery_sources.yaml`** holds `ego_boards:`
> overrides for the Ego Lite lane. `load_feed_specs()` / `load_board_specs()`
> fall back to complete built-in defaults when a file is missing or has no
> matching key, so functionality never depends on the YAML surviving.

---

## 3. Filter semantics — `matching/filters.py`

`build_filters(profile)` reads `profile["search"]["filters"]` (or
`profile["filters"]`, or flat top-level keys) and normalises it into a dict
consumed by `apply_filters(postings, filters)` → `FilterResult(kept, rejected)`
where `rejected` is a list of `(posting, reason)` tuples. All rules are
deterministic and **fail-open**: missing/malformed values keep a posting rather
than dropping it; `filters=None` keeps everything.

### Normalised keys and rules

| Key | Rule |
| --- | --- |
| `employment_types` | When non-empty, a **known** employment type must be in the list. Unknown (`""`) is kept by default ("treat as unknown") and only rejected when `require_known_employment_type` is true. Reason: `employment_type=full_time not in ['part_time', 'contract']` |
| `require_known_employment_type` | When true, reject postings with no employment type |
| `remote_only` | Accept if `remote is True` **OR** `remote_policy` ∈ `allowed_remote_policies` **OR** "remote" appears in title/description; else reject |
| `allowed_remote_policies` | A known `remote_policy` not in the list rejects (an explicit `remote=True` overrides it) |
| `timezone_alignment` | e.g. `"GMT+7 ±4"` → base offset +7, allowed delta 4h. A parseable posting `timezone` outside that band rejects. Unknown posting timezones are kept |
| `min_hourly_rate` | Reject when a derivable hourly rate is below it; unknown pay is kept |
| `min_annual_salary` | Reject when a derivable annual salary is below it; unknown pay is kept |
| `max_hours_per_week` | Reject when `hours_per_week` exceeds the cap |
| `exclude_keywords` | Reject if any appears in the searchable text |
| `exclude_companies` | Case-insensitive substring match on company rejects |
| `include_keywords` | At least one keyword must appear, else reject |
| `max_age_days` | Reject when a parseable publication date (from `extra`) is older; missing dates are kept |

`score_employment_fit(posting, filters)` returns a 0..1 ranking score (not a
hard filter) based on employment type, remote fit, include keywords and
timezone alignment. It never raises.

Aliases are normalised: `part-time` / `part time` → `part_time`,
`full-time` → `full_time`, `intern` → `internship`, `freelance` → `contract`.
Timezone parsing understands `GMT+7`, `UTC-5`, and named zones including
`WIB` (Jakarta, +7), `PST`, `EST`, `CET`, `JST`, `SGT`.

### Worked example — Jakarta GMT+7 design engineer, part-time remote

Profile (`config/profiles/*.yaml`):

```yaml
search:
  queries: ["product designer", "ux engineer", "design systems"]
  locations: ["Jakarta, Indonesia", "Remote"]
  filters:
    employment_type: "part-time"          # normalised to ["part_time"]
    remote_only: true
    allowed_remote_policies: ["remote", "hybrid"]
    timezone_alignment: "GMT+7 ±4"        # Jakarta +/- 4h overlap
    min_hourly_rate: 20
    max_hours_per_week: 30
    include_keywords: ["figma", "design system", "product design"]
    exclude_keywords: ["clearance required", "on-call 24/7"]
    exclude_companies: ["Acme Staffing"]
    max_age_days: 30
```

Wiring:

```python
from job_agent.matching import build_filters, apply_filters
f = build_filters(profile)
# f["timezone_offset"] == 7.0, f["timezone_delta"] == 4.0,
# f["employment_types"] == ["part_time"]

result = apply_filters(postings, f)
for job, reason in result.rejected:
    print("rejected:", job.title, "->", reason)
```

What passes: a `part_time`, `remote` (or `hybrid`), `WIB`/`GMT+7`-hinted,
`$30/hr`, 20 h/week Figma role. What gets rejected and why:

- `Part-time Onsite Designer (Jakarta)` → `remote_only: not remote (...)`;
- `Full-time Remote Engineer` → `employment_type=full_time not in ['part_time']`;
- `Part-time Remote Role, EST` → `timezone='EST' outside GMT+7 ±4`;
- `Part-time Remote Designer $10/hr` → `hourly_rate=10.00 < min_hourly_rate=20.00`;
- `Senior 40 h/week Contract Designer` → `hours_per_week=40 > max_hours_per_week=30`.

Unknown employment types, unknown timezones and unknown pay are deliberately
**kept** so a sparse feed posting is never silently discarded.

---

## 4. The AI-training gig lane (Alignerr-style)

### Why it is a different workflow

Alignerr, Outlier, DataAnnotation and Mercor are **expert networks** that pay
people to train / evaluate AI models. There is no job application to submit:

- Onboarding is **account + assessment**: create an account, pass a
  qualification/assessment, then get activated on a project.
- Pay is **hourly or per-task**, availability is **country-dependent**.
- You must **NEVER send a resume or cover letter** to a gig network. This lane
  is deliberately kept out of the application pipeline (`GigService` has no
  `apply`, `resume`, or `cover_letter` path).

They are stored separately too: the `GigOpportunity` table has its own
`GigStatus` lifecycle (`DISCOVERED → ELIGIBLE → APPLIED → ASSESSMENT → ACTIVE`
...), independent of `Job` / `Application`.

### Sources — `gigs/networks.py`

- `AlignerrNetwork` GETs `https://www.alignerr.com/jobs` with httpx and parses
  it with `parse_alignerr_html(html)`. The parser defensively handles JSON-LD,
  `__NEXT_DATA__`-ish JSON blobs, and simple HTML anchors matching
  `/jobs/<uuid>`, extracting titles, `$NN-NN/hr` pay ranges, "Remote" and short
  snippets. **Nothing is hardcoded** — role counts and rates are parsed from
  the page. On request failure or an empty parse it returns `[]` and logs a
  warning; it never raises.
- `track` classification: `coding` (developer / software / coding / engineer),
  `audio` (voice / audio / transcription), else `general`.
- `OutlierNetwork`, `DataAnnotationNetwork`, `MercorNetwork` are **stubs** that
  return `[]` with a "manual configuration required" warning. They have no
  stable public feed; do not invent openings for them. `get_network(name)`
  returns these (or a generic stub for unknown names) and never raises.

### Refresh + eligibility — `gigs/service.py`

```python
from job_agent.config import Settings
from job_agent.gigs import GigService

settings = Settings(_env_file=None)
service = GigService(settings, session=None)          # session=None = no DB writes
counts = service.refresh()                             # {"discovered","eligible","skipped","errors"}
rows = service.to_rows(service.eligible())             # push to the gig sheet
```

- `refresh(networks=None)` reads `settings.gigs.networks` (default
  `["alignerr"]`), fetches each network, dedupes, computes eligibility and
  optionally upserts into `GigOpportunity` when a `session` is supplied.
  It **works with `session=None`**: listings are kept in memory for
  `eligible()` / `to_rows()` and no DB writes occur. A failing network
  increments `errors` and never aborts.
- `upsert(listings, session)` is idempotent by `(network, external_id)` using
  `select(GigOpportunity).where(...)` and commits exactly once. New rows are
  `ELIGIBLE` when they passed the filter, else `DISCOVERED`; existing rows
  keep their status (so `APPLIED` / `ACTIVE` is not overwritten).
- `to_rows()` emits sheet-ready dicts keyed to the gig sheet. The canonical
  upsert identity is `gig_key = "<network>:<external_id>"` (e.g.
  `alignerr:c1`); `SheetSync.push_gigs()` can also derive it from `network` +
  `external_id`. `decision` and `notes` are **human-owned** sheet columns:
  emitted blank and never overwritten by the sheet backend. Extra ORM columns
  (`external_id`, `description`, `location`, `discovered_at`) are preserved as
  additional header columns for lossless round-tripping. `status` is the
  `GigStatus` `.value` string (`eligible` / `discovered`). Note: the canonical
  `SheetSync.sync_from_session()` mirror reads `GigOpportunity` ORM objects
  directly via `gig_to_row()`, so it does not depend on `to_rows()`.

**Eligibility rules:**

- keep `coding` / `general` / `design` tracks; drop `audio` and voice-only
  roles (audio keywords with no coding/design relevance);
- apply `settings.gigs.min_hourly_rate` by comparing `pay_min` **or** `pay_max`
  (only for hourly-denominated pay; unknown pay is kept leniently);
- respect `settings.gigs.remote_only`.

Rates and availability are parsed from the live page and vary by role and
country — verify on the network's site. The assessment is taken on the
network's own platform; `job-agent` does not administer it.

---

## 5. Compliance & rate-limit guidance

- **Be polite.** The feed client uses a descriptive `User-Agent`, a 12s default
  timeout, `follow_redirects=True`, bounded concurrency (`max_workers=6`) and
  tolerates per-feed failures. Keep concurrency low and add delays between
  runs; do not hammer endpoints.
- **Respect robots.txt / ToS.** Some sites (notably LinkedIn guest endpoints)
  rate-limit or block automated access. The fast lane uses only public,
  documented JSON endpoints. When a source starts returning 429/403, disable
  it in `config/feeds.yaml` or move it to the Ego Lite lane.
- **Human supervision for signed-in boards.** Ego Lite drives the person's own
  browser and keeps a human in the loop; never automate submissions on boards
  that require login or present CAPTCHAs.
- **Gigs ≠ applications.** No CV / cover letter is ever sent to an expert
  network, and gig onboarding (account + assessment) happens on the network's
  own site under the user's control.
- **No secrets in config.** Board tokens are public slugs, but never commit
  account credentials. `.env` / `Settings` hold secrets.

---

## 6. Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| `discover()` returns `[]` | No network, or all feeds disabled/failing. Check `hub.results()` — each `FeedResult.error` tells you which feed failed. `load_feed_specs()` returns built-in defaults for a missing file. |
| A feed always errors | Bad `url`/token, 429/403, or endpoint changed. Set `enabled: false` for that feed, or fix `url`. For Greenhouse/Lever/Ashby supply a real board token. |
| Filters drop everything | `include_keywords` too strict, `remote_only` with sparse `remote` flags, or `min_hourly_rate` too high. Filters are fail-open on *unknown* values but will reject *known* bad values — inspect `FilterResult.rejected` reasons. |
| Timezone filter not firing | Posting `timezone` is only parsed when it looks like `GMT+7` / `UTC-5` / a known zone. Unknown posting timezones are kept by design. |
| Greenhouse/Lever/Ashby errors | Token missing → the parser raises, which is caught and recorded as a non-fatal `FeedResult.error`. Set `FeedSpec.url` to the token and `enabled: true`. |
| `parse_alignerr_html` returns `[]` | Markup changed (new JSON blob / no `/jobs/<uuid>` anchors / no `$NN-NN/hr` text). The parser has three strategies + a regex pay fallback; check the logged `alignerr_parse_no_roles` warning and extend a strategy. It never raises. |
| Gigs sheet has no rows | `refresh()` was called with `session=None` (no DB writes is expected) — use `service.to_rows(service.eligible())` to push rows, or supply a session for persistence. |
| `config/feeds.yaml` edits ignored | Check `feeds.config_path` in `config/default.yaml` (it should be `config/feeds.yaml`), and make sure the entry sits under the `feeds:` key with `enabled: true`. |
```
