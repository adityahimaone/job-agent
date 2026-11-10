"""Shared test fixtures."""

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import MagicMock

from job_agent.config import Settings
from job_agent.db.models import Base


@pytest.fixture(autouse=True)
def _isolate_test_env(monkeypatch):
    """Clear ambient job-agent env vars for every test.

    Importing job_agent.ai.jev runs load_dotenv(".env") at module import,
    which leaks the developer .env into os.environ. That breaks tests two
    ways: pydantic-settings rebuilds nested platform configs from env
    (JOB_AGENT_PLATFORMS__LINKEDIN__USE_EGO alone flips linkedin.enabled
    to False, losing the per-platform defaults), and JobMatcher prefers
    the live JEV API over an injected mock when TYPESAFE_API_KEY is set.
    Tests must be hermetic: they pass with or without a local .env, and
    in CI where no .env exists. Tests that need a var set it themselves
    (monkeypatch / patch.dict run after this fixture).
    """
    for key in list(os.environ):
        if key.startswith("JOB_AGENT_") or key.startswith("TYPESAFE_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def settings():
    """Test settings with in-memory SQLite."""
    s = Settings(
        _env_file=None,
        anthropic_api_key="test-key",
        database_url="sqlite:///:memory:",
        flask_secret_key="test-secret",
    )
    return s


@pytest.fixture
def db_session(settings):
    """Create an in-memory database session for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def mock_page():
    """A MagicMock standing in for a Playwright Page."""
    page = MagicMock()
    page.url = "https://example.com/job/123"
    page.is_closed.return_value = False

    # Default locator behaviour: nothing found
    locator = MagicMock()
    locator.count.return_value = 0
    page.locator.return_value = locator

    return page


@pytest.fixture
def mock_rate_limiter():
    """A MagicMock standing in for RateLimiter."""
    rl = MagicMock()
    rl.wait.return_value = True  # circuit breaker closed by default
    return rl
