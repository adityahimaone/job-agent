"""Regression pins for the remote ego(lite) lane.

Two bugs used to make the Mac lane silently produce zero rows from cron:
  * the driver resolved a Mac-only binary path on the VPS and the remote shell
    answered `permission denied` (exit 126);
  * `dealls` was missing from the hardcoded platform list, so the board was
    never called at all.
"""

from __future__ import annotations

from job_agent.platforms.ego.client import EgoClient


def test_remote_argv_uses_remote_path_not_local_absolute_binary():
    client = EgoClient(host="100.0.0.1", user="someone", binary="ego-browser")
    argv = client._argv()

    assert argv[0] == "ssh"
    command = argv[-1]
    assert command.startswith('export PATH="$HOME/.local/bin:$PATH";')
    assert "'ego-browser' nodejs" in command


def test_remote_driver_keeps_configured_binary_when_set():
    client = EgoClient(
        host="100.0.0.1", user="someone", binary="/opt/custom/ego-browser"
    )
    assert "'/opt/custom/ego-browser' nodejs" in client._argv()[-1]


def test_driver_with_host_skips_local_binary_resolution():
    from types import SimpleNamespace

    from job_agent.platforms.ego.driver import EgoDriver

    settings = SimpleNamespace(
        ego=SimpleNamespace(
            host="100.0.0.1",
            user="someone",
            binary="",
            task_space="job-agent",
            settle_ms=0,
            timeout=30,
        )
    )
    driver = EgoDriver(settings, "dealls")
    assert driver.client.binary == "ego-browser"


def test_platform_loop_includes_dealls():
    import inspect

    from job_agent.orchestrator import pipeline

    source = inspect.getsource(pipeline.run_pipeline)
    loop = source[
        source.index("for p in [") : source.index("]:", source.index("for p in ["))
    ]
    assert '"dealls"' in loop
