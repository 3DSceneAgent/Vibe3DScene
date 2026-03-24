from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

DEFAULT_IMAGE_ROUTING_COMPARE_RECORD_PATH = Path(
    "/tmp/scene_agent_image_routing_compare_tool_calls.jsonl"
)
DEFAULT_SAM3D_RECONSTRUCT_RECORD_PATH = Path(
    "/tmp/scene_agent_sam3d_reconstruct_tool_calls.jsonl"
)
DEFAULT_SAM3D_RECONSTRUCT_BLEND_FILE_PATH = (
    "/tmp/scene_agent_sam3d_demo_reconstruct.blend"
)


class _ToolCallRecord(BaseModel):
    name: str
    args: dict[str, object] = Field(default_factory=dict)


class _SceneObjectState(BaseModel):
    name: str
    location: list[float]


class _SearchArgs(BaseModel):
    query: str
    top_k: int = 3


class _ImportRetrievedArgs(BaseModel):
    model_url: str
    object_name: str | None = None


class _GenerateHunyuanArgs(BaseModel):
    text_prompt: str | None = None
    input_image_url: str | None = None
    input_image_name: str | None = None
    input_image_id: str | None = None
    timeout_seconds: int = 300
    poll_interval_seconds: float = 5.0


class _ImportGlbArgs(BaseModel):
    model_url: str
    object_name: str | None = None


class _ExecuteCodeArgs(BaseModel):
    code: str
    safe_mode: bool = True
    rollback_on_guard_fail: bool = True
    validate_scene: bool = True


class _ReconstructFullSceneArgs(BaseModel):
    input_image_path: str | None = None
    input_image_name: str | None = None
    input_image_id: str | None = None
    output_dir: str | None = None
    timeout_seconds: int = 300
    poll_interval_seconds: float = 2.0
    min_area_threshold: int = 100
    max_masks: int = 15
    naming_mode: str = "index"
    vlm_model: str = "gpt-4o"
    seed: int = 42


class _ImportBlendContentsArgs(BaseModel):
    blend_file_path: str
    import_mode: str = "auto"
    collection_names: list[str] | str | None = None
    object_names: list[str] | str | None = None
    link: bool = False


class _ToolRecorder:
    def __init__(self, record_path: Path) -> None:
        self._record_path = record_path
        self._lock = threading.Lock()
        self._scene_objects: dict[str, _SceneObjectState] = {}

    def record(self, name: str, **kwargs: object) -> None:
        entry = _ToolCallRecord(name=name, args=dict(kwargs))
        with self._lock:
            self._record_path.parent.mkdir(parents=True, exist_ok=True)
            with self._record_path.open("a", encoding="utf-8") as handle:
                handle.write(entry.model_dump_json() + "\n")

    def ensure_object(self, name: str, location: list[float] | None = None) -> None:
        self._scene_objects[name] = _SceneObjectState(
            name=name,
            location=list(location or [0.0, 0.0, 0.0]),
        )

    def set_object_location(self, name: str, location: list[float]) -> None:
        self.ensure_object(name, location=location)

    def scene_info_payload(self) -> str:
        objects = []
        for object_state in self._scene_objects.values():
            objects.append(
                {
                    "name": object_state.name,
                    "type": "MESH",
                    "location": list(object_state.location),
                    "dimensions": [1.0, 1.0, 1.0],
                    "world_bounding_box": [
                        [object_state.location[0] - 0.5, -0.5, -0.5],
                        [object_state.location[0] + 0.5, 0.5, 0.5],
                    ],
                    "visible": True,
                    "material_count": 1,
                }
            )
        return json.dumps({"objects": objects}, ensure_ascii=False)


