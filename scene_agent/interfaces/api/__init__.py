"""FastAPI package entrypoint for the 3D scene agent."""

from . import shared as _shared
from .routes_assets import router as assets_router
from .routes_chat import router as chat_router
from .routes_runtime import router as runtime_router
from .routes_scene import router as scene_router
from .routes_system import router as system_router

app = _shared.app
run_api = _shared.run_api

# Preserve broad attribute compatibility for existing imports.
globals().update(
    {
        key: value
        for key, value in vars(_shared).items()
        if not key.startswith("__")
    }
)

if not getattr(app.state, "_split_routes_registered", False):
    app.include_router(system_router)
    app.include_router(chat_router)
    app.include_router(scene_router)
    app.include_router(assets_router)
    app.include_router(runtime_router)
    app.state._split_routes_registered = True

__all__ = ["app", "run_api"]
