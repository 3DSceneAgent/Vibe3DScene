from __future__ import annotations

from collections.abc import AsyncIterator

import aiohttp
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse


class OwnerProxyError(Exception):
    """Raised when owner proxy forwarding fails."""


_HOP_HEADER = "X-Session-Proxy-Hop"
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}


def _sanitize_request_headers(headers: dict[str, str]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in _HOP_BY_HOP_HEADERS:
            continue
        cleaned[key] = value
    return cleaned


def _sanitize_response_headers(headers: aiohttp.typedefs.LooseHeaders) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for raw_key, raw_value in headers.items():
        key = str(raw_key)
        if key.lower() in _HOP_BY_HOP_HEADERS:
            continue
        cleaned[key] = str(raw_value)
    return cleaned


def _build_target_url(owner_url: str, request: Request) -> str:
    base = owner_url.rstrip("/")
    path = request.url.path
    if request.url.query:
        return f"{base}{path}?{request.url.query}"
    return f"{base}{path}"


async def _stream_upstream(
    *,
    upstream: aiohttp.ClientResponse,
    session: aiohttp.ClientSession,
) -> AsyncIterator[bytes]:
    try:
        async for chunk in upstream.content.iter_chunked(1024):
            if chunk:
                yield chunk
    finally:
        upstream.close()
        await session.close()


async def forward_request_to_owner(
    *,
    request: Request,
    owner_url: str,
    timeout_seconds: int,
) -> Response:
    hop_raw = request.headers.get(_HOP_HEADER, "0").strip()
    try:
        hop_count = int(hop_raw)
    except ValueError:
        hop_count = 0
    if hop_count >= 1:
        raise HTTPException(status_code=508, detail="Owner proxy hop limit exceeded.")

    target_url = _build_target_url(owner_url, request)
    payload = await request.body()
    upstream_headers = _sanitize_request_headers(dict(request.headers.items()))
    upstream_headers[_HOP_HEADER] = str(hop_count + 1)

    timeout = aiohttp.ClientTimeout(total=max(1, timeout_seconds))
    session = aiohttp.ClientSession(timeout=timeout)
    try:
        upstream = await session.request(
            request.method,
            target_url,
            headers=upstream_headers,
            data=payload if payload else None,
            allow_redirects=False,
        )
    except Exception as exc:
        await session.close()
        raise OwnerProxyError(f"Failed to proxy request to owner {owner_url}: {exc!r}") from exc

    headers = _sanitize_response_headers(upstream.headers)
    content_type = upstream.headers.get("Content-Type", "")
    if content_type.startswith("text/event-stream"):
        return StreamingResponse(
            _stream_upstream(upstream=upstream, session=session),
            status_code=upstream.status,
            media_type="text/event-stream",
            headers=headers,
        )

    body = await upstream.read()
    upstream.close()
    await session.close()

    media_type = content_type.split(";", 1)[0].strip() if content_type else None
    return Response(
        content=body,
        status_code=upstream.status,
        media_type=media_type,
        headers=headers,
    )
