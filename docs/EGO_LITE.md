# ego(lite) browser lane

job-agent can drive the **ego(lite)** browser that is already open and logged in
on your own machine, instead of launching its own Playwright/Camoufox browser.
That makes it a fast, low-detection lane for discovery and, with a human in the
loop, for submitting applications.

> Official quick-start: <https://lite.ego.app/document/en/docs/quick-start>
> The official docs are currently **Windows-focused**; this integration targets
> the **macOS build** (`/Applications/ego lite.app`). The `ego-browser skill`
> API (v2.2.0 macOS) is what the code uses.

---

## 1. Install / verify

The bridge talks to the `ego-browser` helper shipped *inside* ego(lite). It is
not on `PATH` by default. Verify the helper and the skill API:

```bash
BINARY="/Applications/ego lite.app/Contents/Frameworks/ego Framework.framework/Versions/Current/Helpers/ego-browser"

"$BINARY" --help        # shows: nodejs -e <script> / nodejs < script.js
"$BINARY" skill         # prints the v2.2.0 usage guide (the API we use)
"$BINARY" nodejs -e 'console.log(process.version)'
```

From job-agent, prove the whole bridge end to end (opens `example.com`, reads
the title, and reports the Node version + binary path):

```bash
job-agent ego doctor
```

It should print `ego bridge OK`. `EgoClient.version()` reports the embedded
Node.js version with a short 20s timeout; `EgoClient.doctor()` also returns the
resolved `binary` path for troubleshooting.

### macOS vs Windows

| | macOS build (this integration) | Windows (official docs) |
|---|---|---|
| Helper path | `/Applications/ego lite.app/Contents/Frameworks/ego Framework.framework/Versions/Current/Helpers/ego-browser` | inside the ego(lite) install dir, on `PATH` differently |
| Binary resolution | `Versions/Current` symlink + `Versions/*` glob fallback | handled by the installer |
| `console.log` stream | **stderr** (the client reads both streams) | stderr |

The Python client is platform-agnostic: on a remote host it runs the helper over
SSH (`ego.host` / `ego.user`), otherwise locally via `bash -lc`.

---

## 2. Binary resolution & version updates

`resolve_ego_binary(configured)` (in `client.py`) looks, in order:

1. `ego.binary` if set and executable;
2. `Versions/Current/Helpers/ego-browser` — the **default**, tracks updates;
3. the highest `Versions/*/Helpers/ego-browser` found on disk;
4. `shutil.which("ego-browser")` (a `PATH` install);
5. the legacy pinned `0.6.2.8` path.

`DEFAULT_BINARY` is now the `Versions/Current` path, so an ego(lite) update that
moves the symlink no longer breaks the app. If none is usable, an `EgoError`
lists everything it tried — install ego(lite), put `ego-browser` on `PATH`, or
set `ego.binary`. After an ego(lite) upgrade, re-read the skill guide
(`ego-browser skill`) in case the API changed.

---

## 3. Configuration

In `config/default.yaml` (override via `--config` or `JOB_AGENT_EGO__*` env
vars):

```yaml
ego:
  enabled: false              # master switch for the whole lane
  host: ""                    # empty = run ego-browser locally; else SSH user@host
  user: ""
  binary: ""                  # empty = auto-resolve Versions/Current
  timeout: 180                # seconds per nodejs invocation
  task_space: "job-agent"     # one named ego task space reused for the whole run
  settle_ms: 2500             # wait after navigation before scraping
  apply_enabled: true         # kill switch for applications
  require_human_submit: true  # NEVER auto-click the final submit (recommended)
  board_config_path: "config/discovery_sources.yaml"
```

Per platform, opt in:

```yaml
platforms:
  linkedin:
    use_ego: true
  wellfound:
    use_ego: true
```

The lane is used only when **both** `ego.enabled: true` and
`platforms.<board>.use_ego: true`. `EgoDriver.requires_login = False` — there is
no credential to replay because ego(lite) already holds your session.

CLI smoke tests:

```bash
job-agent ego doctor
job-agent ego search --platform linkedin --query "frontend developer" --limit 10
job-agent ego search --platform wellfound --query "python" --location "Remote"
```

---

## 4. The human handoff workflow (login / 2FA / CAPTCHA / final submit)

Applications are **human-supervised by default** (`require_human_submit: true`):

1. The agent opens the posting, takes a snapshot, fills only the fields you
   explicitly supplied, and attaches the resume (and cover letter when a second
   file input exists) via `page.setInputFiles` with **absolute paths**.
2. It calls `task.handOff()` and **ends the round**. You are told (in the logs /
   CLI) to: finish any login or 2FA, solve a CAPTCHA if present, review the form,
   and click the final **Submit** yourself.
3. In a later round the agent resumes the **same named task space** with
   `task.waitForControl()`, takes a final snapshot, and looks for confirmation
   text.
