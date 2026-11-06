"""Human-supervised job application through ego(lite).

Safety contract (do not weaken):
  * Never fabricate form answers — only fields explicitly supplied are filled.
  * ``require_human_submit=True`` (default) hands control to the human and NEVER
    clicks submit itself; the human logs in, solves 2FA/CAPTCHA and clicks the
    final submit.
  * Never auto-click hCaptcha (or any captcha widget). The submit finder skips
    anything inside a captcha container / iframe.
  * Never claim success without evidence: a confirmation keyword must appear in
    the final snapshot, otherwise the result is ``pending_human`` / ``unverified``
    / ``failed`` with ``ok=False``.

Each :class:`EgoApplicator` step is logged. Work is grouped into single scripts
where possible to limit the number of ``ego-browser nodejs`` process spawns.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from job_agent.platforms.ego.client import EgoError
from job_agent.platforms.ego.session import EgoSession
from job_agent.utils.logging import get_logger

log = get_logger(__name__)

CONFIRM_KEYWORDS = (
    "application submitted",
    "thank you",
    "we've received",
    "we have received",
    "successfully applied",
    "application received",
    "confirmation",
)
FAILURE_KEYWORDS = (
    "error",
    "required field",
    "please complete",
)

_HANDOFF_MESSAGE = (
    "Review the application form, complete any login / 2FA / CAPTCHA, then "
    "click the final Submit button yourself. Return control to the agent "
    "afterwards so it can verify the result."
)


@dataclass
class EgoApplyResult:
    ok: bool
    status: str  # "confirmed" | "pending_human" | "unverified" | "failed"
    note: str = ""
    url: str = ""


def _classify(text: str) -> str:
    """Return ``confirmed`` / ``failed`` / ``""`` from snapshot text."""
    low = (text or "").lower()
    if any(k in low for k in CONFIRM_KEYWORDS):
        return "confirmed"
    if any(k in low for k in FAILURE_KEYWORDS):
        return "failed"
    return ""


class EgoApplicator:
    """Fill and (optionally) submit an application in the ego(lite) browser."""

    def __init__(
        self,
        session: EgoSession,
        *,
        require_human_submit: bool = True,
        settle_ms: int = 2500,
    ):
        self.session = session
        self.require_human_submit = bool(require_human_submit)
        self.settle_ms = int(settle_ms)

    def apply(
        self,
        url: str,
        resume_path: str,
        cover_letter_path: str = "",
        *,
        fields: dict[str, str] | None = None,
    ) -> EgoApplyResult:
        try:
            return self._apply(url, resume_path, cover_letter_path, fields)
        except EgoError as exc:
            log.warning("ego_apply_bridge_error", url=url, error=str(exc)[:300])
            return EgoApplyResult(
                False, "failed", f"ego-browser error: {exc}"[:500], url
            )
        except Exception as exc:  # noqa: BLE001 - never leak an exception to the pipeline
            log.warning("ego_apply_unexpected_error", url=url, error=str(exc)[:300])
            return EgoApplyResult(
                False, "failed", f"unexpected error: {exc}"[:500], url
            )

    def _apply(
        self,
        url: str,
        resume_path: str,
        cover_letter_path: str,
        fields: dict[str, str] | None,
    ) -> EgoApplyResult:
        log.info(
            "ego_apply_begin", url=url, require_human_submit=self.require_human_submit
        )

        # 1. Open the posting and observe the form.
        self.session.goto(url)
        log.info("ego_apply_goto_done", url=url)
        initial = self.session.snapshot(full_page=True)
        log.info("ego_apply_snapshot_initial", chars=len(initial))

        # 2. Fill ONLY the fields explicitly provided. Never fabricate answers.
        # A bad selector (e.g. a question string rather than a CSS selector)
        # must not abort the whole application: log and continue.
        for selector, value in (fields or {}).items():
            try:
                self.session.fill(selector, value)
                log.info("ego_apply_field_filled", selector=selector)
            except EgoError as exc:
                log.warning(
                    "ego_apply_field_fill_failed",
                    selector=selector,
                    error=str(exc)[:200],
                )

        # 3. Attach the resume (and cover letter when a 2nd file input exists).
        uploaded = self._upload_files(resume_path, cover_letter_path)
        log.info("ego_apply_upload", **uploaded)

        if self.require_human_submit:
            # 4a. Hand off: the human logs in, solves 2FA/CAPTCHA and clicks
            # the final submit. We NEVER click submit in this mode.
            handoff = self.session.handoff(_HANDOFF_MESSAGE)
            log.info(
                "ego_apply_handoff",
                message=handoff.get("message", _HANDOFF_MESSAGE),
                note="tell the human to review and click submit, then return control",
            )
            self.session.wait_for_control()
            log.info("ego_apply_control_regained")
            final = self.session.snapshot(full_page=True)
            log.info("ego_apply_snapshot_final", chars=len(final))

            if _classify(final) == "confirmed":
                return EgoApplyResult(
                    True, "confirmed", "confirmation text found after human submit", url
                )
            # No evidence of a completed submission: the human is still in the
            # loop (may be mid-form, or the submit produced no confirmation).
            return EgoApplyResult(
                False,
                "pending_human",
                "no confirmation found after control returned; a human must verify/submit",
                url,
            )

        # 4b. Automated mode: click a submit-like control, then verify.
        result = self._submit_and_verify()
        log.info(
            "ego_apply_auto_submit",
            clicked=result.get("clicked"),
            submit=result.get("submit"),
        )
        final = str(result.get("snapshot") or "")
        log.info("ego_apply_snapshot_final", chars=len(final))

        verdict = _classify(final)
        if verdict == "confirmed":
            return EgoApplyResult(
                True, "confirmed", "confirmation text found after submit", url
            )
        if verdict == "failed":
            return EgoApplyResult(
                False, "failed", "failure keywords found in final snapshot", url
            )
        return EgoApplyResult(
            False,
            "unverified",
            "submit attempted but no confirmation could be verified",
            url,
        )

    def _upload_files(self, resume_path: str, cover_letter_path: str) -> dict:
        """Tag file inputs and set the resume / cover letter via setInputFiles.

        One script = one process. ``input[type=file]`` is used for the resume
        when it is the only file input; with two inputs each is targeted by a
        temporary ``data-ego-file-idx`` attribute so the call is unambiguous.
        All paths are made absolute (ego-browser requires absolute paths).
        """
        files = [os.path.abspath(resume_path)]
        if cover_letter_path:
            files.append(os.path.abspath(cover_letter_path))
        script = f"""
