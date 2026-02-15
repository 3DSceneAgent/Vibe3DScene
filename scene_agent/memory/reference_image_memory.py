from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from PIL import Image

from scene_agent.config import get_settings
from scene_agent.memory.reference_image_store import get_reference_image_store


@dataclass(frozen=True)
class ReferenceImage:
    id: str
    thread_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    stored_path: str
    uploaded_at: str


class ReferenceImageMemory:
    def __init__(self) -> None:
        self._store = get_reference_image_store()

    def list_images(self, thread_id: str) -> list[ReferenceImage]:
        rows = self._store.list_images(thread_id)
        images: list[ReferenceImage] = []
        for row in rows:
            try:
                images.append(
                    ReferenceImage(
                        id=str(row.get("id", "")),
                        thread_id=str(row.get("thread_id", thread_id)),
                        filename=str(row.get("filename", "")),
                        content_type=str(row.get("content_type", "image/unknown")),
                        size_bytes=int(row.get("size_bytes", 0)),
                        sha256=str(row.get("sha256", "")),
                        stored_path=str(row.get("stored_path", "")),
                        uploaded_at=str(row.get("uploaded_at", "")),
                    )
                )
            except Exception:
                continue
        return images

    def clear_thread(self, thread_id: str) -> None:
        self._store.clear_thread(thread_id)

    def clear_all(self) -> None:
        self._store.clear_all()

    def get_image_paths(self, thread_id: str) -> list[str]:
        return [image.stored_path for image in self.list_images(thread_id)]

    def add_images(
        self,
        *,
        thread_id: str,
        uploads: Iterable[tuple[str, str, bytes]],
    ) -> list[ReferenceImage]:
        settings = get_settings()
        max_count = settings.reference_image_max_count
        max_bytes = settings.reference_image_max_bytes
        storage_dir = settings.reference_image_storage_dir

        stored: list[ReferenceImage] = []
        uploads_list = list(uploads)
        if not uploads_list:
            return stored

        current = self.list_images(thread_id)
        if len(current) + len(uploads_list) > max_count:
            raise ValueError(f"Maximum {max_count} reference images allowed per conversation.")

        thread_dir = os.path.join(storage_dir, thread_id)
        os.makedirs(thread_dir, exist_ok=True)

        for filename, content_type, payload in uploads_list:
            if len(payload) > max_bytes:
                raise ValueError("Reference image exceeds maximum upload size.")
            self._validate_image_payload(payload, content_type)

            image_id = self._generate_id(payload)
            ext = self._infer_extension(filename, content_type)
            stored_path = os.path.join(thread_dir, f"{image_id}{ext}")
            with open(stored_path, "wb") as handle:
                handle.write(payload)

            metadata = ReferenceImage(
                id=image_id,
                thread_id=thread_id,
                filename=filename,
                content_type=content_type or "image/unknown",
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                stored_path=stored_path,
                uploaded_at=datetime.now().isoformat(),
            )
            stored.append(metadata)

        self._store.add_images(
            thread_id=thread_id,
            images=[self._to_store_payload(image) for image in stored],
        )

        return stored

    @staticmethod
    def _to_store_payload(image: ReferenceImage) -> dict[str, str]:
        return {
            "id": image.id,
            "thread_id": image.thread_id,
            "filename": image.filename,
            "content_type": image.content_type,
            "size_bytes": str(image.size_bytes),
            "sha256": image.sha256,
            "stored_path": image.stored_path,
            "uploaded_at": image.uploaded_at,
        }

    @staticmethod
    def _generate_id(payload: bytes) -> str:
        seed = hashlib.sha256(payload).hexdigest()
        return seed[:16]

    @staticmethod
    def _infer_extension(filename: str, content_type: str | None) -> str:
        if filename and "." in filename:
            ext = os.path.splitext(filename)[1]
            if ext:
                return ext
        if content_type:
            if content_type == "image/png":
                return ".png"
            if content_type == "image/jpeg":
                return ".jpg"
            if content_type == "image/webp":
                return ".webp"
        return ".png"

    @staticmethod
    def _validate_image_payload(payload: bytes, content_type: str | None) -> None:
        if content_type and not content_type.startswith("image/"):
            raise ValueError("Unsupported upload type. Please upload an image.")
        try:
            with Image.open(io.BytesIO(payload)) as image:
                image.verify()
        except Exception as exc:
            raise ValueError("Uploaded file is not a valid image.") from exc


_reference_image_memory = ReferenceImageMemory()


def get_reference_image_memory() -> ReferenceImageMemory:
    return _reference_image_memory
