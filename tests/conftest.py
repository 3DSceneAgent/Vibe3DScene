import os
import sys
from pathlib import Path
import pytest


PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture(autouse=True)
def _set_test_env():
    os.environ.setdefault("VLM_API_KEY", "test-key")
    os.environ.setdefault("ENABLE_RODIN", "false")
    os.environ.setdefault("RODIN_API_KEY", "")
    yield


@pytest.fixture(scope="session", autouse=True)
def _clear_runtime_registry_state():
    from scene_agent.session import get_session_coordinator

    coordinator = get_session_coordinator()
    coordinator.clear_runtime_state()
    yield
    coordinator.clear_runtime_state()

@pytest.fixture(scope="session")
def api_base_url() -> str:
    return "http://localhost:8000"
