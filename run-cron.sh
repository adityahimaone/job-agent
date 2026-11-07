#!/usr/bin/env bash
# job-agent cron run: no ego(lite), headless lanes + feeds only.
# Started from crontab every 6h. Logs rotate by date under ~/logs.
set -uo pipefail
cd /home/adityahimaone/apps/job-agent || exit 1
LOG=/home/adityahimaone/logs/job-agent-cron.log
{
  echo "=== $(date -Is) start ==="
  /home/adityahimaone/apps/job-agent/.venv/bin/job-agent -c config/cron.yaml \
    run -p config/profiles/aditya.yaml --dry-run --once
  rc=$?
  echo "=== $(date -Is) run done (exit $rc) ==="
  # results surface in the 'hermes' research tab, not in jobs/gigs tabs
  PYTHONPATH=src /home/adityahimaone/apps/job-agent/.venv/bin/python \
    scripts/sync_hermes_tab.py
  echo "=== $(date -Is) sheet done (exit $?) ==="
} >> "$LOG" 2>&1
# keep the log bounded
tail -n 5000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"