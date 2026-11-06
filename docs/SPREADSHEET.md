# Spreadsheet integration (human approval surface)

The spreadsheet integration is a **bidirectional mirror** between the job-agent
database and a human-editable sheet. The agent pushes discovered jobs, submitted
applications and gig opportunities into the sheet so you can review them in a
familiar tool. More importantly, the sheet is the **approval surface**: you
approve or reject a job by editing a `decision` column, then pull that decision
back into the database.

The CSV backend works out of the box with **no extra dependencies**. Google
Sheets is an optional backend.

## Quick start

```bash
# 1. Create the header-only CSV files in the configured directory.
job-agent sheet template

# 2. Where are they?
job-agent sheet path

# 3. Mirror current DB rows into the sheet.
job-agent sheet push

# 4. Open the CSVs, set `decision` (see below), save.

# 5. Apply your decisions to the database.
job-agent sheet pull

# 6. Submit the jobs you approved.
job-agent apply-approved
```

`job-agent sheet decisions` prints the decisions found in the sheet **without**
changing the database — handy for a dry check before `pull`.

## Configuration

In `config/default.yaml` (or an override / `JOB_AGENT_SHEETS__*` env vars):

```yaml
sheets:
  enabled: true
  backend: "csv"            # "csv" | "google"
  directory: "~/.job-agent/sheets"
  spreadsheet_id: ""        # google backend only
  credentials_path: ""      # google backend only
  pull_decisions: true
```

`directory` is expanded (`~` → home) and created automatically
(`Settings.sheets_dir`).

## CSV backend (default)

One CSV file per sheet lives in `sheets.directory`:

| File | Key column | Purpose |
| --- | --- | --- |
| `jobs.csv` | `job_key` | Discovered jobs + `decision` / `notes`. |
| `applications.csv` | `job_key` | Submitted applications (read-mostly). |
| `gigs.csv` | `gig_key` | AI-training / expert-network gigs + `decision` / `notes`. |
| `search_profiles.csv` | `name` | Optional human-authored search profiles (read-only). |

Editable in **Excel, Numbers, LibreOffice**, or import the CSVs into **Google
Sheets**. Files are UTF-8 with a header row. The agent reads with
`utf-8-sig`, so a BOM added by Excel is handled transparently, and writes
plain UTF-8.

### The `decision` column workflow

1. `job-agent sheet push` writes/refresh the rows. Each row keeps a stable key
   (`job_key` / `gig_key`).
2. In the `jobs.csv` (or `gigs.csv`) `decision` column, type one of the allowed
   values (case-insensitive, whitespace-trimmed):

   | You type | Canonical | Effect on the job |
   | --- | --- | --- |
   | `approve`, `approved`, `yes`, `y` | `approve` | status → `APPROVED` |
   | `reject`, `rejected`, `no`, `n` | `reject` | status → `REJECTED` |
   | `skip`, `hold` | `skip` | status → `SKIPPED` |
   | blank / anything else | — | ignored, no change |

3. `job-agent sheet pull` reads the decisions and updates the database.
4. Approved jobs are picked up by `job-agent apply-approved` on its next run
   (the sheet is an approval source, not an applicator).

Only jobs currently in an **undecided** state are changed:
`QUEUED`, `AUTO_APPROVED`, `DISCOVERED`, `MATCHED`, `FILTERED`,
`PENDING_HUMAN`. Jobs already `APPROVED` / `REJECTED` / `SKIPPED` / `APPLIED`
etc. are left untouched (counted as `unchanged`). A decision for a key that
does not exist in the database is counted as `unknown` and ignored — no error.

`notes` is yours: the agent never overwrites it. Leave a decision on a row that
is not yet in the DB — it stays in the sheet and is simply ignored until the
job appears.

### `job_key` format

```
<platform>:<external_id>
```

For example `linkedin:3827194` or `remoteok:abc123`. `platform` is the lowercase
platform enum value and `external_id` is the platform's own id. The same key is
used in `applications.csv` to link an application back to its job. `gig_key`
uses `<network>:<external_id>` (e.g. `alignerr:987`). **Do not edit key
columns** — they are the identity used for matching.

`SheetSync.push_gigs()` also accepts rows shaped like the raw
`GigOpportunity` columns (i.e. carrying `network` + `external_id` but no
`gig_key`); it derives `gig_key = "<network>:<external_id>"` automatically.
Any extra columns on those rows are preserved by the upsert. Note that the
`notes` column is human review notes and is never overwritten by a push — it
is not the `GigOpportunity.notes` database field.

### Idempotent upsert rules

