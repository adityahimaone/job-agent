"""ego(lite) bridge: drive the person's own browser from a script.

Kept for backwards compatibility: ``DEFAULT_BINARY``, ``EgoClient``, ``EgoError``
remain importable from here (and from their original submodules). New v2 helpers
are re-exported for convenience.
"""

from job_agent.platforms.ego.applicator import EgoApplyResult, EgoApplicator
from job_agent.platforms.ego.boards import (
    BoardSpec,
    build_board_script,
    default_board_specs,
    load_board_specs,
    parse_board_rows,
)
from job_agent.platforms.ego.client import (
    DEFAULT_BINARY,
    EgoClient,
    EgoError,
    resolve_ego_binary,
)
from job_agent.platforms.ego.discovery import EgoDiscovery
from job_agent.platforms.ego.driver import EgoDriver
from job_agent.platforms.ego.session import EgoSession

__all__ = [
    "DEFAULT_BINARY",
    "EgoClient",
    "EgoError",
    "resolve_ego_binary",
    "EgoDiscovery",
    "EgoDriver",
    "EgoSession",
    "EgoApplicator",
    "EgoApplyResult",
    "BoardSpec",
    "default_board_specs",
    "load_board_specs",
    "build_board_script",
    "parse_board_rows",
]
