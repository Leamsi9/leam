import asyncio
import base64
import json
import struct
import threading
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.local_voice import LocalVoice
from leam_api.voice_protocol import Speak
from leam_api.voice_worker import create_worker

TOKEN = "test-worker-token-" + "x" * 48


class Engine:
    def __init__(self):
        self.opened = 0
        self.closed = 0
        self.samples = []
        self.spoken = []

    def begin(self):
        self.opened += 1
        return object()

    def capture(self, stream, samples, finish):
        self.samples.extend(samples)
        return {
            "finalText": "A synthetic transcript" if finish else "",
            "interimText": "A synthetic" if not finish else "",
        }

    def close_capture(self, stream):
        self.closed += 1

    def speak(self, text, voice):
        self.spoken.append((text, voice))
        yield struct.pack("<100f", *([0.1] * 100))


def fixture(tmp_path):
    engine = Engine()
    worker = create_worker(engine, TOKEN)
    directory = tmp_path / "voice"
    directory.mkdir()
    token = directory / "worker-token"
    token.write_text(TOKEN)
    token.chmod(0o600)
    voice = LocalVoice(
        directory, transport=httpx.ASGITransport(app=worker, client=("127.0.0.1", 1234))
    )
    app = create_app(
        tmp_path / "app",
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        local_voice=voice,
    )
    return TestClient(app, headers={"Origin": "http://testserver"}), engine, worker


def capture(key=None, **changes):
    return {
        "captureId": key or str(uuid4()),
        "sequence": 0,
        "pcm": base64.b64encode(struct.pack("<2f", 0.2, -0.3)).decode(),
        **changes,
    }


def test_real_app_auth_origin_and_independent_optional_status(tmp_path):
    client, engine, _worker = fixture(tmp_path)
    with client:
        assert client.get("/api/voice/status").status_code == 401
        login(client)
        assert client.get("/api/voice/status").json()["ready"]
        result = client.post(
            "/api/voice/capture",
            json=capture(),
            headers={"Origin": "https://untrusted.invalid"},
        )
        assert result.status_code == 403
        assert engine.opened == 0
        assert client.post("/api/voice/capture", json=capture()).status_code == 200
        assert engine.opened == 1
        assert client.get("/api/voice/status").headers["cache-control"] == "no-store"


def test_capture_order_owner_duration_finish_and_retirement(tmp_path):
    client, engine, worker = fixture(tmp_path)
    with client:
        login(client)
        body = capture()
        assert client.post("/api/voice/capture", json=body).status_code == 200
        assert client.post("/api/voice/capture", json=body).status_code == 409
        assert client.post("/api/voice/capture", json=capture()).status_code == 409
        assert (
            client.post("/api/voice/speak", json={"text": "Hello"}).status_code == 409
        )
        result = client.post(
            "/api/voice/capture", json={**body, "sequence": 1, "finish": True}
        )
        assert result.status_code == 200
        assert (
            result.json()["finished"]
            and result.json()["finalText"] == "A synthetic transcript"
        )
        assert engine.closed == 1
        assert client.post("/api/voice/capture", json=body).status_code == 409
        other = capture()
        assert client.post("/api/voice/capture", json=other).status_code == 200
        worker.state.worker.capture_state["samples"] = 90 * 16000
        assert (
            client.post("/api/voice/capture", json={**other, "sequence": 1}).status_code
            == 422
        )
        assert engine.closed == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"pcm": "invalid!"},
        {"pcm": base64.b64encode(struct.pack("<f", float("nan"))).decode()},
        {"pcm": base64.b64encode(struct.pack("<f", 2)).decode()},
        {"sampleRate": 48000},
        {"language": "fr"},
        {"url": "https://invalid.example/audio"},
    ],
)
def test_invalid_audio_never_reaches_engine(tmp_path, changes):
    client, engine, _worker = fixture(tmp_path)
    with client:
        login(client)
        assert (
            client.post("/api/voice/capture", json=capture(**changes)).status_code
            == 422
        )
        assert engine.opened == 0 and not engine.samples