`push` uses an **upsert keyed on the key column**, not a blind rewrite:

- Existing rows (including rows you added yourself) are never deleted.
- A matching row is updated in place; the human-owned `decision` and `notes`
  columns are preserved (never overwritten).
- Extra columns you added (e.g. `priority`, `owner`) are preserved on every
  row, and new rows get empty values for them.
- Rows in the sheet with no matching DB row stay in the sheet.

So it is safe to re-run `push` any number of times: it is incremental and will
not clobber your edits.

### Incremental sync

`job-agent sheet push` mirrors up to `--limit N` rows (default 1000) per sheet,
ordered by most recent. It calls `ensure_templates()` first, so a fresh
directory is populated with header-only CSVs before the first upsert. To keep
the sheet current, schedule `sheet push` alongside your discovery runs, or run
it on demand. `pull` is cheap and safe to run frequently.

## Optional: Google Sheets backend

Google Sheets support is **optional** — no Google libraries are installed by
default, and the CSV backend is unaffected. The class is always importable; it
only raises a clear error when actually instantiated without its dependencies.

Setup:

1. Install the optional libraries (do **not** add them to `pyproject.toml`):

   ```bash
   uv pip install gspread google-auth
   ```

2. Create a **Google Cloud service account**, enable the Google Sheets API and
   Google Drive API, and download its JSON key. Store it outside the repo
   (e.g. `~/.job-agent/gsa.json`) and keep it private.

3. Create a spreadsheet and **share it with the service account's email**
   (the `client_email` in the JSON) as an Editor.

4. Configure:

   ```yaml
   sheets:
     backend: "google"
     spreadsheet_id: "<the id from the sheet URL>"
     credentials_path: "~/.job-agent/gsa.json"
   ```

5. Check availability with `job-agent sheet google-check`.

The backend opens the spreadsheet by id and uses one worksheet per sheet
(`jobs`, `applications`, `gigs`, `search_profiles`). `read` uses
`get_all_records()`; `upsert`/`write_all` rewrite the worksheet via
`append_row(s)` batching. Worksheet creation, header row, extra columns and
human `decision`/`notes` preservation follow the same rules as the CSV backend.

If the libraries are missing, instantiating the backend raises:

```
RuntimeError: Google Sheets backend requires: uv pip install gspread google-auth ...
```

## Adding custom columns safely

Add columns to the right of the built-in ones, in any order. The upsert reads
whatever header is present, keeps extra columns and their values, and only ever
writes the built-in columns plus any columns it finds. Example: add a `priority`
column and fill it per row; it will survive every `push`. Do **not** rename or
delete built-in columns — they are re-added on the next write, and removing a
key column breaks matching.

The `decision` and `notes` columns are special: they are always preserved. Use
`notes` for free-form review comments.

## Troubleshooting

- **`sheet push` shows 0 rows** — the DB is empty; run a discovery first
  (`job-agent run --profile ... --once`). Confirm with `job-agent sheet path` and
  check the CSVs in that directory.
- **My decisions were not applied** — check `job-agent sheet decisions` output.
  Make sure the `decision` value is one of the allowed words, the `job_key` is
  unchanged, and the job is still in an undecided status (`pull` reports
  `unchanged` / `unknown` counts).
- **Decisions keep coming back** — `pull` does not clear the `decision` cell;
  it re-applies on each run. Clear the cell yourself once a job is decided, or
  just leave it (re-applying to an already-decided job is a no-op counted as
  `unchanged`).
- **"Unknown sheet" / missing headers** — do not remove built-in columns. Run
  `job-agent sheet template` in a fresh directory, or restore the headers from
  `config/templates/sheets/`.
- **Excel added a BOM / weird first column** — the reader uses `utf-8-sig`, so
  this is handled. If a header still looks wrong, re-save as UTF-8 CSV.
- **Google backend errors** — confirm `google-check`, that the sheet is shared
  with `client_email`, and that `spreadsheet_id` / `credentials_path` are set.
- **`gigs` table errors on `push`** — older databases may lack the
  `gig_opportunities` table; the sync logs a warning and skips gigs. Run
  `job-agent init-db` to create all tables.

## Privacy

The sheet contains **personally identifiable information and your full
application history**: your profile, resume / cover-letter paths, where and
when you applied, and your review notes. With the CSV backend keep the files
**local** (default `~/.job-agent/sheets`) and do not commit them — they are
git-ignored via the data directory. With the Google backend, keep the
spreadsheet **private**, share it only with your service account, and store the
service-account JSON key outside the repository. Treat both as sensitive data.
