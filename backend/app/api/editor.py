"""Streaming reverse proxy onto a forge-scad-editor instance.

Forwards `/editor/*` verbatim (same path, no rewriting) to
`{FORGE_EDITOR_URL}/editor/*` — the editor container serves its SPA *and*
its own API under that one prefix (see forge-scad-editor's main.py), so one
unmodified-path rule is all this needs. Mounted before the SPA catch-all in
main.py, which would otherwise swallow `/editor/*` — the same ordering rule
the MCP mount already follows.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .deps import State

log = logging.getLogger(__name__)

router = APIRouter(prefix="/editor", tags=["editor"])

# Headers that are connection-scoped, not resource-scoped — passing these
# through verbatim would either be meaningless cross-hop or actively wrong
# (e.g. a stale Content-Length after we've re-chunked the body).
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
}


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(state: State, path: str, request: Request) -> StreamingResponse:
    editor_url = state.settings.editor_url
    if not editor_url:
        raise HTTPException(
            status_code=503, detail="editor is not configured (FORGE_EDITOR_URL is empty)"
        )

    upstream_url = f"{editor_url.rstrip('/')}/editor/{path}"
    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in {"host", *_HOP_BY_HOP}
    }

    client = httpx.AsyncClient()
    try:
        upstream_request = client.build_request(
            request.method,
            upstream_url,
            params=request.query_params,
            headers=headers,
            content=request.stream(),  # streamed, not buffered — R5
        )
        upstream_response = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"editor unreachable: {exc}") from exc

    # Preserves Content-Type, Content-Length, ETag, Cache-Control as-is —
    # what lets the browser cache the ~9.6MB wasm across visits (R5,
    # verified in Phase 7: a second load served it from cache, not the
    # network).
    response_headers = {
        k: v for k, v in upstream_response.headers.items() if k.lower() not in _HOP_BY_HOP
    }

    async def body_stream():
        try:
            async for chunk in upstream_response.aiter_bytes():
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    return StreamingResponse(
        body_stream(),
        status_code=upstream_response.status_code,
        headers=response_headers,
    )
