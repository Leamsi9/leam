import json

from starlette.requests import Request
from test_api import login, make


def finite_connection(monkeypatch):
    checks = 0

    async def disconnected(_request):
        nonlocal checks
        checks += 1
        return checks > 1

    monkeypatch.setattr(Request, "is_disconnected", disconnected)


def packets(response):
    assert response.status_code == 200, response.text
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_selected_thread_replays_canonical_start_through_authenticated_event_route(
    tmp_path, monkeypatch
):
    client, codex = make(tmp_path)
    with client:
        assert client.get("/api/events?thread_id=a").status_code == 401
        login(client)
        store = client.app.state.store
        store.event(
            "codex", {"method": "turn/started", "params": {"threadId": "other"}}
        )
        start = store.event(
            "codex",
            {
                "method": "turn/started",
                "params": {"threadId": "a", "turn": {"id": "turn-a"}},
            },
        )
        delta = store.event(
            "codex",
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "a",
                    "turnId": "turn-a",
                    "itemId": "answer",
                    "delta": "Hello",
                },
            },
        )
        finite_connection(monkeypatch)
        result = packets(client.get("/api/events?thread_id=a"))
        assert [event["id"] for event in result] == [start, delta]
        assert codex.calls == []


def test_reconnect_sequence_and_explicit_after_override_selected_thread_replay(
    tmp_path, monkeypatch
):
    client, _ = make(tmp_path)
    with client:
        login(client)
        store = client.app.state.store
        start = store.event(
            "codex", {"method": "turn/started", "params": {"threadId": "a"}}
        )
        delta = store.event(
            "codex",
            {
                "method": "item/agentMessage/delta",
                "params": {"threadId": "a", "delta": "One"},
            },
        )
        end = store.event(
            "codex", {"method": "turn/completed", "params": {"threadId": "a"}}
        )
        finite_connection(monkeypatch)
        result = packets(
            client.get(
                f"/api/events?thread_id=a&after={start}",
                headers={"last-event-id": str(delta)},
            )
        )
        assert [event["id"] for event in result] == [end]
        finite_connection(monkeypatch)
        result = packets(client.get(f"/api/events?thread_id=a&after={start}"))
        assert [event["id"] for event in result] == [delta, end]
        assert (
            client.get(
                "/api/events?thread_id=a", headers={"last-event-id": "invalid"}
            ).status_code
            == 400
        )


def test_old_or_absent_start_uses_live_cursor_without_claiming_history_watermark(
    tmp_path, monkeypatch
):
    client, _ = make(tmp_path)
    with client:
        login(client)
        store = client.app.state.store
        store.event("codex", {"method": "turn/started", "params": {"threadId": "a"}})
        with store.connect() as db:
            db.execute(
                "INSERT INTO events(id,topic,payload,created) VALUES (5000,'fixture','{}',0)"
            )
        finite_connection(monkeypatch)
        assert packets(client.get("/api/events?thread_id=a")) == []
        finite_connection(monkeypatch)
        assert packets(client.get("/api/events?thread_id=missing")) == []
        assert client.get("/api/events?thread_id=" + "a" * 257).status_code == 422
