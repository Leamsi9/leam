"""Optional private loopback speech worker. Models load locally before serving."""

import asyncio
import base64
import hmac
import json
import os
import queue
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .voice_catalog import installed_voices
from .voice_protocol import (
    MAX_OUTPUT_SECONDS,
    MAX_SECONDS,
    OUTPUT_RATE,
    SAMPLE_RATE,
    Cancel,
    Capture,
    Speak,
)
from .voice_stream import OwnedStream, VoiceStreamingResponse


async def native(fn, *args, on_cancel=None):
    """Keep ownership until the actual thread settles, even during task shutdown."""
    loop = asyncio.get_running_loop()
    settled = loop.create_future()

    def resolve(value, error):
        if error is None:
            settled.set_result(value)
        else:
            settled.set_exception(error)

    def run():
        try:
            value = fn(*args)
        except BaseException as error:  # noqa: BLE001 -- always settle native completion
            loop.call_soon_threadsafe(resolve, None, error)
        else:
            loop.call_soon_threadsafe(resolve, value, None)

    # Unlike a to_thread Task, this completion Future is not cancelled by
    # asyncio's all-task shutdown. The native call itself cannot be cancelled.
    threading.Thread(target=run, daemon=True, name="leam-voice-native").start()
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(settled)
            break
        except asyncio.CancelledError:
            cancelled = True
            if settled.done():
                result = settled.result()
                break
    if cancelled:
        if on_cancel is not None:
            await native(on_cancel, result)
        raise asyncio.CancelledError
    return result


class SpeechEngine:
    """Only installed English models and verified stock voices; no remote inputs."""

    def __init__(self, directory):
        import torch
        from moonshine_voice import ModelArch, Transcriber
        from pocket_tts import TTSModel

        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        config = json.loads((directory / "models.json").read_text())
        self.transcriber = Transcriber(
            str(directory / config["moonshine"]),
            model_arch=ModelArch.SMALL_STREAMING,
            update_interval=0.5,
            options={
                "log_output_text": "false",
                "log_api_calls": "false",
                "log_ort_run": "false",
                "return_audio_data": "false",
            },
        )
        self.tts = TTSModel.load_model(config=directory / "pocket.yaml")
        self.tts.has_voice_cloning = False
        self.directory = directory
        self.voices = installed_voices(directory, config)
        self.voice_name = "alba" if "alba" in self.voices else self.voices[0]
        self.voice = self.tts.get_state_for_audio_prompt(
            directory / f"{self.voice_name}.safetensors"
        )
        if self.tts.sample_rate != OUTPUT_RATE:
            raise ValueError("Unexpected installed voice sample rate")

    def begin(self):
        stream = self.transcriber.create_stream(update_interval=120)
        stream.start()
        return stream

    def capture(self, stream, samples, finish):
        if samples:
            stream.add_audio(samples, SAMPLE_RATE)
        transcript = stream.stop() if finish else stream.update_transcription()
        final, partial = [], []
        for line in transcript.lines if transcript else []:
            (final if line.is_complete or finish else partial).append(line.text)
        return {
            "finalText": " ".join(final)[:20000],
            "interimText": " ".join(partial)[:20000],
        }

    def close_capture(self, stream):
        stream.close()

    def speak(self, text, voice):
        if voice not in self.voices:
            raise ValueError("Stock voice is not installed")
        if voice != self.voice_name:
            state = self.tts.get_state_for_audio_prompt(
                self.directory / f"{voice}.safetensors"
            )
            self.voice, self.voice_name = state, voice
        for chunk in self.tts.generate_audio_stream(self.voice, text):
            # Official generator completes and joins its decoder only when drained.
            yield chunk.detach().cpu().numpy().astype("<f4").tobytes()


