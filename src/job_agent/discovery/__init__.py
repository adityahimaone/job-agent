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

__all__ = [
    "FeedHub",
    "FeedResult",
    "FeedSpec",
    "default_feed_specs",
    "load_feed_specs",
    "BoardSpec",
    "default_board_specs",
    "load_board_specs",
]
