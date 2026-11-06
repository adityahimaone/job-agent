"""Heuristic grounding checks for generated documents.

This is a *heuristic*, not a guarantee. It flags suspicious URLs, email
addresses, numeric claims, and forbidden phrases so a human (or the generator's
AI guard) can review them. It never raises.
"""

from __future__ import annotations

import re

from job_agent.content.candidate import CandidateProfile

_URL_RE = re.compile(r"https?://[^\s)\]\"'<>]+", re.I)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_NUMERIC_CLAIM_RE = re.compile(
    r"\b\d+\+?\s*years?\b"
    r"|\b\d+[kKmM]?\+?\s*(?:users?|customers?|clients?|downloads?|engineers?|developers?)\b"
    r"|\b\d+\s*%",
    re.I,
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def _clean_url(url: str) -> str:
    return url.rstrip(".,;:!?)/").rstrip("/")


def check_grounding(text: str, profile: CandidateProfile) -> list[str]:
    """Return human-readable warnings about possibly ungrounded claims.

    Detects:
      - URLs not present in ``profile.links`` or the verified facts,
      - email addresses that differ from ``profile.email``,
      - numeric claims ("N years", "Nk+ users", "N users", "N%") not found in
        the verified facts, and
      - any phrase from ``profile.do_not_claim`` appearing in the text.
    """
    warnings: list[str] = []
    text = text or ""

    facts = list(getattr(profile, "facts", None) or [])
    links = dict(getattr(profile, "links", None) or {})

    # Known URLs = declared links + any URL embedded in a verified fact.
    allowed_urls: set[str] = set()
    for value in links.values():
        cleaned = _clean_url(str(value))
        if cleaned:
            allowed_urls.add(cleaned)
    for fact in facts:
        for found in _URL_RE.findall(str(fact)):
            cleaned = _clean_url(found)
            if cleaned:
                allowed_urls.add(cleaned)

    for found in _URL_RE.findall(text):
        url = _clean_url(found)
        if not url:
            continue
        if not any(
            url == known or url.startswith(known) or known.startswith(url)
            for known in allowed_urls
        ):
            warnings.append(f"URL not found in profile links or verified facts: {url}")

    profile_email = str(getattr(profile, "email", "") or "")
    for found in _EMAIL_RE.findall(text):
        if found.lower() != profile_email.lower():
            warnings.append(f"Email address not grounded in candidate profile: {found}")

    facts_norm = _normalize(" ".join(str(f) for f in facts))
    for match in _NUMERIC_CLAIM_RE.finditer(text):
        claim = match.group(0).strip()
        if _normalize(claim) not in facts_norm:
            warnings.append(f"Numeric claim not found in verified facts: {claim}")

    lowered = text.lower()
    for phrase in list(getattr(profile, "do_not_claim", None) or []):
        cleaned = str(phrase).strip()
        if cleaned and cleaned.lower() in lowered:
            warnings.append(f"Text contains a do-not-claim phrase: {cleaned}")

    return warnings
