from __future__ import annotations

import json
import logging
import time
from typing import Any, Mapping

_LOGGER_NAME = "scene_agent"


def _ensure_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
        )
    return logger


def log_event(level: str, message: str, context: Mapping[str, Any] | None = None) -> None:
    logger = _ensure_logger()
    payload: dict[str, Any] = {
        "message": message,
        "timestamp": time.time(),
    }
    if context:
        payload.update(context)

    serialized = json.dumps(payload, ensure_ascii=False, default=str)
    level_name = level.lower()
    if level_name == "debug":
        logger.debug(serialized)
    elif level_name == "warning" or level_name == "warn":
        logger.warning(serialized)
    elif level_name == "error":
        logger.error(serialized)
    elif level_name == "critical":
        logger.critical(serialized)
    else:
        logger.info(serialized)
