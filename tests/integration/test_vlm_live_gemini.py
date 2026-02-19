from __future__ import annotations

import base64
import io
import json
import os
from typing import Any

import pytest
from PIL import Image, ImageDraw
from langchain_core.messages import HumanMessage

from scene_agent.vlm.providers import GeminiProvider


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    snippet = text[start : end + 1]
    try:
        parsed = json.loads(snippet)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _build_red_square_data_url() -> str:
    image = Image.new("RGB", (128, 128), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((32, 32, 96, 96), fill=(220, 20, 20))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def test_live_gemini_can_reach_and_understand_image():
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 to run live Gemini integration tests.")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        pytest.skip("Set GEMINI_API_KEY to run live Gemini integration tests.")

    model_name = os.getenv("GEMINI_LIVE_TEST_MODEL", "gemini-2.5-flash")
    try:
        provider = GeminiProvider(api_key=api_key, model=model_name)
        model = provider.get_chat_model()
    except Exception as exc:  # pragma: no cover - environment-dependent import/runtime
        pytest.skip(f"Gemini provider unavailable in this environment: {exc}")

    data_url = _build_red_square_data_url()
    prompt = (
        "You are validating image reachability and minimal visual understanding. "
        "Return JSON only with keys: reachable, dominant_color, main_shape. "
        "Use reachable=true if you can read the image. "
        "dominant_color must be one of [red, green, blue, white, black, other]. "
        "main_shape must be one of [square, rectangle, circle, triangle, other]."
    )
    response = model.invoke(
        [
            HumanMessage(
                content=[
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]
            )
        ]
    )
    response_text = _content_to_text(getattr(response, "content", response))
    parsed = _extract_json(response_text)

    # print(parsed)
    # open("demo.txt", "w").write(str(parsed))
    assert isinstance(parsed, dict), (
        "Gemini response is not parseable JSON; this fails the reachability check. "
        f"Raw response: {response_text}"
    )
    assert bool(parsed.get("reachable")) is True, (
        "Gemini did not confirm image reachability. "
        f"Parsed response: {parsed}"
    )

    dominant_color = str(parsed.get("dominant_color", "")).lower()
    main_shape = str(parsed.get("main_shape", "")).lower()
    assert "red" in dominant_color or dominant_color in {"red"}, (
        "Gemini failed minimal color semantics on the deterministic test image. "
        f"Parsed response: {parsed}"
    )
    assert (
        "square" in main_shape
        or "rectangle" in main_shape
        or main_shape in {"square", "rectangle"}
    ), (
        "Gemini failed minimal shape semantics on the deterministic test image. "
        f"Parsed response: {parsed}"
    )