def test_output_bounded_stream_and_no_storage(tmp_path):
    client, engine, _worker = fixture(tmp_path)
    with client:
        login(client)
        before = list((tmp_path / "voice").iterdir())
        response = client.post(
            "/api/voice/speak", json={"text": "This is synthetic test speech."}
        )
        assert response.status_code == 200
        frames = [json.loads(line) for line in response.text.splitlines()]
        assert frames[0]["sequence"] == 0 and frames[0]["sampleRate"] == 24000
        assert frames[-1] == {"done": True}
        assert TOKEN not in response.text
        assert list((tmp_path / "voice").iterdir()) == before
        assert (
            client.post("/api/voice/speak", json={"text": "x" * 301}).status_code == 422
        )
        assert (
            client.post(
                "/api/voice/speak",
                json={"text": "test", "voice": "https://invalid.example/clone"},
            ).status_code
            == 422
        )
        assert len(engine.spoken) == 1


def test_worker_rejects_public_origin_and_wrong_token():
    worker = create_worker(Engine(), TOKEN)
    with TestClient(worker, client=("127.0.0.1", 1234)) as client:
        assert client.get("/status").status_code == 401
        assert (
            client.get(
                "/status",
                headers={
                    "authorization": "Bearer " + TOKEN,
                    "origin": "http://testserver",
                },
            ).status_code
            == 401
        )
        assert (
            client.get(
                "/status", headers={"authorization": "Bearer " + TOKEN}
            ).status_code
            == 200
        )
    with TestClient(worker, client=("192.0.2.1", 1234)) as client:
        assert (
            client.get(
                "/status", headers={"authorization": "Bearer " + TOKEN}
            ).status_code
            == 403
        )


def test_cancelled_output_does_not_release_native_model_early():
    class Held(Engine):
        def speak(self, text, voice):
            yield struct.pack("<f", 0.1)
            released.wait(2)
            drained.set()
            yield struct.pack("<f", 0.2)

    released, drained = threading.Event(), threading.Event()

    async def run():
        worker = create_worker(Held(), TOKEN).state.worker
        stream = await worker.prepare_speech(Speak(text="test"))
        assert b'"pcm"' in await anext(stream)
        await stream.aclose()
        assert worker.lock.locked() and not drained.is_set()
        released.set()
        for _ in range(100):
            if not worker.lock.locked():
                break
            await asyncio.sleep(0.01)
        assert drained.is_set() and not worker.lock.locked()

    asyncio.run(run())


def test_main_validation_never_echoes_audio(tmp_path):
    client, engine, _worker = fixture(tmp_path)
    with client:
        login(client)
        result = client.post(
            "/api/voice/capture", json=capture(pcm="private-audio-marker" * 6000)
        )
        assert result.status_code == 422
        assert len(result.content) < 200 and "private-audio-marker" not in result.text
        assert engine.opened == 0


def test_cancel_during_stream_allocation_closes_acquired_stream():
    from leam_api.voice_protocol import Capture

    allocated, released = threading.Event(), threading.Event()

    class Held(Engine):
        def begin(self):
            stream = super().begin()
            allocated.set()
            released.wait(2)
            return stream

    async def run():
        engine = Held()
        worker = create_worker(engine, TOKEN).state.worker
        task = asyncio.create_task(worker.capture("a" * 64, Capture(**capture())))
        assert await asyncio.to_thread(allocated.wait, 1)
        task.cancel()
        released.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert engine.opened == engine.closed == 1
        assert worker.capture_state is None and not worker.lock.locked()

    asyncio.run(run())


def test_output_close_before_first_iteration_drains_producer():
    drained = threading.Event()

    class Many(Engine):
        def speak(self, text, voice):
            for _ in range(12):
                yield struct.pack("<f", 0.1)
            drained.set()

    async def run():
        worker = create_worker(Many(), TOKEN).state.worker
        stream = await worker.prepare_speech(Speak(text="Synthetic test"))
        await stream.aclose()
        for _ in range(100):
            if not worker.lock.locked():
                break
            await asyncio.sleep(0.01)
        assert drained.is_set() and not worker.lock.locked()

    asyncio.run(run())


def test_native_all_task_shutdown_waits_for_thread_without_spinning():
    import subprocess
    import sys

    # A subprocess deadline makes a regression fail instead of hanging pytest.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio, time
from leam_api.voice_worker import native
async def run():
    done = []
    def held():
        time.sleep(.15)
        done.append(True)
    task = asyncio.create_task(native(held))
    await asyncio.sleep(.02)
    for pending in asyncio.all_tasks():
        if pending is not asyncio.current_task():
            pending.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert done == [True]
