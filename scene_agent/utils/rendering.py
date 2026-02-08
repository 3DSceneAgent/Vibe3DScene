from __future__ import annotations

import hashlib
import logging
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image as PILImage

RENDERS_DIR = Path(tempfile.gettempdir()) / "scene_agent_renders"
RENDERS_DIR.mkdir(parents=True, exist_ok=True)

MAX_IMAGE_SIZE = (1920, 1080)  # Max resolution
JPEG_QUALITY = 85  # Compression quality

LogEvent = Callable[[str, str, Mapping[str, Any] | None], None]


def _build_render_url(prefix: str, filename: str) -> str:
    prefix = prefix.rstrip("/")
    if not prefix:
        return f"/{filename}"
    return f"{prefix}/{filename}"


def process_and_save_render(
    source_path: str,
    thread_id: str,
    camera_name: str,
    *,
    renders_dir: Path = RENDERS_DIR,
    max_image_size: tuple[int, int] = MAX_IMAGE_SIZE,
    jpeg_quality: int = JPEG_QUALITY,
    url_prefix: str = "/renders",
    log_event: LogEvent | None = None,
    logger: logging.Logger | None = None,
) -> str:
    """
    Process rendered image: compress, resize, and save to renders directory.
    Returns the URL path to access the image.
    """
    try:
        img = PILImage.open(source_path)
        original_size = img.size

        # Convert RGBA to RGB if needed
        if img.mode == "RGBA":
            background = PILImage.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Resize if larger than max size
        if img.width > max_image_size[0] or img.height > max_image_size[1]:
            img.thumbnail(max_image_size, PILImage.Resampling.LANCZOS)

        # Generate filename based on content hash for deduplication
        img_bytes = img.tobytes()
        content_hash = hashlib.md5(img_bytes).hexdigest()[:12]
        timestamp = int(time.time() * 1000)
        filename = f"{thread_id}_{camera_name}_{timestamp}_{content_hash}.jpg"

        # Save as JPEG with compression
        dest_path = renders_dir / filename
        img.save(dest_path, "JPEG", quality=jpeg_quality, optimize=True)

        if log_event is not None:
            log_event(
                "info",
                "render_saved",
                {
                    "thread_id": thread_id,
                    "camera": camera_name,
                    "filename": filename,
                    "original_size": f"{original_size}",
                    "processed_size": f"{img.size}",
                    "file_size_kb": dest_path.stat().st_size // 1024,
                },
            )

        if logger is not None:
            logger.info(
                "Processed render: %s, size: %s, file: %sKB",
                filename,
                img.size,
                dest_path.stat().st_size // 1024,
            )

        return _build_render_url(url_prefix, filename)
    except Exception as exc:
        if log_event is not None:
            log_event(
                "error",
                "render_processing_failed",
                {"error": str(exc), "source_path": source_path},
            )
        if logger is not None:
            logger.error("Failed to process render: %s", exc)
        raise
