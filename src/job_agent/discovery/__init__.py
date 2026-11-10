"""Discovery: fast structured feeds (fast lane) + Ego Lite board definitions."""

from __future__ import annotations

from job_agent.discovery.boards import (
    BoardSpec,
    default_board_specs,
    load_board_specs,
)
from job_agent.discovery.feeds import (
    FeedHub,
    FeedResult,
    FeedSpec,
    default_feed_specs,
    load_feed_specs,
)
from job_agent.discovery.lanes import (
    DEFAULT_EGO_LOCATIONS,
    DEFAULT_EGO_PLATFORMS,
    DEFAULT_EGO_QUERIES,
    DEFAULT_MUSE_LOCATIONS,
    DEFAULT_MUSE_QUERIES,
    LANE_EGO,
    LANE_MUSE,
    new_muse_jobs,
    resolve_lane_order,
    run_muse_first,
    should_fallback_to_ego,
)
from job_agent.discovery.muse import (
    MUSE_SOURCE,
    import_muse_jobs,
    parse_muse_file,
    to_posting,
)

__all__ = [
    "FeedHub",
    "FeedResult",
    "FeedSpec",
    "default_feed_specs",
    "load_feed_specs",
    "BoardSpec",
    "default_board_specs",
    "load_board_specs",
    "MUSE_SOURCE",
    "import_muse_jobs",
    "parse_muse_file",
    "to_posting",
    "DEFAULT_EGO_LOCATIONS",
    "DEFAULT_EGO_PLATFORMS",
    "DEFAULT_EGO_QUERIES",
    "DEFAULT_MUSE_LOCATIONS",
    "DEFAULT_MUSE_QUERIES",
    "LANE_EGO",
    "LANE_MUSE",
    "new_muse_jobs",
    "resolve_lane_order",
    "run_muse_first",
    "should_fallback_to_ego",
]
