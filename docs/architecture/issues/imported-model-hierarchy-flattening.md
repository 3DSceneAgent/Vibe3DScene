# Imported Model Hierarchy Flattening

**Date:** 2026-03-08
**Status:** Proposed
**Scope:** addon (asset_handlers), mcp_server (sketchfab, base), scene tools (get_scene_info, get_object_info)

## Problem

Models imported from external sources (Sketchfab, Objaverse, PolyHaven, etc.) frequently contain
nested hierarchies with empty parent nodes that carry non-identity transforms. A typical Sketchfab
model hierarchy looks like:

```
Empty ("model_root")              ← scale=(100,100,100), rotation=(90°,0,0)
  └─ Empty ("Armature" / "group") ← another transform layer
       └─ Mesh ("actual_object")  ← local scale=(1,1,1), inherits parent transforms
```

This causes several cascading failures for the agent:

1. **Blind hierarchy:** `get_scene_info` and `get_object_info` do not report parent-child
   relationships. The agent cannot see the hierarchy at all.

2. **Misleading local transforms:** `get_object_info` returns the mesh's local `scale` as
   `(1,1,1)` when the effective world scale is `(100,100,100)` inherited from parents.

3. **Destructive parent deletion:** When the agent deletes a "useless" empty parent, even with
   `detach_keep_world` mode (which preserves `matrix_world`), the child's local `scale` becomes
   `(100,100,100)`. Subsequent agent operations on `obj.scale` produce unexpected results.

4. **Double-scaling:** When the agent tries to scale a child mesh directly, it doesn't account for
   the parent's inherited transform, leading to compounded scaling errors.

## Affected Import Paths

| Import Path | Entry Point | Has Scale Normalization | Has Hierarchy Flatten |
|-------------|-------------|------------------------|----------------------|
| Sketchfab | `sketchfab.py` → `_build_blender_import_code` | Yes (target_size) | **No** |
| Objaverse / Retrieval | `base.py` → `import_glb_model` → addon `import_glb_model` | **No** | **No** |
| Rodin | `rodin.py` → `import_glb_model` | **No** | **No** |
| TRELLIS2 | `trellis2.py` → `import_glb_model` | **No** | **No** |
| SAMServer | `sam_reconstruct.py` → `glb_import.py` | Partial (origin set) | **No** |
| PolyHaven | addon `asset_handlers.py` | **No** | **No** |
| .blend import | addon `import_blend_contents` | **No** | **No** (preserves collections) |

## Proposed Solution

A multi-layered approach, ordered by priority.

### P0: Flatten hierarchy in post-import hook

Create a shared utility in `addon/blender_mcpv_addon/asset_handlers.py`:

```python
def _flatten_imported_hierarchy(imported_objects):
    """Bake parent transforms into mesh data and remove empty wrapper nodes.

    For models that are already flat (no empty parents, identity transforms),
    this is effectively a no-op.
    """
    import bpy

    mesh_objects = [obj for obj in imported_objects if obj.type == 'MESH']
    empties_in_set = {obj for obj in imported_objects if obj.type == 'EMPTY'}

    # Step 1: Unparent all meshes, preserving world transform
    for obj in mesh_objects:
        if obj.parent is not None:
            world_matrix = obj.matrix_world.copy()
            obj.parent = None
            obj.matrix_world = world_matrix

    # Step 2: Apply visual transforms (rotation + scale) so local = world
    bpy.ops.object.select_all(action='DESELECT')
    for obj in mesh_objects:
        obj.select_set(True)
    if mesh_objects:
        bpy.context.view_layer.objects.active = mesh_objects[0]
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    # Step 3: Remove orphaned empties from the import set
    bpy.context.view_layer.update()
    for empty in empties_in_set:
        if not empty.children:  # now orphaned
            bpy.data.objects.remove(empty, do_unlink=True)
```

Integrate into:
- `addon/.../asset_handlers.py :: import_glb_model()` — after import, before returning results
- `mcp_server/tools/asset_retrieval/sketchfab.py :: _build_blender_import_code()` — inline the
  same logic after scale normalization (since this path uses `execute_code`, not addon methods)

**Why Objaverse needs this too:** Objaverse-XL is a superset of Sketchfab and includes models from
many other sources (Thingiverse, GitHub, etc.) with equally unpredictable hierarchies. The shared
`import_glb_model` path currently does zero hierarchy cleanup. Adding the flatten hook here covers
Objaverse, Rodin, and TRELLIS2 imports in one change.

**Safety:** For models that are already flat (e.g., Rodin/TRELLIS2 generated GLBs which typically
have a single mesh with identity transforms), the flatten operation is a no-op — there are no
parents to remove and `transform_apply` on identity transforms changes nothing.

### P0: Enrich scene info with hierarchy data

In `addon/.../server_scene_tools_mixin.py`:

**`get_scene_info`** — add to each object entry:
```python
obj_info["parent"] = obj.parent.name if obj.parent else None
```

**`get_object_info`** — add:
```python
obj_info["parent"] = obj.parent.name if obj.parent else None
obj_info["children"] = [c.name for c in obj.children]
obj_info["world_location"] = list(obj.matrix_world.translation)
obj_info["world_scale"] = list(obj.matrix_world.to_scale())
```

This gives the agent visibility into the hierarchy and the effective world-space transforms, even
if flatten is not applied.

### P1: Add a standalone `flatten_hierarchy` MCP tool

Expose the flatten logic as an explicit tool the agent can call on-demand:

```python
def flatten_hierarchy(
    ctx: Context,
    object_names: list[str] | str,
) -> str:
    """Flatten hierarchy for specified objects: bake world transforms into mesh
    data, unparent, apply rotation+scale, remove orphaned empties."""
```

This handles cases where models were imported before the hook existed, or when the user manually
imports models via other means.

### P2: Agent strategy prompt update

Add to the strategy prompt:
- "Imported models (especially from Sketchfab/Objaverse) may have nested empty parents with
  non-identity transforms. Use `get_object_info` to check `world_scale` vs local `scale`."
- "Never delete parent Empty objects directly; use `flatten_hierarchy` first, or `delete_objects`
  with `detach_keep_world` / `reparent_to_parent_keep_world` mode."
- "When reading object scale, prefer `world_scale` over `scale` for imported models."

## Edge Cases

1. **Armatures:** Some Sketchfab models have `ARMATURE` type parents (not just `EMPTY`). The
   flatten logic should either skip armatures or handle them specially (unparent meshes from
   armature but keep armature if it has animation data).

2. **Multi-root imports:** A single GLTF file can contain multiple root objects. The flatten
   should handle each root independently.

3. **Shared mesh data:** Some models use instancing (multiple objects sharing the same mesh
   datablock). `transform_apply` on these will only affect one instance unless the mesh data is
   made single-user first (`obj.data = obj.data.copy()`).

4. **Collection hierarchy:** `.blend` imports preserve collection hierarchies intentionally.
   The flatten hook should NOT be applied to `.blend` imports by default.

## Testing

- Unit test: import a GLB with known nested hierarchy, verify post-flatten the mesh has no parent,
  local scale matches former world scale, and empty wrappers are removed.
- Regression test: import a flat GLB (single mesh, no parent), verify flatten is a no-op.
- Integration test: Sketchfab download → import → agent manipulates scale → verify correctness.
