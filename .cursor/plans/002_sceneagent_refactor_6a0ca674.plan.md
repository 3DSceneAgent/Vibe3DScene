---
name: SceneAgent Refactor
overview: Move backend Python packages into a dedicated `scene_agent/` package and update imports/entrypoints accordingly, while preserving the current CLI/API behavior. Also document how headless mode is wired in code so the runtime expectations are clear.
todos:
  - id: create-package
    content: Create `scene_agent/` pkg; move backend dirs+config
    status: completed
  - id: update-imports
    content: Rewrite imports to `scene_agent.*` everywhere
    status: completed
    dependencies:
      - create-package
  - id: entrypoint-check
    content: Update root `main.py` to new imports
    status: completed
    dependencies:
      - update-imports
  - id: smoke-check
    content: Run quick lint/import check if needed
    status: completed
    dependencies:
      - entrypoint-check
isProject: false
---

# SceneAgent Backend Refactor Plan

## Scope

- Move backend modules into a new `scene_agent/` package and update all imports to absolute `scene_agent.*` paths.
- Keep `main.py` at repo root as the entrypoint, updating it to import from `scene_agent`.

## Key Changes

- Create `scene_agent/` package with `__init__.py` and move folders/files:
- `blender/`, `interfaces/`, `memory/`, `rag/`, `tools/`, `vlm/`, `agent/`, `config.py`
- Update all internal imports to `scene_agent.*` (e.g., `from scene_agent.config import get_settings`).
- Update tests and any docs that reference old import paths or directory layout.

## Files/Areas to Touch

- `[\/Users\/fishwowater\/projects\/blender-mcp-vision\/thirdparty\/3DSceneAgent\/scene_agent\/__init__.py](/Users/fishwowater/projects/blender-mcp-vision/thirdparty/3DSceneAgent/scene_agent/__init__.py)`
- `[\/Users\/fishwowater\/projects\/blender-mcp-vision\/thirdparty\/3DSceneAgent\/main.py](/Users/fishwowater/projects/blender-mcp-vision/thirdparty/3DSceneAgent/main.py)`
- All moved modules under `[\/Users\/fishwowater\/projects\/blender-mcp-vision\/thirdparty\/3DSceneAgent\/scene_agent\/](/Users/fishwowater/projects/blender-mcp-vision/thirdparty/3DSceneAgent/scene_agent/)`
- Tests under `[\/Users\/fishwowater\/projects\/blender-mcp-vision\/thirdparty\/3DSceneAgent\/tests](/Users/fishwowater/projects/blender-mcp-vision/thirdparty/3DSceneAgent/tests)`
- Docs that show layout/imports: `[\/Users\/fishwowater\/projects\/blender-mcp-vision\/thirdparty\/3DSceneAgent\/README.md](/Users/fishwowater/projects/blender-mcp-vision/thirdparty/3DSceneAgent/README.md)` (and any spec/quickstart that references paths)

## Implementation Todos

- `create-package`: Add `scene_agent/` package and move backend directories + `config.py`.
- `update-imports`: Rewrite imports to `scene_agent.*` across code/tests/docs.
- `entrypoint-check`: Update `main.py` to import `scene_agent.interfaces.*`.
- `smoke-check`: Quick lint/import sanity check (no runtime execution).

