# Sheet templates

These are **header-only** CSV files. They are reference templates: copy them
into your configured sheets directory (default `~/.job-agent/sheets/`) or run
`job-agent sheet template` to generate them there. The agent writes one row per
job / application / gig into these files; you edit the `decision` column to
approve or reject a job.

The header order must match `SHEET_SPECS` in `job_agent.integrations.spreadsheet`.
You may **add your own columns** (e.g. `priority`, `owner`) — the agent keeps
any extra columns and the rows you add. Do not remove or rename the built-in
columns.

## `jobs.csv`

| Column | Meaning |
| --- | --- |
| `job_key` | Stable id, format `<platform>:<external_id>`. Do not edit. |
| `platform` | Source platform (linkedin, indeed, …). |
| `title`, `company`, `location` | Job basics. |
| `employment_type`, `remote_policy`, `timezone`, `hours_per_week` | Structured employment metadata. |
| `salary`, `url` | Compensation text and job link. |
| `score`, `match_reason` | AI match score and reasoning (blank if not scored). |
| `status` | Current DB status (discovered, queued, approved, …). |
| `decision` | **Your input.** See "Allowed decision values" below. |
| `notes` | **Your free-text notes.** Never overwritten by the agent. |
| `discovered_at` | ISO timestamp of discovery. |

## `applications.csv`

| Column | Meaning |
| --- | --- |
| `job_key` | Links back to the job (`<platform>:<external_id>`). |
| `company`, `title` | Copied from the related job. |
| `applied_at` | ISO timestamp of submission. |
| `channel` | Platform the application was sent through. |
| `resume_path`, `cover_letter_path` | Generated document paths. |
| `verification_status`, `status` | Submission verification + application status. |
| `outcome`, `follow_up_at`, `notes` | Human-tracking columns (agent leaves them blank). |

## `gigs.csv`

| Column | Meaning |
| --- | --- |
| `gig_key` | Stable id, format `<network>:<external_id>`. Do not edit. |
| `network`, `title`, `track` | Expert network and role. |
| `pay_min`, `pay_max`, `currency`, `pay_period` | Pay range and period. |
| `employment_type`, `remote`, `url` | Engagement metadata. |
| `eligibility`, `status` | Country/eligibility text and gig status. |
| `decision` | **Your input.** Same values as `jobs.csv`. |
| `notes` | **Your free-text notes.** Never overwritten by the agent. |

## `search_profiles.csv`

Optional input sheet for describing search profiles (`name`, `keywords`,
`locations`, `employment_types`, `remote_only`, `min_hourly_rate`,
`max_hours_per_week`, `timezone_alignment`, `enabled`). It is human-authored;
the agent only reads it (`read_search_profiles()`), it never writes to it.

## Allowed decision values

Values are case-insensitive and whitespace-trimmed.

| You type | Canonical |
| --- | --- |
| `approve`, `approved`, `yes`, `y` | `approve` → job becomes `APPROVED` |
| `reject`, `rejected`, `no`, `n` | `reject` → job becomes `REJECTED` |
| `skip`, `hold` | `skip` → job becomes `SKIPPED` |
| blank / anything else | ignored (no change) |

After editing, run `job-agent sheet pull` to write decisions into the database,
then `job-agent apply-approved` to submit approved jobs.
