"""Honest submission verification.

Legacy board drivers return ``True`` immediately after clicking submit. That is
an *attempt*, not a confirmation. This module turns a driver's outcome into an
explicit verification state so the database never claims more than we know.
"""

from __future__ import annotations

from dataclasses import dataclass

CONFIRMED = "confirmed"
UNVERIFIED = "unverified"
PENDING_HUMAN = "pending_human"
FAILED = "failed"

_SUCCESS_PHRASES = (
    "application submitted",
    "application received",
    "successfully applied",
    "we've received your application",
    "we have received your application",
    "thanks for applying",
    "thank you for applying",
    "application complete",
)
_FAILURE_PHRASES = (
    "please complete all required",
    "required field",
    "something went wrong",
    "unable to submit",
    "application closed",
    "no longer accepting applications",
)


@dataclass
class Verification:
    state: str
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.state in (CONFIRMED, UNVERIFIED)


def snapshot_verdict(snapshot: str) -> Verification:
    """Classify a page snapshot taken after a submission attempt."""
    text = (snapshot or "").lower()
    for phrase in _FAILURE_PHRASES:
        if phrase in text:
            return Verification(FAILED, f"page reported: {phrase}")
    for phrase in _SUCCESS_PHRASES:
        if phrase in text:
            return Verification(CONFIRMED, f"confirmation text: {phrase}")
    return Verification(UNVERIFIED, "no confirmation text found on page")


def verify_driver_result(driver, success: bool) -> Verification:
    """Combine the driver's boolean with any richer result it exposes.

    ``EgoDriver`` exposes ``last_result`` with an explicit status. Legacy
    Playwright drivers only return a bool, so their successful result is
    recorded as an unverified submission rather than a confirmed one.
    """
    if not success:
        result = getattr(driver, "last_result", None)
        status = getattr(result, "status", "")
        if status == PENDING_HUMAN:
            return Verification(PENDING_HUMAN, getattr(result, "note", ""))
        return Verification(FAILED, "driver returned failure")

    result = getattr(driver, "last_result", None)
    status = getattr(result, "status", "")
    note = getattr(result, "note", "")
    if status == CONFIRMED:
        return Verification(CONFIRMED, note or "driver confirmed submission")
    if status == PENDING_HUMAN:
        return Verification(PENDING_HUMAN, note or "waiting for human submit")
    if status == UNVERIFIED:
        return Verification(UNVERIFIED, note or "driver could not verify submission")
    return Verification(
        UNVERIFIED,
        "legacy driver returned True without a confirmation check",
    )
