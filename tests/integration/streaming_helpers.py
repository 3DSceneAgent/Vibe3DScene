from __future__ import annotations

import json
from typing import Any, Iterable, Sequence


def collect_sse_payloads(lines: Iterable[bytes | str], limit: int | None = None) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for raw in lines:
        if not raw:
            continue
        if isinstance(raw, bytes):
            decoded = raw.decode("utf-8")
        else:
            decoded = raw
        if not decoded.startswith("data:"):
            continue
        data = decoded.replace("data:", "", 1).strip()
        if not data:
            continue
        payload = json.loads(data)
        if isinstance(payload, dict):
            payloads.append(payload)
        if limit is not None and len(payloads) >= limit:
            break
    return payloads


def find_payload(payloads: Sequence[dict[str, Any]], key: str) -> dict[str, Any] | None:
    for payload in payloads:
        if key in payload:
            return payload
    return None
