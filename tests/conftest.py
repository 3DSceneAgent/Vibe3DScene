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
    os.environ.setdefault("RODIN_MODE", "MAIN_SITE")
    yield

@pytest.fixture(scope="session")
def api_base_url() -> str:
    return "http://localhost:8000"
