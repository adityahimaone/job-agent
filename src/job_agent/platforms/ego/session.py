"""Human-supervised ego(lite) session helpers.

``EgoSession`` wraps the documented ego-browser ``skill`` API (v2.2.0 macOS):

    taskSpace(name|id), task.page("p1"), page.goto(), page.snapshot(),
    page.fill(), page.click(), page.setInputFiles(), page.evaluate(),
    page.fetch(), task.handOff(), task.waitForControl(), task.finish(),
    task.tabs(), page.url(), page.title()

Every method builds a small JavaScript script, prints ``__RESULT__<json>`` and
returns the parsed dict. **Every method spawns a fresh ``ego-browser nodejs``
process** — task spaces, tabs and Page labels persist, but JS variables do not.
For efficiency, callers should batch several actions into ONE script using
``run_batch`` or ``run`` rather than chaining many single-action methods.

All string inputs are embedded with ``json.dumps`` so selectors, URLs, values
and messages cannot break out of the generated script (JSON-injection safe).
Human takeover: call ``handoff()`` (ends the round), tell the human what to do,
then resume the same named space with ``wait_for_control()`` in a later round.
"""

from __future__ import annotations

import json
import os
from typing import Any

from job_agent.platforms.ego.client import EgoClient
from job_agent.utils.logging import get_logger

log = get_logger(__name__)

# Injected at the top of every script. ``taskSpace(name)`` resumes our space by
# name, so a later round keeps the same tabs and Page labels.
_PREAMBLE = 'const task = await taskSpace({space});\nconst page = task.page("p1");\n'


class EgoSession:
    """A reusable, name-addressed ego(lite) task space."""

    def __init__(self, client: EgoClient, space: str = "job-agent"):
        self.client = client
        self.space = space or "job-agent"

    def _preamble(self) -> str:
        return _PREAMBLE.format(space=json.dumps(self.space))

    def run(self, script: str) -> dict:
        """Run *script* with the task/page preamble injected.

        Passes through to :meth:`EgoClient.run` and returns its parsed dict.
        Use this (or ``run_batch``) to compose multiple actions in one process.
        """
        return self.client.run(self._preamble() + "\n" + script)

    @staticmethod
    def _result(expr: str) -> str:
        """Return a ``console.log("__RESULT__" + JSON.stringify(...))`` line."""
        return f'console.log("__RESULT__" + JSON.stringify({expr}));'

    def open(self) -> dict:
        """Ensure the task space exists; return ``{spaceId, name}``."""
        script = self._result("{ spaceId: task.spaceId, name: task.name }")
        return self.run(script)

    def snapshot(self, *, full_page: bool = False) -> str:
        """Return the semantic page snapshot as a string."""
        scope = "full_page" if full_page else "only_within_viewport"
        script = (
            f"const snap = await page.snapshot({{ scope: {json.dumps(scope)} }});\n"
            + self._result("{ snapshot: snap }")
        )
        result = self.run(script)
        snap = result.get("snapshot", "")
        return snap if isinstance(snap, str) else json.dumps(snap, ensure_ascii=False)

    def goto(self, url: str) -> dict:
        script = f"await page.goto({json.dumps(url)});\n" + self._result(
            "{ url: await page.url() }"
        )
        return self.run(script)

    def fill(self, selector: str, value: str) -> dict:
        script = (
            f"await page.fill({json.dumps(selector)}, {json.dumps(value)});\n"
            + self._result("{ ok: true, selector: " + json.dumps(selector) + " }")
        )
        return self.run(script)

    def click(self, selector: str) -> dict:
        script = f"await page.click({json.dumps(selector)});\n" + self._result(
            "{ ok: true, selector: " + json.dumps(selector) + " }"
        )
        return self.run(script)

    def set_input_files(self, selector: str, paths: list[str]) -> dict:
        absolute = [os.path.abspath(p) for p in paths]
        script = (
            f"await page.setInputFiles({json.dumps(selector)}, "
            f"{json.dumps(absolute)});\n"
            + self._result("{ ok: true, paths: " + json.dumps(absolute) + " }")
        )
        return self.run(script)

    def evaluate(self, expression: str, argument: Any = None) -> object:
        """Run *expression* inside the page and return its JSON value.

        ``expression`` is raw browser-side JavaScript (a function or expression
        string); ``argument`` is JSON-serialized and passed as its single arg.
        """
        script = (
            f"const __arg = {json.dumps(argument)};\n"
            f"const __val = await page.evaluate({expression}, __arg);\n"
            + self._result("{ value: __val }")
        )
        result = self.run(script)
        return result.get("value")

    def handoff(self, message: str = "") -> dict:
        """Give control to the human and END this round.

        The skill requires ending the round after ``handOff()`` and telling the
        user what to do; resume the same space via ``wait_for_control()`` in a
        later round. ``message`` is carried in the result for the caller to
        surface to the human.
        """
        script = "await task.handOff();\n" + self._result(
            "{ handedOff: true, message: " + json.dumps(message) + " }"
        )
        return self.run(script)

    def wait_for_control(self, timeout_ms: int = 300000) -> dict:
        """Block (in a resumed round) until the human returns Agent control."""
        script = (
            f"await task.waitForControl({{ timeout: {int(timeout_ms)} }});\n"
            + self._result("{ control: true }")
        )
        return self.run(script)

    def finish(self, keep: list[str] | None = None) -> dict:
        """Close the task space. ``keep`` lists Page labels to retain."""
        kept = keep or []
        script = f"await task.finish({{ keep: {json.dumps(kept)} }});\n" + self._result(
            "{ finished: true, keep: " + json.dumps(kept) + " }"
        )
        return self.run(script)

    def tabs(self) -> list[dict]:
        """Return ``await task.tabs()`` (best-effort, for diagnostics)."""
        script = "const t = await task.tabs();\n" + self._result("{ tabs: t }")
        result = self.run(script)
        tabs = result.get("tabs", [])
        return tabs if isinstance(tabs, list) else []

    def run_batch(self, actions: list[dict]) -> dict:
        """Compose ONE script for a sequence of goto/fill/click/snapshot steps.

        Each action is a dict with a ``type`` of ``goto`` (``url``), ``fill``
        (``selector``, ``value``), ``click`` (``selector``) or ``snapshot``
        (optional ``full_page``). Snapshots are collected into ``snapshots``.
        One process = fewer spawns than chaining the single-action methods.
        """
        lines: list[str] = ["const __snaps = [];"]
        for action in actions:
            kind = action.get("type")
            if kind == "goto":
                lines.append(f"await page.goto({json.dumps(action.get('url', ''))});")
            elif kind == "fill":
                lines.append(
                    f"await page.fill({json.dumps(action.get('selector', ''))}, "
                    f"{json.dumps(action.get('value', ''))});"
                )
            elif kind == "click":
                lines.append(
                    f"await page.click({json.dumps(action.get('selector', ''))});"
                )
            elif kind == "snapshot":
                scope = (
                    "full_page" if action.get("full_page") else "only_within_viewport"
                )
                lines.append(
                    f"__snaps.push(await page.snapshot({{ scope: {json.dumps(scope)} }}));"
                )
            else:
                log.warning("ego_batch_unknown_action", type=str(kind))
        lines.append(self._result("{ ok: true, snapshots: __snaps }"))
        return self.run("\n".join(lines))