class Worker:
    def __init__(self, engine, clock=time.monotonic):
        self.engine, self.clock = engine, clock
        self.lock = asyncio.Lock()
        self.capture_state = None
        self.output = False
        self.retired = OrderedDict()

    def drop_capture(self):
        current, self.capture_state = self.capture_state, None
        if current:
            self.retired[current["identity"]] = True
            if len(self.retired) > 512:
                self.retired.popitem(last=False)
            self.engine.close_capture(current["stream"])

    def expire(self):
        if self.capture_state and self.clock() - self.capture_state["at"] > 120:
            self.drop_capture()

    async def capture(self, owner, body):
        samples = body.samples()
        if self.lock.locked():
            raise HTTPException(409, "Local speech is busy; try again when it finishes")
        async with self.lock:
            await native(self.expire)
            identity = (owner, str(body.captureId))
            if identity in self.retired:
                raise HTTPException(409, "Capture has ended; start a new session")
            current = self.capture_state
            if current and current["identity"] != identity:
                raise HTTPException(409, "Another microphone session is active")
            if current is None:
                if body.sequence != 0:
                    raise HTTPException(409, "Recognition session expired; start again")
                current = {
                    "identity": identity,
                    "stream": await native(
                        self.engine.begin, on_cancel=self.engine.close_capture
                    ),
                    "sequence": 0,
                    "samples": 0,
                    "at": self.clock(),
                }
                self.capture_state = current
            if body.sequence != current["sequence"]:
                raise HTTPException(409, "Audio sequence changed; start a new capture")
            if current["samples"] + len(samples) > MAX_SECONDS * SAMPLE_RATE:
                await native(self.drop_capture)
                raise HTTPException(422, "Recognition is limited to 90 seconds")
            try:
                result = await native(
                    self.engine.capture, current["stream"], samples, body.finish
                )
                current["sequence"] += 1
                current["samples"] += len(samples)
                if body.finish:
                    await native(self.drop_capture)
                return {
                    **result,
                    "captureId": str(body.captureId),
                    "sequence": body.sequence,
                    "finished": body.finish,
                }
            except BaseException:
                await native(self.drop_capture)
                raise

    async def cancel(self, owner, body):
        async with self.lock:
            if self.capture_state and self.capture_state["identity"] == (
                owner,
                str(body.captureId),
            ):
                await native(self.drop_capture)
        return {"cancelled": True}

    async def prepare_speech(self, body):
        if body.voice not in getattr(self.engine, "voices", ("alba",)):
            raise HTTPException(422, "Selected stock voice is not installed")
        if self.lock.locked():
            raise HTTPException(409, "Local speech is busy; try again when it finishes")
        await self.lock.acquire()
        try:
            await native(self.expire)
            if self.capture_state:
                raise HTTPException(
                    409, "Stop microphone capture before local playback"
                )
            self.output = True
            return self.audio(body)
        except BaseException:
            self.output = False
            self.lock.release()
            raise

    def audio(self, body):
        # Producer drains the current bounded phrase on cancellation. It never releases
        # the model for a new inference while Pocket's own threads still use it.
        pending = queue.Queue(maxsize=4)
        stopped = threading.Event()
        done = threading.Event()
        loop = asyncio.get_running_loop()

        def produce():
            try:
                total = 0
                for raw in self.engine.speak(body.text, body.voice):
                    total += len(raw)
                    if (
                        not raw
                        or len(raw) % 4
                        or len(raw) > OUTPUT_RATE * 4
                        or total > MAX_OUTPUT_SECONDS * OUTPUT_RATE * 4
                    ):
                        stopped.set()
                        continue
                    while not stopped.is_set():
                        try:
                            pending.put(raw, timeout=0.1)
                            break
                        except queue.Full:
                            pass
            except Exception:  # noqa: BLE001 -- sanitize arbitrary native/library failures
                stopped.set()
            finally:
                done.set()
                loop.call_soon_threadsafe(self.finish_output)

        threading.Thread(target=produce, daemon=True, name="leam-voice-output").start()

        async def close():
            stopped.set()
            while not pending.empty():
                pending.get_nowait()

        async def stream():
            count = 0
            try:
                while not done.is_set() or not pending.empty():
                    if stopped.is_set():
                        yield b'{"error":"Local speech stopped"}\n'
                        return
                    try:
                        raw = pending.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(0.01)
                        continue
                    yield (
                        json.dumps(
                            {
                                "sequence": count,
                                "sampleRate": OUTPUT_RATE,
                                "pcm": base64.b64encode(raw).decode(),
                            }
                        )
                        + "\n"
                    ).encode()
                    count += 1
                yield (
                    b'{"done":true}\n'
                    if not stopped.is_set()
                    else b'{"error":"Local speech stopped"}\n'
                )
            finally:
                await close()

        # aclose must work before the generator's first iteration as well.
        return OwnedStream(stream(), close)

    def finish_output(self):
        self.output = False
        self.lock.release()


