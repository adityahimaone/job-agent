"""JEV (typesafe.ai) structured scoring — no chat needed.

`POST /v1/systemone` with a "noul" question returns a low-token numeric
answer directly (probe: perfect fit 0.9, unrelated role 0.04). "score"
questions return an expected criterion index, not a 0-1 fit, so noul is
primary; a numeric choice is the fallback. Result is clamped to [0, 1].

Enabled when TYPESAFE_API_KEY is set (TYPESAFE_API_URL / TYPESAFE_JEV_MODEL
optional). Any failure returns None and the caller falls back to the AI
chat matcher — JEV never blocks a run.
"""

from __future__ import annotations

import os

import httpx

from job_agent.utils.logging import get_logger

log = get_logger(__name__)

# Settings() consumes .env for model fields but never exports it, so read it
# here as well: TYPESAFE_* is not a Settings field.
try:
    from dotenv import load_dotenv

    load_dotenv(".env", override=False)
except Exception:  # dotenv missing is not fatal — real env still works
    pass

DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"

FIT_INSTRUCTIONS = (
    "Return ONLY a decimal number between 0 and 1 for how well this job fits "
    "the candidate described in the state. 0 means no fit, 1 means a perfect fit."
)


def jev_enabled() -> bool:
    return bool(os.environ.get("TYPESAFE_API_KEY"))


def score_fit(state: str, timeout: int = 30) -> float | None:
    """One systemone call → fit in [0, 1], or None on any failure."""
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key:
        return None
    url = os.environ.get("TYPESAFE_API_URL") or DEFAULT_URL
    model = os.environ.get("TYPESAFE_JEV_MODEL") or DEFAULT_MODEL
    payload = {
        "state": state,
        "model": model,
        "questions": {"fit": {"type": "noul", "instructions": FIT_INSTRUCTIONS}},
    }
    try:
        resp = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        answer = resp.json()["answers"]["fit"]
        value = float(answer["noul"])
    except Exception as exc:  # network, schema, parse — degrade, don't die
        log.warning("jev_score_failed", error=str(exc)[:200])
        return None
    return max(0.0, min(1.0, value))