const __files = {json.dumps(files)};
const __n = await page.evaluate(() => {{
  const els = [...document.querySelectorAll('input[type=file]')];
  els.forEach((e, i) => e.setAttribute('data-ego-file-idx', String(i)));
  return els.length;
}});
let __uploaded = 0;
if (__n === 1) {{
  await page.setInputFiles('input[type=file]', [__files[0]]);
  __uploaded = 1;
}} else if (__n >= 2) {{
  await page.setInputFiles('input[data-ego-file-idx="0"]', [__files[0]]);
  __uploaded = 1;
  if (__files.length > 1) {{
    await page.setInputFiles('input[data-ego-file-idx="1"]', [__files[1]]);
    __uploaded = 2;
  }}
}}
console.log("__RESULT__" + JSON.stringify({{
  fileInputs: __n, uploaded: __uploaded, paths: __files
}}));
"""
        result = self.session.run(script)
        return {
            "file_inputs": result.get("fileInputs"),
            "uploaded": result.get("uploaded"),
            "paths": result.get("paths", files),
        }

    def _submit_and_verify(self) -> dict:
        """Find a submit-like control, click it, and snapshot in one round.

        The finder explicitly skips anything inside a captcha container or
        iframe so hCaptcha/reCAPTCHA is NEVER auto-clicked. Only a genuine
        submit/apply control is tagged and clicked.
        """
        script = r"""
const __found = await page.evaluate(() => {
  const blocked = (el) => el.closest(
    '[class*="captcha"], [id*="captcha"], [class*="h-captcha"], '
    + '[class*="recaptcha"], [class*="g-recaptcha"], iframe'
  );
  const groups = [
    'button[type=submit]', 'input[type=submit]', 'button', '[role=button]'
  ];
  for (const sel of groups) {
    for (const el of document.querySelectorAll(sel)) {
      if (blocked(el)) continue;
      const raw = (
        (el.innerText || '') + ' ' + (el.value || '') + ' ' +
        (el.getAttribute('aria-label') || '')
      ).toLowerCase();
      if (el.type === 'submit' ||
          /submit|apply|send application|send|continue|next|finish/.test(raw)) {
        el.setAttribute('data-ego-submit', '1');
        return { selector: '[data-ego-submit="1"]', text: raw.trim().slice(0, 80) };
      }
    }
  }
  return { selector: '', text: '' };
});
if (__found.selector) {
  await page.click(__found.selector);
}
const __snap = await page.snapshot({ scope: "full_page" });
console.log("__RESULT__" + JSON.stringify({
  clicked: !!__found.selector, submit: __found, snapshot: __snap
}));
"""
        return self.session.run(script)