def create_worker(engine, token):
    if len(token) < 40:
        raise ValueError("Worker token is missing or too short")
    worker = Worker(engine)

    @asynccontextmanager
    async def lifespan(app):
        async def expire_idle():
            while True:
                await asyncio.sleep(5)
                if not worker.lock.locked():
                    async with worker.lock:
                        await native(worker.expire)

        cleanup = asyncio.create_task(expire_idle())
        try:
            yield
        finally:
            cleanup.cancel()
            await asyncio.gather(cleanup, return_exceptions=True)
            while worker.output:
                await asyncio.sleep(0.05)
            if worker.capture_state:
                await native(engine.close_capture, worker.capture_state["stream"])
                worker.capture_state = None

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.worker = worker

    @app.middleware("http")
    async def security(request, call_next):
        if request.headers.get("origin") or not hmac.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + token
        ):
            return JSONResponse(
                {"detail": "Worker authentication required"}, status_code=401
            )
        if request.client and request.client.host not in {"127.0.0.1", "::1"}:
            return JSONResponse({"detail": "Loopback only"}, status_code=403)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 100000:
                return JSONResponse(
                    {"detail": "Audio request too large"}, status_code=413
                )
        request._body = bytes(body)
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 -- no native/audio details in HTTP errors
            return JSONResponse(
                {"detail": "Local speech failed; retry with a new session"},
                status_code=503,
            )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation(request, error):
        return JSONResponse(
            {"detail": "Invalid bounded speech request"}, status_code=422
        )

    def owner(request):
        value = request.headers.get("x-leam-owner", "")
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise HTTPException(401, "Session binding required")
        return value

    @app.get("/status")
    async def status():
        return {
            "ready": True,
            "input": "moonshine",
            "output": "pocket",
            "language": "en",
            "voices": list(getattr(engine, "voices", ("alba",))),
            "busy": worker.lock.locked() or worker.capture_state is not None,
            "retainsAudio": False,
        }

    @app.post("/capture")
    async def capture(body: Capture, request: Request):
        try:
            return await worker.capture(owner(request), body)
        except ValueError:
            raise HTTPException(422, "Invalid bounded PCM samples") from None

    @app.post("/capture/cancel")
    async def cancel(body: Cancel, request: Request):
        return await worker.cancel(owner(request), body)

    @app.post("/speak")
    async def speak(body: Speak, request: Request):
        owner(request)
        return VoiceStreamingResponse(
            await worker.prepare_speech(body), media_type="application/x-ndjson"
        )

    return app


def main():
    import argparse
    import logging

    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
    os.environ.pop("DEBUG_MIMI", None)
    os.environ.pop("POCKET_TTS_SAVE_WEIGHTS", None)
    os.environ.update(
        OMP_NUM_THREADS="2",
        MKL_NUM_THREADS="2",
        # ORT otherwise creates host-wide pools that exhaust the service CPU quota.
        MOONSHINE_ORT_SINGLE_THREAD="1",
        HF_HUB_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
    )
    logging.disable(logging.CRITICAL)
    token_path = args.directory / "worker-token"
    if token_path.stat().st_mode & 0o077:
        raise ValueError("Worker token must be private")
    engine = SpeechEngine(args.directory)
    uvicorn.run(
        create_worker(engine, token_path.read_text().strip()),
        host="127.0.0.1",
        port=46440,
        access_log=False,
        log_level="critical",
    )


if __name__ == "__main__":
    main()
