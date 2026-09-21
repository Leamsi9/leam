"""Narrow adapter for pinned IronClaw 1.4. Runtime tokens never leave this process."""

import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx


class RuntimeError(Exception):
    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class IronClaw:
    def __init__(
        self, url: str, token_path: Path | None, *, token=None, transport=None
    ):
        parsed = urlparse(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in ["127.0.0.1", "localhost", "::1"]
            or parsed.username
            or parsed.password
        ):
            raise ValueError("The runtime adapter requires a local loopback URL")
        self.token_path = token_path
        self.token = token
        self.client = httpx.AsyncClient(
            base_url=url,
            timeout=30,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def request(self, method, path, body=None):
        try:
            token = (
                self.token
                if self.token is not None
                else self.token_path.read_text().strip()
            )
        except (OSError, AttributeError):
            raise RuntimeError("IronClaw is not paired with this Leam installation")
        try:
            response = await self.client.request(
                method,
                "/api/webchat/v2" + path,
                json=body,
                headers={"authorization": "Bearer " + token},
            )
        except httpx.HTTPError:
            raise RuntimeError(
                "IronClaw is unreachable. Check runtime status in Settings."
            )
        if not response.is_success:
            # Provider diagnostics may contain credentials or request payloads.
            raise RuntimeError(
                f"IronClaw rejected this operation (HTTP {response.status_code}). Check provider configuration and runtime diagnostics.",
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError:
            raise RuntimeError("IronClaw returned an invalid response")

    async def events(self, path, cursor=None):
        """Bounded canonical browser events, using server-held runtime credentials."""
        try:
            token = (
                self.token
                if self.token is not None
                else self.token_path.read_text().strip()
            )
            headers = {
                "authorization": "Bearer " + token,
                "accept": "text/event-stream",
            }
            if cursor:
                headers["last-event-id"] = cursor
            async with asyncio.timeout(65):
                async with self.client.stream(
                    "GET",
                    "/api/webchat/v2" + path,
                    headers=headers,
                    timeout=httpx.Timeout(30, read=25),
                ) as response:
                    if response.status_code != 200 or not response.headers.get(
                        "content-type", ""
                    ).startswith("text/event-stream"):
                        raise RuntimeError("Runtime event stream unavailable")
                    pending = b""
                    async for chunk in response.aiter_bytes():
                        pending += chunk
                        pending = pending.replace(b"\r\n", b"\n")
                        while b"\n\n" in pending:
                            frame, pending = pending.split(b"\n\n", 1)
                            if len(frame) > 262144:
                                raise RuntimeError("Runtime event frame too large")
                            lines = frame.decode("utf-8").splitlines()
                            name = next(
                                (
                                    line[6:].strip()
                                    for line in lines
                                    if line.startswith("event:")
                                ),
                                "message",
                            )
                            data = "\n".join(
                                line[5:].lstrip()
                                for line in lines
                                if line.startswith("data:")
                            )
                            event_id = next(
                                (
                                    line[3:].strip()
                                    for line in lines
                                    if line.startswith("id:")
                                ),
                                None,
                            )
                            if not data:
                                yield b": keepalive\n\n"
                                continue
                            if (
                                len(name) > 64
                                or not name.replace("_", "").isalnum()
                                or event_id
                                and (len(event_id) > 4096 or "\x00" in event_id)
                            ):
                                raise RuntimeError("Invalid runtime event")
                            payload = json.loads(data)
                            if not isinstance(payload, dict):
                                raise RuntimeError("Invalid runtime event")
                            if name == "stream_error":
                                payload = {
                                    "error": "runtime_stream_unavailable",
                                    "retryable": payload.get("retryable") is True,
                                }
                            result = (
                                ("id: " + event_id + "\n" if event_id else "")
                                + "event: "
                                + name
                                + "\ndata: "
                                + json.dumps(payload, separators=(",", ":"))
                                + "\n\n"
                            )
                            yield result.encode()
                        if len(pending) > 262144:
                            raise RuntimeError("Runtime event frame too large")
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            # Periodic reconnect rechecks the Leam session and resumes from the cursor.
            return
        except (OSError, AttributeError, httpx.HTTPError, ValueError, RuntimeError):
            yield b'event: stream_error\ndata: {"error":"runtime_stream_unavailable","retryable":true}\n\n'

    async def close(self):
        await self.client.aclose()