def _get_record_path(default_path: Path) -> Path:
    configured = os.getenv("SCENE_AGENT_TEST_TOOL_RECORD_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return default_path


def build_image_routing_compare_stub_tools() -> list[StructuredTool]:
    record_path = _get_record_path(DEFAULT_IMAGE_ROUTING_COMPARE_RECORD_PATH)
    recorder = _ToolRecorder(record_path)

    def get_scene_info() -> str:
        recorder.record("get_scene_info")
        return recorder.scene_info_payload()

    def search_3d_assets_by_text(query: str, top_k: int = 3) -> str:
        recorder.record("search_3d_assets_by_text", query=query, top_k=top_k)
        return (
            f"Found 1 assets for query: '{query}'\n"
            "1. Asset ID: retrieved-reference-asset\n"
            "   Similarity: 0.981\n"
            "   Description (EN): Retrieved candidate matching the uploaded reference object.\n"
            "   Model URL: https://example.test/retrieval/retrieved_reference_asset.glb\n\n"
            "To import one result, use import_retrieved_asset(model_url=..., object_name=...). "
            "asset_id is only a reference label and is not required by the import tool."
        )

    def import_retrieved_asset(model_url: str, object_name: str | None = None) -> str:
        normalized_name = object_name or "RetrievedAsset"
        recorder.record(
            "import_retrieved_asset",
            model_url=model_url,
            object_name=normalized_name,
        )
        recorder.ensure_object(normalized_name)
        return (
            f"Successfully imported model from '{model_url}'\n"
            f"Imported 1 object(s): {normalized_name}\n"
            "Bounding box: min=[0, 0, 0], max=[1, 1, 1]\n"
        )

    def generate_hunyuan3d_model(
        text_prompt: str | None = None,
        input_image_url: str | None = None,
        input_image_name: str | None = None,
        input_image_id: str | None = None,
        timeout_seconds: int = 300,
        poll_interval_seconds: float = 5.0,
    ) -> str:
        recorder.record(
            "generate_hunyuan3d_model",
            text_prompt=text_prompt,
            input_image_url=input_image_url,
            input_image_name=input_image_name,
            input_image_id=input_image_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        payload = {
            "job_id": "job_hunyuan_reference_compare",
            "status": "DONE",
            "result_file_3ds": [
                {
                    "File3D": [
                        {
                            "Type": "GIF",
                            "Url": "https://example.test/hunyuan/generated_reference_asset.gif",
                        },
                        {
                            "Type": "OBJ",
                            "Url": "https://example.test/hunyuan/generated_reference_asset.zip",
                        },
                    ]
                }
            ],
            "preferred_model_asset": {
                "type": "OBJ",
                "url": "https://example.test/hunyuan/generated_reference_asset.zip",
                "url_extension": "zip",
                "is_archive": True,
            },
        }
        return (
            json.dumps(payload, ensure_ascii=False)
            + "\n\nUse import_glb_model(model_url='https://example.test/hunyuan/generated_reference_asset.zip', "
            "object_name='GeneratedReferenceAsset') before any placement code."
        )

    def import_glb_model(model_url: str, object_name: str | None = None) -> str:
        normalized_name = object_name or "ImportedModel"
        recorder.record(
            "import_glb_model",
            model_url=model_url,
            object_name=normalized_name,
        )
        recorder.ensure_object(normalized_name)
        return (
            f"Successfully imported model from '{model_url}'\n"
            f"Imported 1 object(s): {normalized_name}\n"
            "Bounding box: min=[0, 0, 0], max=[1, 1, 1]\n"
            "If RetrievedReferenceAsset also exists, call execute_blender_code next to place "
            "RetrievedReferenceAsset at x=-1 and GeneratedReferenceAsset at x=1 side by side.\n"
        )

    def execute_blender_code(
        code: str,
        safe_mode: bool = True,
        rollback_on_guard_fail: bool = True,
        validate_scene: bool = True,
    ) -> str:
        recorder.record(
            "execute_blender_code",
            code=code,
            safe_mode=safe_mode,
            rollback_on_guard_fail=rollback_on_guard_fail,
            validate_scene=validate_scene,
        )
        if "RetrievedReferenceAsset" in code:
            recorder.set_object_location("RetrievedReferenceAsset", [-1.0, 0.0, 0.0])
        if "GeneratedReferenceAsset" in code:
            recorder.set_object_location("GeneratedReferenceAsset", [1.0, 0.0, 0.0])
        return (
            "Status: success\n"
            "Mode: execute_code(transactional)\n"
            "Transaction:\n"
            "```json\n"
            "{\"success\": true}\n"
            "```"
        )

    return [
        StructuredTool.from_function(
            func=get_scene_info,
            name="get_scene_info",
            description="Read current scene objects and transforms before or after imports and placement.",
        ),
        StructuredTool.from_function(
            func=search_3d_assets_by_text,
            name="search_3d_assets_by_text",
            description="Search Objaverse-style retrieval for a candidate matching the reference object.",
            args_schema=_SearchArgs,
        ),
        StructuredTool.from_function(
            func=import_retrieved_asset,
            name="import_retrieved_asset",
            description="Import a retrieved asset URL into the scene.",
            args_schema=_ImportRetrievedArgs,
        ),
        StructuredTool.from_function(
            func=generate_hunyuan3d_model,
            name="generate_hunyuan3d_model",
            description=(
                "Generate a Hunyuan3D model. For image-to-3D, leave text_prompt empty and use the "
                "current attached image or pass input_image_name/input_image_id."
            ),
            args_schema=_GenerateHunyuanArgs,
        ),
        StructuredTool.from_function(
            func=import_glb_model,
            name="import_glb_model",
            description="Import a GLB URL, including generated Hunyuan outputs, into the current scene.",
            args_schema=_ImportGlbArgs,
        ),
        StructuredTool.from_function(
            func=execute_blender_code,
            name="execute_blender_code",
            description="Run Blender Python for simple placement and offset adjustments on existing objects.",
            args_schema=_ExecuteCodeArgs,
        ),
    ]


def build_sam3d_reconstruct_stub_tools() -> list[StructuredTool]:
    record_path = _get_record_path(DEFAULT_SAM3D_RECONSTRUCT_RECORD_PATH)
    recorder = _ToolRecorder(record_path)

    def get_scene_info() -> str:
        recorder.record("get_scene_info")
        return recorder.scene_info_payload()

    def reconstruct_full_scene(
        input_image_path: str | None = None,
        input_image_name: str | None = None,
        input_image_id: str | None = None,
        output_dir: str | None = None,
        timeout_seconds: int = 300,
        poll_interval_seconds: float = 2.0,
        min_area_threshold: int = 100,
        max_masks: int = 15,
        naming_mode: str = "index",
        vlm_model: str = "gpt-4o",
        seed: int = 42,
    ) -> dict[str, Any]:
        recorder.record(
            "reconstruct_full_scene",
            input_image_path=input_image_path,
            input_image_name=input_image_name,
            input_image_id=input_image_id,
            output_dir=output_dir,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            min_area_threshold=min_area_threshold,
            max_masks=max_masks,
            naming_mode=naming_mode,
            vlm_model=vlm_model,
            seed=seed,
        )
        return {
            "success": True,
            "job_id": "job_sam3d_demo_reconstruct",
            "status": "succeeded",
            "blend_file_path": DEFAULT_SAM3D_RECONSTRUCT_BLEND_FILE_PATH,
            "num_objects": 4,
            "num_masks": 4,
            "partial_errors": [],
            "recommended_next_tool": "import_blend_contents",
            "recommended_next_action": (
                "Call import_blend_contents(blend_file_path=...) to merge the "
                "reconstructed scene into the current scene."
            ),
        }

    def import_blend_contents(
        blend_file_path: str,
        import_mode: str = "auto",
        collection_names: list[str] | str | None = None,
        object_names: list[str] | str | None = None,
        link: bool = False,
    ) -> str:
        recorder.record(
            "import_blend_contents",
            blend_file_path=blend_file_path,
            import_mode=import_mode,
            collection_names=collection_names,
            object_names=object_names,
            link=link,
        )
        recorder.ensure_object("Sam3DSceneRoot")
        return (
            f"Successfully imported blend file: {blend_file_path}\n"
            f"Import mode: {import_mode} (link={link})\n"
            "Imported 1 collection(s): Sam3DSceneCollection\n"
            "Imported 4 object(s): Sam3DSceneRoot, Floor, Wall, Table\n"
        )

    return [
        StructuredTool.from_function(
            func=get_scene_info,
            name="get_scene_info",
            description="Read current scene objects and transforms before or after reconstruction import.",
        ),
        StructuredTool.from_function(
            func=reconstruct_full_scene,
            name="reconstruct_full_scene",
            description=(
                "Reconstruct a full scene from a single reference image. For the current request image, "
                "leave input_image_name and input_image_id empty and let runtime resolve input_image_path."
            ),
            args_schema=_ReconstructFullSceneArgs,
        ),
        StructuredTool.from_function(
            func=import_blend_contents,
            name="import_blend_contents",
            description="Import a reconstructed .blend scene into the current Blender scene.",
            args_schema=_ImportBlendContentsArgs,
        ),
    ]
