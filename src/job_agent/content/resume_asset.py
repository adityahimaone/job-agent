"""Canonical English resume PDF for email attachments.

Source of truth is the published resume (adityahimaone.space/resume.pdf), not the
generated template — applications always attach the same English resume.
Cached under ~/.job-agent/canonical_resume.pdf; refreshed when older than 7 days.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

RESUME_URL = "https://adityahimaone.space/resume.pdf"
CACHE = Path.home() / ".job-agent" / "canonical_resume.pdf"
MAX_AGE_S = 7 * 24 * 3600


def canonical_resume(force: bool = False) -> tuple[str, bytes]:
    """Return (filename, pdf bytes). Falls back to the cache if the site is down."""
    fresh = CACHE.exists() and (time.time() - CACHE.stat().st_mtime) < MAX_AGE_S
    if force or not fresh:
        try:
            resp = requests.get(RESUME_URL, timeout=30)
            resp.raise_for_status()
            if resp.content[:4] == b"%PDF":
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_bytes(resp.content)
        except Exception:
            if not CACHE.exists():
                raise
    return "Aditya_Himawan_Resume.pdf", CACHE.read_bytes()