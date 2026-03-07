"""Router heuristic constants."""

from __future__ import annotations

# Lightweight lexical heuristics for router fallback/clarification prompts.
_PLAN_INTENT_MARKERS: tuple[str, ...] = (
    "recreate",
    "replicate",
    "match this scene",
    "rebuild scene",
    "entire scene",
    "full scene",
    "from reference",
    "according to reference",
    "layout",
    "composition",
    "lighting and materials",
)

_ACTION_INTENT_MARKERS: tuple[str, ...] = (
    "add ",
    "create ",
    "generate ",
    "import ",
    "place ",
    "move ",
    "rotate ",
    "scale ",
    "delete ",
    "remove ",
    "arrange ",
    "set texture",
)

_IMAGE_QA_MARKERS: tuple[str, ...] = (
    "this image",
    "the image",
    "in the image",
    "what is in",
    "what's in",
)

ROUTER_MIN_CONFIDENCE = 0.65
