from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_DOTENV_LOADED = False


def load_project_dotenv(*, override: bool = True) -> bool:
    """Load project-level .env into process environment.

    By default, repository-local `.env` values override inherited shell
    variables so project configuration is deterministic across terminals/IDEs.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED and not override:
        return False

    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return False

    loaded = load_dotenv(dotenv_path=env_path, override=override)
    _DOTENV_LOADED = _DOTENV_LOADED or bool(loaded)
    return bool(loaded)
