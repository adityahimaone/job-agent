# Muse-primary / Ego-fallback discovery

Muse is the primary job-search lane; Ego Lite on the Mac is the fallback.
This document is the in-repo half of that design. The cron that drives it
is specified in `docs/MUSE_CRON.md`.

## Why Muse first

| | Muse (primary) | Ego Lite (fallback) |
|---|---|---|
| Who searches | The Muse agent, with its own browser/search | ego(lite) browser on the Mac, driven over SSH/Tailscale |
| Session | Muse's signed-in LinkedIn session when available | Guest-only; sign-in/apply hits reCAPTCHA / password checkpoints |
| Mac needed | No | Yes — the Mac must be online |
| IP | Cloud / VPS egress | Same Tencent Singapore IP as the VPS (the Mac's egress is a VPN, not a residential IP — verified 2026-11-11) |
| Detection risk | Lower: human-paced, signed-in browsing | Higher: scripted driving, guest pages |

Two findings from 2026-11-11 shape this: the Mac has **no residential IP**
to gain from a proxy, and Muse's cloud browser **cannot be pointed at a
custom proxy** — so job-agent code cannot call Muse's browser directly.
The Muse lane is therefore driven from *outside* job-agent (a Muse cron
run) that writes a jobs file; job-agent imports it through the fusion lane.

## Config — `discovery:` in `config/default.yaml`

```yaml
discovery:
  primary: muse            # lane tried first
  fallback: ego            # lane used only when the primary yields nothing new
  fallback_enabled: true   # master switch for the fallback
  fallback_on_zero_new: true  # trigger: Muse import yielded zero NEW jobs
  muse_queries: [...]      # queries the Muse cron run should use
  muse_locations: [...]    # ...and locations (SEA-first, remote-first)
  ego_queries: [...]       # queries the Ego fallback should use
  ego_locations: [...]
  ego_platforms: [linkedin, dealls]
  ego_limit_per_query: 10
```

`Settings.discovery` is a `DiscoveryConfig` (pydantic-settings, same style
as the other sections). `resolve_lane_order(settings)` returns the ordered
lanes, e.g. `["muse", "ego"]`; Ego is dropped from the order when
`fallback_enabled` is false or `ego.enabled` is false. Unknown lane names
are dropped, never fatal.

## Entry point — `job-agent muse run`

```bash
# Normal run: import the Muse file; fall back to Ego only if it yields
# zero new jobs. Import is QUEUED-only, never applies.
job-agent muse run /tmp/muse_jobs_2026-11-11.json \
  --profile config/profiles/aditya.yaml --with-ego-fallback

# Dry run first: parse + dedup + score in memory, write nothing.
# The Ego fallback is REPORTED, never executed, in dry-run mode.
job-agent muse run /tmp/muse_jobs_2026-11-11.json \
  --profile config/profiles/aditya.yaml --dry-run --with-ego-fallback

# Muse file missing or empty? That counts as zero new jobs, so the
# fallback triggers (when --with-ego-fallback is passed and Ego is enabled).
job-agent muse run /tmp/does-not-exist.json --with-ego-fallback
```

The fallback is **opt-in per run** (`--with-ego-fallback`). Without the
flag, a zero-new Muse import just ends with a report — Ego never runs
silently. When it does run, it calls the existing `discover_jobs` per
platform/query/location (search + store as DISCOVERED), never an applicator,
and `ego.require_human_submit` stays true.

## Fallback rule, exactly

Ego runs only when **all** of these hold:

1. `--with-ego-fallback` was passed, and
2. `discovery.fallback_enabled` and `discovery.fallback_on_zero_new` are true, and
3. `ego.enabled` is true (the Mac bridge is up), and
4. the Muse import yielded **zero new jobs** — `imported == 0` on a real
   run, `would_import == 0` on a dry run. Rows that were duplicates or
   cross-platform duplicates do not count: job-agent already knew them.

If Muse imports even one new job, Ego does not run. Every outcome is
printed with a reason (`fallback_reason`), so a skipped fallback is always
explained.

## What this does NOT do

* No LinkedIn sign-in, no form submission, no checkpoint retry. An Ego
  search that hits a checkpoint returns no rows; the run reports it.
* No auto-apply. Imported jobs are decided with `require_approval=True`;
  the best outcome is QUEUED. Ego fallback stores jobs as DISCOVERED for
  the next pipeline run to score.
* The 6-hour `run-cron.sh` feed cron is untouched — this lane is driven by
  the Muse cron in `docs/MUSE_CRON.md`, not by `run-cron.sh`.

## Tests

`tests/unit/test_muse_primary_ego_fallback.py` covers: config defaults and
parsing, `new_muse_jobs` (real vs dry-run), the fallback trigger on zero
new / empty / missing results, no fallback when Muse yields jobs, fallback
disabled by config or by Ego being disabled, lane-order resolution, and
`run_muse_first` (Ego called exactly once on fallback, never on dry-run,
never when disabled).
