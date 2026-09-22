"""Scoped document capabilities and authenticated downloads from IronClaw."""

import asyncio
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

CAPABILITIES = frozenset(
    "builtin." + name
    for name in (
        "read_file",
        "write_file",
        "list_dir",
        "glob",
        "grep",
        "document_edit",
        "html_to_pdf",
    )
)
MAX_DOWNLOAD = 25 * 1024 * 1024


def workspace_path(value):
    if (
        not value.startswith("/workspace/")
        or value.endswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/")[1:])
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise HTTPException(422, "Choose a file inside the document workspace")
    return value


def router(runtime):
    routes = APIRouter(prefix="/api/companion/threads")

    @routes.get(
        "/{thread_id}/files/content",
        response_class=Response,
        responses={
            200: {
                "content": {
                    "application/octet-stream": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                }
            }
        },
    )
    async def download(thread_id: UUID, path: str = Query(max_length=2048)):
        path = workspace_path(path)
        route = (
            "/api/webchat/v2/threads/"
            + str(thread_id)
            + "/files/content?"
            + urlencode({"path": path})
        )
        try:
            token = (
                runtime.token
                if runtime.token is not None
                else runtime.token_path.read_text().strip()
            )
            async with asyncio.timeout(30):
                async with runtime.client.stream(
                    "GET", route, headers={"Authorization": "Bearer " + token}
                ) as upstream:
                    if upstream.status_code != 200:
                        raise HTTPException(
                            upstream.status_code
                            if upstream.status_code in {403, 404}
                            else 502,
                            "Document is unavailable from the runtime",
                        )
                    declared = upstream.headers.get("content-length")
                    if declared is not None and (
                        int(declared) < 0 or int(declared) > MAX_DOWNLOAD
                    ):
                        raise HTTPException(502, "Document exceeds the download limit")
                    body = bytearray()
                    async for chunk in upstream.aiter_bytes():
                        if len(body) + len(chunk) > MAX_DOWNLOAD:
                            raise HTTPException(
                                502, "Document exceeds the download limit"
                            )
                        body.extend(chunk)
        except (OSError, AttributeError, ValueError, httpx.HTTPError, TimeoutError):
            raise HTTPException(
                502, "Document download failed; retry explicitly"
            ) from None
        filename = PurePosixPath(path).name
        return Response(
            bytes(body),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(filename, safe=""),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    return routes
