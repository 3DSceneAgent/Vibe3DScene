from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from PIL import Image

from scene_agent.config import get_settings
from scene_agent.memory.reference_image_store import get_image_asset_store

GLOBAL_TASK_ID = "global"
VALID_IMAGE_ROLES: frozenset[str] = frozenset(
    {
        "question_image",
        "object_reference",
        "scene_reference",
        "style_reference",
        "verification_reference",
    }
)


@dataclass(frozen=True)
class ImageAsset:
    id: str
    thread_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    stored_path: str
    uploaded_at: str
    source: str


@dataclass(frozen=True)
class ImageBinding:
    id: str
    thread_id: str
    task_id: str
    image_id: str
    role: str
    created_at: str
    updated_at: str


# Legacy naming alias kept for compatibility with existing internal imports.
ReferenceImage = ImageAsset


class ReferenceImageMemory:
    """Thread-level image asset manager with internal task-level role bindings."""

    def __init__(self) -> None:
        self._store = get_image_asset_store()

    def list_assets(self, thread_id: str) -> list[ImageAsset]:
        rows = self._store.list_assets(thread_id)
        assets: list[ImageAsset] = []
        for row in rows:
            parsed = self._coerce_asset(row, thread_id)
            if parsed is not None:
                assets.append(parsed)
        return assets

    def get_assets_by_ids(self, thread_id: str, asset_ids: Iterable[str]) -> list[ImageAsset]:
        ordered_ids: list[str] = []
        seen: set[str] = set()
        for raw_asset_id in asset_ids:
            asset_id = str(raw_asset_id).strip()
            if not asset_id or asset_id in seen:
                continue
            seen.add(asset_id)
            ordered_ids.append(asset_id)
        if not ordered_ids:
            return []

        assets_by_id = {asset.id: asset for asset in self.list_assets(thread_id)}
        return [assets_by_id[asset_id] for asset_id in ordered_ids if asset_id in assets_by_id]

    def list_bindings(self, thread_id: str, task_id: str | None = None) -> list[ImageBinding]:
        rows = self._store.list_bindings(thread_id, task_id)
        bindings: list[ImageBinding] = []
        for row in rows:
            parsed = self._coerce_binding(row, thread_id)
            if parsed is not None:
                bindings.append(parsed)
        return bindings

    def bind_images(
        self,
        *,
        thread_id: str,
        task_id: str,
        bindings: Iterable[tuple[str, str]],
    ) -> list[ImageBinding]:
        normalized_task_id = self._normalize_task_id(task_id)
        assets = self.list_assets(thread_id)
        asset_ids = {asset.id for asset in assets}

        payloads: list[dict[str, str]] = []
        results: list[ImageBinding] = []
        now = datetime.now().isoformat()

        dedup: list[tuple[str, str]] = []
        dedup_seen: set[tuple[str, str]] = set()
        for image_id_raw, role_raw in bindings:
            image_id = str(image_id_raw).strip()
            role = self._normalize_role(role_raw)
            if not image_id:
                continue
            if image_id not in asset_ids:
                raise ValueError(f"Image '{image_id}' does not exist in thread '{thread_id}'.")
            pair = (image_id, role)
            if pair in dedup_seen:
                continue
            dedup_seen.add(pair)
            dedup.append(pair)

        existing = self.list_bindings(thread_id, normalized_task_id)
        existing_by_key = {(binding.image_id, binding.role): binding for binding in existing}

        for image_id, role in dedup:
            existing_binding = existing_by_key.get((image_id, role))
            binding_id = existing_binding.id if existing_binding is not None else self._binding_id(
                normalized_task_id,
                image_id,
                role,
            )
            created_at = existing_binding.created_at if existing_binding is not None else now
            binding = ImageBinding(
                id=binding_id,
                thread_id=thread_id,
                task_id=normalized_task_id,
                image_id=image_id,
                role=role,
                created_at=created_at,
                updated_at=now,
            )
            payloads.append(self._binding_to_store(binding))
            results.append(binding)

        self._store.upsert_bindings(
            thread_id=thread_id,
            task_id=normalized_task_id,
            bindings=payloads,
        )
        return results

    def ensure_auto_bindings(
        self,
        *,
        thread_id: str,
        task_id: str | None,
        preferred_role: str,
    ) -> list[ImageBinding]:
        """Create task-scoped bindings automatically when none exists."""
        normalized_task_id = self._normalize_task_id(task_id)
        existing = self.list_bindings(thread_id, normalized_task_id)
        if existing:
            return existing
        assets = self.list_assets(thread_id)
        if not assets:
            return []
        return self.bind_images(
            thread_id=thread_id,
            task_id=normalized_task_id,
            bindings=[(asset.id, preferred_role) for asset in assets],
        )

    def resolve_assets(
        self,
        *,
        thread_id: str,
        task_id: str | None = None,
        roles: set[str] | None = None,
        limit: int | None = None,
    ) -> list[ImageAsset]:
        assets = self.list_assets(thread_id)
        if not assets:
            return []

        role_filter = {self._normalize_role(role) for role in roles} if roles else None
        asset_by_id = {asset.id: asset for asset in assets}

        selected_ids: list[str] = []
        if task_id is not None:
            task_ids = [self._normalize_task_id(task_id)]
            if task_ids[0] != GLOBAL_TASK_ID:
                task_ids.append(GLOBAL_TASK_ID)
            for current_task_id in task_ids:
                for binding in self.list_bindings(thread_id, current_task_id):
                    if role_filter and binding.role not in role_filter:
                        continue
                    if binding.image_id in asset_by_id:
                        selected_ids.append(binding.image_id)
        else:
            for binding in self.list_bindings(thread_id, None):
                if role_filter and binding.role not in role_filter:
                    continue
                if binding.image_id in asset_by_id:
                    selected_ids.append(binding.image_id)

        deduped_selected: list[str] = []
        seen_ids: set[str] = set()
        for image_id in selected_ids:
            if image_id in seen_ids:
                continue
            seen_ids.add(image_id)
            deduped_selected.append(image_id)

        if deduped_selected:
            resolved = [asset_by_id[image_id] for image_id in deduped_selected if image_id in asset_by_id]
        else:
            # Fallback for unbound assets: return latest thread assets.
            resolved = assets

        if isinstance(limit, int) and limit > 0 and len(resolved) > limit:
            return resolved[-limit:]
        return resolved

    def clear_thread(self, thread_id: str) -> None:
        self._store.clear_thread(thread_id)
        thread_dir = self._thread_storage_dir(thread_id)
        try:
            if os.path.isdir(thread_dir):
                shutil.rmtree(thread_dir)
        except OSError:
            pass

    def clear_all(self) -> None:
        self._store.clear_all()
        storage_dir = get_settings().reference_image_storage_dir
        try:
            if os.path.isdir(storage_dir):
                for entry in os.listdir(storage_dir):
                    candidate = os.path.join(storage_dir, entry)
                    if os.path.isdir(candidate):
                        shutil.rmtree(candidate, ignore_errors=True)
        except OSError:
            pass

    def add_assets(
        self,
        *,
        thread_id: str,
        uploads: Iterable[tuple[str, str, bytes]],
        source: str = "upload",
    ) -> list[ImageAsset]:
        settings = get_settings()
        max_count = settings.reference_image_max_count
        max_bytes = settings.reference_image_max_bytes

        uploads_list = list(uploads)
        if not uploads_list:
            return []

        existing_assets = self.list_assets(thread_id)
        existing_by_id = {asset.id: asset for asset in existing_assets}

        candidate_ids: set[str] = set()
        upload_payloads: list[tuple[str, str, bytes, str, str]] = []
        for filename, content_type, payload in uploads_list:
            if len(payload) > max_bytes:
                raise ValueError("Reference image exceeds maximum upload size.")
            self._validate_image_payload(payload, content_type)
            sha256 = hashlib.sha256(payload).hexdigest()
            image_id = self._asset_id(sha256)
            candidate_ids.add(image_id)
            upload_payloads.append((filename, content_type, payload, sha256, image_id))

        projected_total = len(existing_by_id | {image_id: None for image_id in candidate_ids})
        if projected_total > max_count:
            raise ValueError(f"Maximum {max_count} reference images allowed per conversation.")

        thread_dir = self._thread_storage_dir(thread_id)
        os.makedirs(thread_dir, exist_ok=True)

        new_assets_payload: list[dict[str, str]] = []
        returned_assets: list[ImageAsset] = []
        now = datetime.now().isoformat()

        for filename, content_type, payload, sha256, image_id in upload_payloads:
            existing = existing_by_id.get(image_id)
            if existing is not None:
                returned_assets.append(existing)
                continue

            ext = self._infer_extension(filename, content_type)
            stored_path = os.path.join(thread_dir, f"{image_id}{ext}")
            with open(stored_path, "wb") as handle:
                handle.write(payload)

            asset = ImageAsset(
                id=image_id,
                thread_id=thread_id,
                filename=filename or f"{image_id}{ext}",
                content_type=content_type or "image/unknown",
                size_bytes=len(payload),
                sha256=sha256,
                stored_path=stored_path,
                uploaded_at=now,
                source=source,
            )
            existing_by_id[image_id] = asset
            returned_assets.append(asset)
            new_assets_payload.append(self._asset_to_store(asset))

        if new_assets_payload:
            self._store.add_assets(thread_id=thread_id, assets=new_assets_payload)

        return returned_assets

    # ------------------------------------------------------------------
    # Internal/legacy helpers
    # ------------------------------------------------------------------
    def list_images(self, thread_id: str) -> list[ReferenceImage]:
        return self.list_assets(thread_id)

    def get_image_paths(self, thread_id: str) -> list[str]:
        return [image.stored_path for image in self.list_assets(thread_id)]

    def add_images(
        self,
        *,
        thread_id: str,
        uploads: Iterable[tuple[str, str, bytes]],
    ) -> list[ReferenceImage]:
        return self.add_assets(
            thread_id=thread_id,
            uploads=uploads,
            source="legacy_reference_upload",
        )

    @staticmethod
    def _normalize_task_id(task_id: str | None) -> str:
        text = str(task_id or "").strip()
        if not text:
            return GLOBAL_TASK_ID
        return text[:128]

    @staticmethod
    def _normalize_role(role: str) -> str:
        normalized = str(role).strip().lower().replace("-", "_").replace(" ", "_")
        if normalized not in VALID_IMAGE_ROLES:
            valid = ", ".join(sorted(VALID_IMAGE_ROLES))
            raise ValueError(f"Invalid image role '{role}'. Valid roles: {valid}.")
        return normalized

    @staticmethod
    def _safe_storage_thread_id(thread_id: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", thread_id).strip("._")
        return safe or "thread"

    def _thread_storage_dir(self, thread_id: str) -> str:
        storage_dir = get_settings().reference_image_storage_dir
        return os.path.join(storage_dir, self._safe_storage_thread_id(thread_id))

    @staticmethod
    def _asset_id(sha256: str) -> str:
        return sha256[:16]

    @staticmethod
    def _binding_id(task_id: str, image_id: str, role: str) -> str:
        seed = f"{task_id}:{image_id}:{role}"
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]

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
    def _validate_image_payload(payload: bytes, content_type: str | None) -> tuple[int, int]:
        if content_type and not content_type.startswith("image/"):
            raise ValueError("Unsupported upload type. Please upload an image.")
        try:
            with Image.open(io.BytesIO(payload)) as image:
                image.verify()
            with Image.open(io.BytesIO(payload)) as image:
                width, height = image.size
            return int(width), int(height)
        except Exception as exc:
            raise ValueError("Uploaded file is not a valid image.") from exc

    @staticmethod
    def _asset_to_store(asset: ImageAsset) -> dict[str, str]:
        return {
            "id": asset.id,
            "thread_id": asset.thread_id,
            "filename": asset.filename,
            "content_type": asset.content_type,
            "size_bytes": str(asset.size_bytes),
            "sha256": asset.sha256,
            "stored_path": asset.stored_path,
            "uploaded_at": asset.uploaded_at,
            "source": asset.source,
        }

    @staticmethod
    def _binding_to_store(binding: ImageBinding) -> dict[str, str]:
        return {
            "id": binding.id,
            "thread_id": binding.thread_id,
            "task_id": binding.task_id,
            "image_id": binding.image_id,
            "role": binding.role,
            "created_at": binding.created_at,
            "updated_at": binding.updated_at,
        }

    @staticmethod
    def _coerce_asset(row: dict[str, object], thread_id: str) -> ImageAsset | None:
        try:
            return ImageAsset(
                id=str(row.get("id", "")),
                thread_id=str(row.get("thread_id", thread_id)),
                filename=str(row.get("filename", "")),
                content_type=str(row.get("content_type", "image/unknown")),
                size_bytes=int(row.get("size_bytes", 0)),
                sha256=str(row.get("sha256", "")),
                stored_path=str(row.get("stored_path", "")),
                uploaded_at=str(row.get("uploaded_at", "")),
                source=str(row.get("source", "upload")),
            )
        except Exception:
            return None

    @staticmethod
    def _coerce_binding(row: dict[str, object], thread_id: str) -> ImageBinding | None:
        try:
            return ImageBinding(
                id=str(row.get("id", "")),
                thread_id=str(row.get("thread_id", thread_id)),
                task_id=str(row.get("task_id", GLOBAL_TASK_ID)),
                image_id=str(row.get("image_id", "")),
                role=str(row.get("role", "verification_reference")),
                created_at=str(row.get("created_at", "")),
                updated_at=str(row.get("updated_at", "")),
            )
        except Exception:
            return None


_reference_image_memory = ReferenceImageMemory()


def get_reference_image_memory() -> ReferenceImageMemory:
    return _reference_image_memory


def get_image_asset_memory() -> ReferenceImageMemory:
    return _reference_image_memory
