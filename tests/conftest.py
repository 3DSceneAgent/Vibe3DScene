import os
import pytest


@pytest.fixture(autouse=True)
def _set_test_env():
    os.environ.setdefault("VLM_API_KEY", "test-key")
    yield
import pytest


@pytest.fixture(scope="session")
def api_base_url() -> str:
    return "http://localhost:8000"
