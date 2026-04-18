"""Shared fixtures for integration tests."""

from pathlib import Path
import pytest
from dotenv import load_dotenv


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: safe read-only calls against demo API")
    config.addinivalue_line("markers", "trade: creates/modifies real demo positions — use sparingly")


@pytest.fixture(scope="session", autouse=True)
def load_env():
    """Load credentials from local .env, or fall back to capital-mcp-server .env."""
    local_env = Path(__file__).parents[2] / ".env"
    if local_env.exists():
        load_dotenv(local_env)
    else:
        fallback = Path("/home/chris/dev/capital-mcp-server/.env")
        if fallback.exists():
            load_dotenv(fallback)
