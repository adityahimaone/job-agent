"""ego(lite) browser bridge.

`ego-browser` drives the real Chromium that lives inside ego(lite), on the machine
where the person is logged in. When job-agent runs elsewhere (a VPS), the bridge is
one ssh hop: the script goes in on stdin, one `__RESULT__<json>` line comes back.

Every `ego-browser nodejs` invocation is a fresh Node process. Task spaces, tabs
and Page labels persist across invocations; JavaScript variables do not. The
browser console (`console.log`) is delivered on *stderr*, so `run()` scans both
streams. See ``session.py`` for the batched, human-supervised workflow.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field

RESULT_PREFIX = "__RESULT__"

# Layout of ego(lite)'s embedded framework on macOS. The versioned directory is
# reached through the ``Current`` symlink so an ego(lite) update does not break
# the app; ``resolve_ego_binary`` adds explicit fallbacks for stale installs.
_APP_FRAMEWORK = (
    "/Applications/ego lite.app/Contents/Frameworks/ego Framework.framework"
)
DEFAULT_BINARY = f"{_APP_FRAMEWORK}/Versions/Current/Helpers/ego-browser"
LEGACY_BINARY = f"{_APP_FRAMEWORK}/Versions/0.6.2.8/Helpers/ego-browser"
_VERSIONS_GLOB = f"{_APP_FRAMEWORK}/Versions/*/Helpers/ego-browser"


class EgoError(RuntimeError):
    """ego-browser could not be reached, or gave no result."""


def _is_executable(path: str) -> bool:
    """True when *path* is a regular file we may execute."""
    try:
        return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)
    except (OSError, TypeError, ValueError):
        return False


def _version_key(path: str) -> tuple[int, ...]:
    """Sort key for a ``Versions/<v>/Helpers/ego-browser`` path (highest first)."""
    version_dir = os.path.basename(os.path.dirname(os.path.dirname(path)))
    parts: list[int] = []
    for seg in version_dir.replace("-", ".").split("."):
        parts.append(int(seg) if seg.isdigit() else 0)
    return tuple(parts)


def resolve_ego_binary(configured: str = "") -> str:
    """Locate the ego-browser binary in a version-safe way.

    Resolution order:
      1. an explicitly configured path (when non-empty and executable);
      2. ``Versions/Current/Helpers/ego-browser`` (tracks ego(lite) updates);
      3. the highest ``Versions/*/Helpers/ego-browser`` found on disk;
      4. ``shutil.which("ego-browser")`` (a PATH install);
      5. the legacy pinned ``0.6.2.8`` path.

    Raises ``EgoError`` with the full search list when nothing usable is found.
    """
    candidates: list[str] = []
    configured = (configured or "").strip()
    if configured:
        candidates.append(configured)
    candidates.append(DEFAULT_BINARY)
    candidates.extend(sorted(glob.glob(_VERSIONS_GLOB), key=_version_key, reverse=True))
    on_path = shutil.which("ego-browser")
    if on_path:
        candidates.append(on_path)
    candidates.append(LEGACY_BINARY)

    for path in candidates:
        if _is_executable(path):
            return path

    raise EgoError(
        "ego-browser binary not found. Tried: configured path, "
        f"{DEFAULT_BINARY}, the newest {_VERSIONS_GLOB}, "
        f"shutil.which('ego-browser'), and {LEGACY_BINARY}. "
        "Install ego(lite), put ego-browser on PATH, or set ego.binary in config."
    )


@dataclass
class EgoClient:
    """Runs one ego-browser nodejs script. `host` empty means run locally."""

    host: str | None = None
    user: str = ""
    binary: str = DEFAULT_BINARY
    timeout: int = 180
    # Raw `__RESULT__` line from the most recent run, for debugging.
    last_result_raw: str = field(default="", compare=False, repr=False)

    @property
    def target(self) -> str:
        if self.user and self.host:
            return f"{self.user}@{self.host}"
        return self.host or ""

    def _argv(self) -> list[str]:
        # Non-interactive ssh has no ~/.local/bin on PATH (where ego-browser
        # lives on the Mac), so prepend it explicitly for the remote case.
        command = f"export PATH=\"$HOME/.local/bin:$PATH\"; '{self.binary}' nodejs"
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
        return self._run(script, self.timeout)

    def _run(self, script: str, timeout: int) -> dict:
        """Shared runner; ``timeout`` can override ``self.timeout`` per call."""
        try:
            proc = subprocess.run(
                self._argv(),
                input=script,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise EgoError(f"ssh not found: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise EgoError(
                f"ego-browser timed out after {timeout}s on {self.target or 'local'}"
            ) from exc

        # ego-browser prints the script's console.log to stderr, not stdout.
        # Reversed lines make the LAST emitted `__RESULT__` win, which is what a
        # batched script (several __RESULT__ lines) expects.
        for stream in (proc.stdout, proc.stderr):
            for line in reversed((stream or "").splitlines()):
                if RESULT_PREFIX in line:
                    payload = line.split(RESULT_PREFIX, 1)[1]
                    self.last_result_raw = line
                    try:
                        return json.loads(payload)
                    except json.JSONDecodeError as exc:
                        raise EgoError(f"bad result line: {line[:200]}") from exc

        detail = ((proc.stderr or "") + (proc.stdout or "")).strip()[:500]
        raise EgoError(
            f"ego-browser gave no {RESULT_PREFIX} line "
            f"(exit {proc.returncode}): {detail}"
        )

    def version(self) -> str:
        """Return the embedded Node.js version (e.g. ``v24.22.1``).

        Uses a short 20s timeout override so a hung browser does not stall a
        doctor/version probe for the full run timeout.
        """
        script = (
            'console.log("__RESULT__" + JSON.stringify({ node: process.version }));'
        )
        result = self._run(script, 20)
        return str(result.get("node", "") or "")

    def run_many(self, scripts: list[str]) -> list[dict]:
        """Run several scripts and return their parsed results in order.

        Each entry is executed as its own ``ego-browser nodejs`` invocation
        (i.e. a fresh Node process). This is deliberately simple rather than
        concatenating scripts into one process: concatenated scripts would
        redeclare ``const task``/``const page`` and collide, and a single
        process can only surface one ``__RESULT__`` line to ``run()``. Callers
        that need one process should batch actions into a single script via
        ``EgoSession.run_batch`` or a composed script instead.
        """
        return [self.run(script) for script in scripts]

    def doctor(self) -> dict:
        """Prove the bridge end to end and report environment details."""
        script = f"""
const task = await taskSpace("job-agent doctor");
const page = task.page("p1");
await page.goto("https://example.com");
console.log("__RESULT__" + JSON.stringify({{
  spaceId: task.spaceId,
  title: await page.title(),
  url: await page.url(),
  node: process.version,
  binary: {json.dumps(self.binary)}
}}));
"""
        return self.run(script)
