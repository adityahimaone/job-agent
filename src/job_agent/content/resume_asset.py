"""Canonical English resume PDF for email attachments.

Source of truth is the published resume, not the generated template — applications
always attach the same English resume. Sources are tried in order (portfolio site
first, Google Drive as fallback). Cached under ~/.job-agent/canonical_resume.pdf;
refreshed when older than 7 days.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

SOURCES = (
    "https://adityahimaone.space/resume.pdf",
    # Fallback: survives a portfolio redeploy that drops /resume.pdf.
    "https://drive.google.com/uc?id=1Vq0a4tlHm4N0BVWSjitHHcppUvX5J_09&export=download",
)
CACHE = Path.home() / ".job-agent" / "canonical_resume.pdf"
MAX_AGE_S = 7 * 24 * 3600


def _download(url: str) -> bytes | None:
    try:
        resp = requests.get(url, timeout=30, allow_redirects=True)
        resp.raise_for_status()
        # Drive answers with application/octet-stream, so trust magic bytes.
        if resp.content[:4] == b"%PDF":
            return resp.content
    except Exception:
        pass
    return None


def canonical_resume(force: bool = False) -> tuple[str, bytes]:
    """Return (filename, pdf bytes). Raises only if every source and the cache fail."""
    fresh = CACHE.exists() and (time.time() - CACHE.stat().st_mtime) < MAX_AGE_S
    if force or not fresh:
        for url in SOURCES:
            data = _download(url)
            if data:
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_bytes(data)
                break
        else:
            if not CACHE.exists():
                raise RuntimeError(f"resume unavailable from all sources: {SOURCES}")
    return "Aditya_Himawan_Resume.pdf", CACHE.read_bytes()