asyncio.run(run())
""",
        ],
        timeout=5,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_proxy_disconnect_before_iteration_closes_acquired_http_stream(tmp_path):
    from starlette.requests import ClientDisconnect

    closed = []

    class Bytes(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"done":true}\n'

        async def aclose(self):
            closed.append(True)

    async def run():
        directory = tmp_path / "voice"
        directory.mkdir()
        token = directory / "worker-token"
        token.write_text(TOKEN)
        token.chmod(0o600)
        voice = LocalVoice(
            directory,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=Bytes())
            ),
        )
        response = await voice.speech(
            Speak(text="Synthetic test"), "owner", lambda _: True
        )

        async def send(_message):
            raise OSError("Synthetic disconnect before response body")

        async def receive():
            return {"type": "http.disconnect"}

        with pytest.raises(ClientDisconnect):
            await response(
                {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
            )
        assert closed == [True]
        await voice.close()

    asyncio.run(run())


def test_two_authenticated_sessions_cannot_share_or_cancel_capture_uuid(tmp_path):
    client, engine, worker = fixture(tmp_path)
    with client:
        login(client)
        first = client.cookies.get("leam_session")
        body = capture()
        assert client.post("/api/voice/capture", json=body).status_code == 200
        assert (
            client.post(
                "/api/auth/login", json={"password": "long-password-for-tests"}
            ).status_code
            == 200
        )
        second = client.cookies.get("leam_session")
        assert first != second
        assert (
            client.post("/api/voice/capture", json={**body, "sequence": 1}).status_code
            == 409
        )
        assert (
            client.post(
                "/api/voice/capture/cancel", json={"captureId": body["captureId"]}
            ).status_code
            == 200
        )
        assert engine.closed == 0 and worker.state.worker.capture_state is not None
        client.cookies.clear()
        client.cookies.set("leam_session", first)
        assert (
            client.post(
                "/api/voice/capture", json={**body, "sequence": 1, "finish": True}
            ).status_code
            == 200
        )
        assert engine.closed == 1


def test_logout_during_recognition_never_returns_transcript(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    client, engine, _worker = fixture(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = engine.capture

    def held(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    engine.capture = held
    with client, ThreadPoolExecutor(max_workers=1) as pool:
        login(client)
        pending = pool.submit(
            client.post, "/api/voice/capture", json=capture(finish=True)
        )
        assert entered.wait(2)
        assert client.post("/api/auth/logout").status_code == 200
        release.set()
        result = pending.result(timeout=3)
        assert result.status_code == 401 and "synthetic transcript" not in result.text
        assert engine.closed == 1


def test_idle_capture_expires_without_another_request():
    import time

    engine = Engine()
    app = create_worker(engine, TOKEN)
    worker = app.state.worker
    clock = [0]
    worker.clock = lambda: clock[0]
    headers = {"authorization": "Bearer " + TOKEN, "x-leam-owner": "a" * 64}
    with TestClient(app, client=("127.0.0.1", 1234)) as client:
        assert (
            client.post("/capture", headers=headers, json=capture()).status_code == 200
        )
        clock[0] = 121
        deadline = time.monotonic() + 6
        while engine.closed == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert engine.closed == 1 and worker.capture_state is None


def test_worker_startup_limits_native_ort_before_loading_models(tmp_path, monkeypatch):
    import logging
    import os
    import sys

    import uvicorn

    import leam_api.voice_worker as module

    token = tmp_path / "worker-token"
    token.write_text(TOKEN)
    token.chmod(0o600)
    monkeypatch.setenv("MOONSHINE_ORT_SINGLE_THREAD", "0")
    monkeypatch.setattr(sys, "argv", ["worker", "--directory", str(tmp_path)])
    monkeypatch.setattr(logging, "disable", lambda _level: None)
    monkeypatch.setattr(os, "sched_setaffinity", lambda *_args: None)
    monkeypatch.setattr(os, "umask", lambda _mask: 0)
    observed = []

    def load(directory):
        assert directory == tmp_path
        assert os.environ["MOONSHINE_ORT_SINGLE_THREAD"] == "1"
        observed.append("models")
        return Engine()

    def serve(app, **kwargs):
        assert observed == ["models"]
        assert kwargs["host"] == "127.0.0.1"
        observed.append("serve")

    monkeypatch.setattr(module, "SpeechEngine", load)
    monkeypatch.setattr(uvicorn, "run", serve)
    module.main()
    assert observed == ["models", "serve"]
