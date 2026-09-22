"""Owner-authenticated proxy to an optional token-protected loopback speech worker."""

import hashlib
import hmac
import json
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from .voice_catalog import STOCK_VOICES
from .voice_protocol import MAX_OUTPUT_SECONDS, OUTPUT_RATE, Cancel, Capture, Speak
from .voice_stream import OwnedStream, VoiceStreamingResponse


class LocalVoice:
    def __init__(self, directory=None, *, transport=None):
        self.directory = directory or Path.home() / ".local/share/leam-next/voice"
        self.client = httpx.AsyncClient(
            base_url="http://127.0.0.1:46440",
            timeout=httpx.Timeout(45, connect=2),
            transport=transport,
            trust_env=False,
        )

    def headers(self, session):
        path = self.directory / "worker-token"
        try:
            token = path.read_text().strip()
            if len(token) < 40 or path.stat().st_mode & 0o077:
                raise ValueError("Invalid private token")
        except (OSError, ValueError):
            raise HTTPException(
                503, "Local speech is not installed or configured"
            ) from None
        return {
            "Authorization": "Bearer " + token,
            "X-Leam-Owner": hmac.new(
                token.encode(), session.encode(), hashlib.sha256
            ).hexdigest(),
        }

    async def request(self, path, session, body=None):
        try:
            async with self.client.stream(
                "GET" if body is None else "POST",
                path,
                headers=self.headers(session),
                json=body,
            ) as response:
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 64000:
                        raise HTTPException(
                            502, "Local speech response exceeded its limit"
                        )
                if response.status_code != 200:
                    raise HTTPException(
                        response.status_code
                        if response.status_code in (409, 422, 503)
                        else 502,
                        "Local speech is busy or unavailable; retry with a new session",
                    )
                return json.loads(raw)
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "Local speech is unavailable") from None

    async def speech(self, body, session, valid):
        context = self.client.stream(
            "POST",
            "/speak",
            headers=self.headers(session),
            json=body.model_dump(mode="json"),
        )
        try:
            response = await context.__aenter__()
        except httpx.HTTPError:
            raise HTTPException(503, "Local speech is unavailable") from None
        if response.status_code != 200:
            await context.__aexit__(None, None, None)
            raise HTTPException(
                response.status_code if response.status_code in (409, 422) else 503,
                "Local speech is busy or unavailable",
            )

        async def close():
            await context.__aexit__(None, None, None)

        async def chunks():
            total = 0
            try:
                async for chunk in response.aiter_bytes():
                    if not valid(session):
                        return
                    total += len(chunk)
                    if total > MAX_OUTPUT_SECONDS * OUTPUT_RATE * 6 + 100000:
                        yield b'{"error":"Local speech response exceeded its limit"}\n'
                        return
                    yield chunk
            except httpx.HTTPError:
                yield b'{"error":"Local speech connection stopped"}\n'

        return VoiceStreamingResponse(
            OwnedStream(chunks(), close), media_type="application/x-ndjson"
        )

    async def close(self):
        await self.client.aclose()


class VoiceRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded_validation(request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(
                    {"detail": "Invalid bounded speech request"}, status_code=422
                )

        return bounded_validation


def router(voice, valid):
    routes = APIRouter(prefix="/api/voice", route_class=VoiceRoute)

    def session(request):
        value = request.cookies.get("leam_session", "")
        if not valid(value):
            raise HTTPException(401, "Sign in to Leam")
        return value

    @routes.get("/status")
    async def status(request: Request):
        owner = session(request)
        try:
            result = await voice.request("/status", owner)
            installed = result.get("voices", [])
            if not isinstance(installed, list):
                installed = []
            # Do not propagate any optional worker paths/configuration/error details.
            return {
                "ready": result.get("ready") is True,
                "input": "moonshine",
                "output": "pocket",
                "language": "en",
                "voices": [name for name in STOCK_VOICES if name in installed],
                "busy": result.get("busy") is True,
                "retainsAudio": False,
            }
        except HTTPException:
            return {
                "ready": False,
                "input": "moonshine",
                "output": "pocket",
                "language": "en",
                "voices": [],
                "notice": "Local speech is not running. Browser speech remains available.",
            }

    @routes.post("/capture")
    async def capture(body: Capture, request: Request):
        owner = session(request)
        try:
            body.samples()
        except ValueError:
            raise HTTPException(422, "Invalid bounded PCM samples") from None
        result = await voice.request("/capture", owner, body.model_dump(mode="json"))
        if not valid(owner):
            raise HTTPException(401, "Sign in to Leam")
        if (
            result.get("captureId") != str(body.captureId)
            or result.get("sequence") != body.sequence
        ):
            raise HTTPException(502, "Recognition response identity changed")
        if (
            any(
                not isinstance(result.get(key), str) or len(result[key]) > 20000
                for key in ("finalText", "interimText")
            )
            or result.get("finished") is not body.finish
        ):
            raise HTTPException(502, "Invalid bounded recognition response")
        return {
            key: result[key]
            for key in ("captureId", "sequence", "finished", "finalText", "interimText")
        }

    @routes.post("/capture/cancel")
    async def cancel(body: Cancel, request: Request):
        return await voice.request(
            "/capture/cancel", session(request), body.model_dump(mode="json")
        )

    @routes.post("/speak")
    async def speak(body: Speak, request: Request):
        return await voice.speech(body, session(request), valid)

    return routes