4. It records an honest outcome (below). It **never clicks submit** in this mode
   and **never auto-clicks hCaptcha/reCAPTCHA**.

Rules the code enforces:

- **No fabricated answers** — only explicit fields are filled; a bad selector
  is logged and skipped.
- **Never claim success without evidence** — a confirmation keyword must appear
  in the final snapshot.
- Outcome statuses (`EgoApplyResult.status`, also on `EgoDriver.last_result`):
  - `confirmed` — confirmation text found; `apply()` returns `True`.
  - `pending_human` — handoff mode, no confirmation yet (you may still be
    working); `apply()` returns `False`.
  - `unverified` — automated submit attempted but no confirmation found.
  - `failed` — failure keywords found, or a bridge/config error.
- `verify_driver_result()` in the orchestrator turns `pending_human` into a
  `PENDING_HUMAN` database state instead of a false "applied".

If you deliberately set `require_human_submit: false`, the agent will click a
submit-like control and verify via snapshot — but it still refuses to click
anything inside a captcha container/iframe and still returns `False` without
confirmation text. This is **not recommended** for real accounts.

> Each `EgoSession` method spawns a fresh `ego-browser nodejs` process. Batch
> related actions into one script (`EgoSession.run_batch` / a composed script)
> when adding new flows — don't chain dozens of single-action calls.

---

## 5. Supported boards & adding one

Built-in boards (`default_board_specs()`): **linkedin, wellfound, jobstreet,
glints, kalibrr, seek**. `linkedin` uses the fast guest JSON-HTML endpoint; the
rest use a generic `page.evaluate(document.querySelectorAll)` snapshot path.
Unknown `platform:` values map to `Platform.OTHER`, results are deduplicated by
URL and tagged `source="ego:<name>"`.

Selectors are best-effort defaults. Override or add a board in
`config/discovery_sources.yaml` under `ego_boards` (see the commented example in
that file):

```yaml
ego_boards:
  myboard:
    platform: other
    search_url: "https://example.com/jobs?q={query}&loc={location}"
    card_selector: "article.job-card"
    title_selector: "h2"
    company_selector: ".company"
    location_selector: ".location"
    link_selector: "a.job-link"
    remote_hint: "remote"
```

- `search_url` **must** contain `{query}` and `{location}` (URL-encoded for you).
- Selectors are CSS, evaluated **inside the browser page**.
- Only fields you set are required; the rest fall back to the built-in default.
  A brand-new board needs at least `search_url` + `card_selector`.
- `load_board_specs()` always returns the six built-ins plus any overrides, so
  a file shared with the structured-feeds feature (no `ego_boards` key) is safe.

`EgoDiscovery.search_all(queries, locations, limit)` batches many searches into
**one** `nodejs` invocation and deduplicates results; `EgoDiscovery.close()`
finishes the task space (safe to call twice).

---

## 6. Safety limits

- `ego.enabled` and `platforms.<board>.use_ego` are both required.
- `ego.apply_enabled` is a separate kill switch for submissions.
- `require_human_submit: true` by default (and forced on when the setting is
  absent) — the agent never clicks the final submit by default.
- Global safety still applies: `agent.dry_run`, `agent.require_approval_for_all`,
  per-platform `max_applications_per_day`, and the human approval surfaces
  (dashboard / sheets / bot).
- `ego.timeout` bounds every `nodejs` round; `settle_ms` bounds scrape waits.
- No credentials are stored or replayed; the human's live session is reused.

---

## 7. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `ego-browser gave no __RESULT__ line` | ego(lite) not running, or the helper failed. Run `ego-browser --help` and `job-agent ego doctor`. The client reads `__RESULT__` from **stderr** as well as stdout. |
| `ego-browser binary not found` | Wrong/old path. Let `ego.binary` be empty so `Versions/Current` is used; check `resolve_ego_binary` search list in the error. |
| Doctor times out | `example.com` unreachable, or ego(lite) busy. Increase `ego.timeout`; check the skill guide for an available update. |
| `wellfound`/`jobstreet` return 0 rows | Best-effort selectors drift. Override `card_selector`/etc. in `config/discovery_sources.yaml`; take a `page.snapshot({scope:"full_page"})` and adjust. |
| `pending_human` forever | The agent is waiting for you to return control after `handOff()`. Finish in the browser and hand control back; the same task space is resumed. |
| `unverified` after submit | No confirmation keyword in the snapshot. Verify manually in ego(lite); the DB will not claim success. |
| Login / 2FA / CAPTCHA blocking | Expected — these are human steps. The agent hands off and never solves them. |
| Stale behavior after upgrade | Re-run `ego-browser skill`; the API is versioned (v2.2.0 macOS). |

Useful probes:

```bash
"$BINARY" --version
"$BINARY" nodejs -e 'console.log("__RESULT__" + JSON.stringify({node: process.version}))'
"$BINARY" skill | head -40
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_ego_bridge_v2.py -q   # offline unit tests
```
