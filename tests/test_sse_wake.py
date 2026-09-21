import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.responses import StreamingResponse
from test_api import login, make

from leam_api.store import Store


def one_packet(monkeypatch):
    # Close the actual HTTP stream after its first data packet, like a browser
    # disconnect. The product's route, wait loop, authentication and cursor run.
    def response(body, **kwargs):
        async def finite():
            try:
                async for chunk in body:
                    yield chunk
                    if "\ndata: " in chunk:
                        break
            finally:
                await body.aclose()

        return StreamingResponse(finite(), **kwargs)

    monkeypatch.setattr("leam_api.app.StreamingResponse", response)


def test_authenticated_stream_wakes_after_local_commit_without_two_second_poll(
    tmp_path, monkeypatch
):
    client, _ = make(tmp_path)
    ready = threading.Event()
    with client:
        login(client)
        store = client.app.state.store
        original = store.events

        def observed(cursor):
            rows = original(cursor)
            if not rows:
                ready.set()
            return rows

        monkeypatch.setattr(store, "events", observed)
        one_packet(monkeypatch)
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(client.get, "/api/events")
            assert ready.wait(2)
            # Let the server enter its wait; publication comes from this other
            # thread and must schedule a safe event-loop wake.
            time.sleep(0.03)
            started = time.monotonic()
            sequence = store.event("fixture", {"text": "Synthetic local event"})
            response = pending.result(timeout=4)
            elapsed = time.monotonic() - started
        assert response.status_code == 200
        packet = next(
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        )
        assert packet == {
            "id": sequence,
            "topic": "fixture",
            "payload": {"text": "Synthetic local event"},
        }
        assert elapsed < 0.8, elapsed
        assert not store._event_waiters


def test_event_committed_between_empty_read_and_wait_is_not_lost(tmp_path, monkeypatch):
    client, _ = make(tmp_path)
    with client:
        login(client)
        store = client.app.state.store
        original = store.events
        injected = False

        def raced(cursor):
            nonlocal injected
            rows = original(cursor)
            if not rows and not injected:
                injected = True
                store.event("fixture", {"race": "after-empty-read"})
            return rows

        monkeypatch.setattr(store, "events", raced)
        one_packet(monkeypatch)
        started = time.monotonic()
        response = client.get("/api/events")
        assert response.status_code == 200
        assert "after-empty-read" in response.text
        assert time.monotonic() - started < 0.8
        assert not store._event_waiters


def test_separate_store_writer_is_discovered_by_bounded_durable_fallback(
    tmp_path, monkeypatch
):
    client, _ = make(tmp_path)
    ready = threading.Event()
    with client:
        login(client)
        store = client.app.state.store
        external = Store(tmp_path)
        original = store.events

        def observed(cursor):
            rows = original(cursor)
            if not rows:
                ready.set()
            return rows

        monkeypatch.setattr(store, "events", observed)
        one_packet(monkeypatch)
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(client.get, "/api/events")
            assert ready.wait(2)
            started = time.monotonic()
            external.event("fixture", {"writer": "separate-instance"})
            response = pending.result(timeout=4)
        assert response.status_code == 200
        assert "separate-instance" in response.text
        assert time.monotonic() - started < 3
        assert not store._event_waiters


def test_session_revoked_while_waiting_cannot_receive_new_event(tmp_path, monkeypatch):
    client, _ = make(tmp_path)
    ready = threading.Event()
    with client:
        login(client)
        store = client.app.state.store
        original = store.events

        def observed(cursor):
            rows = original(cursor)
            if not rows:
                ready.set()
            return rows

        monkeypatch.setattr(store, "events", observed)
        one_packet(monkeypatch)
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(client.get, "/api/events")
            assert ready.wait(2)
            assert (
                client.post(
                    "/api/auth/logout", headers={"origin": "http://testserver"}
                ).status_code
                == 200
            )
            store.event("fixture", {"private": "must-not-be-delivered"})
            response = pending.result(timeout=4)
        assert "must-not-be-delivered" not in response.text
        assert not store._event_waiters


def test_one_commit_wakes_both_authenticated_browser_streams(tmp_path, monkeypatch):
    client, _ = make(tmp_path)
    ready = threading.Event()
    with client:
        login(client)
        store = client.app.state.store
        original = store.events

        def observed(cursor):
            rows = original(cursor)
            if len(store._event_waiters) == 2:
                ready.set()
            return rows

        monkeypatch.setattr(store, "events", observed)
        one_packet(monkeypatch)
        with ThreadPoolExecutor() as pool:
            pending = [pool.submit(client.get, "/api/events") for _ in range(2)]
            assert ready.wait(2)
            started = time.monotonic()
            sequence = store.event("fixture", {"broadcast": True})
            responses = [future.result(timeout=4) for future in pending]
        assert all(f"id: {sequence}\n" in response.text for response in responses)
        assert time.monotonic() - started < 0.8
        assert not store._event_waiters


def test_disconnected_http_client_unregisters_pending_waiter(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        store = client.app.state.store

        async def disconnect():
            incoming = asyncio.Queue()
            await incoming.put(
                {"type": "http.request", "body": b"", "more_body": False}
            )

            async def send(_message):
                pass

            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/api/events",
                "raw_path": b"/api/events",
                "query_string": b"",
                "root_path": "",
                "server": ("testserver", 80),
                "client": ("testclient", 123),
                "headers": [
                    (b"host", b"testserver"),
                    (
                        b"cookie",
                        ("leam_session=" + client.cookies.get("leam_session")).encode(),
                    ),
                ],
            }
            task = asyncio.create_task(client.app(scope, incoming.get, send))
            try:
                async with asyncio.timeout(2):
                    while not store._event_waiters:
                        await asyncio.sleep(0.001)
                await incoming.put({"type": "http.disconnect"})
                await asyncio.wait_for(task, 2)
                assert not store._event_waiters
                store.event("fixture", {"after_disconnect": True})
                await asyncio.sleep(0)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(disconnect())
