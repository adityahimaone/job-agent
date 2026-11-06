"""ego(lite) browser bridge.

`ego-browser` drives the real Chromium that lives inside ego(lite), on the machine
where the person is logged in. When job-agent runs elsewhere (a VPS), the bridge is
one ssh hop: the script goes in on stdin, one `__RESULT__<json>` line comes back.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

RESULT_PREFIX = "__RESULT__"

# Version-pinned path of the binary shipped inside ego lite on macOS. Not on PATH.
DEFAULT_BINARY = (
    "/Applications/ego lite.app/Contents/Frameworks/ego Framework.framework"
    "/Versions/0.6.2.8/Helpers/ego-browser"
)


class EgoError(RuntimeError):
    """ego-browser could not be reached, or gave no result."""


@dataclass
class EgoClient:
    """Runs one ego-browser nodejs script. `host` empty means run locally."""

    host: str | None = None
    user: str = ""
    binary: str = DEFAULT_BINARY
    timeout: int = 180

    @property
    def target(self) -> str:
        if self.user and self.host:
            return f"{self.user}@{self.host}"
        return self.host or ""

    def _argv(self) -> list[str]:
        command = f"'{self.binary}' nodejs"
        if not self.host:
            return ["bash", "-lc", command]
        return [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            self.target,
            command,
        ]

    def run(self, script: str) -> dict:
        """Send `script` to ego-browser and return the JSON it printed."""
        try:
            proc = subprocess.run(
                self._argv(),
                input=script,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise EgoError(f"ssh not found: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise EgoError(
                f"ego-browser timed out after {self.timeout}s on {self.target or 'local'}"
            ) from exc

        # ego-browser prints the script's console.log to stderr, not stdout.
        for stream in (proc.stdout, proc.stderr):
            for line in reversed((stream or "").splitlines()):
                if RESULT_PREFIX in line:
                    payload = line.split(RESULT_PREFIX, 1)[1]
                    try:
                        return json.loads(payload)
                    except json.JSONDecodeError as exc:
                        raise EgoError(f"bad result line: {line[:200]}") from exc

        detail = ((proc.stderr or "") + (proc.stdout or "")).strip()[:500]
        raise EgoError(
            f"ego-browser gave no {RESULT_PREFIX} line "
            f"(exit {proc.returncode}): {detail}"
        )

    def doctor(self) -> dict:
        """Prove the bridge end to end: open example.com and read its title."""
        script = """
const task = await taskSpace("job-agent doctor");
const page = task.page("p1");
await page.goto("https://example.com");
console.log("__RESULT__" + JSON.stringify({
  spaceId: task.spaceId,
  title: await page.title(),
  url: await page.url()
}));
"""
        return self.run(script)