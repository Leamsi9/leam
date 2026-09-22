import json

from fastapi.testclient import TestClient
from test_api import login
from test_coding_handoff import H, propose, review, setup
from test_ticket_chat import TicketCodex

from leam_api.codex import CodexError


class DelayedIndex(TicketCodex):
    missing = False
    unavailable = False

    async def request(self, method, params, *, expected_generation=None):
        if method == "thread/delete":
            self.calls.append((method, params))
            self.missing = True
            return {}
        if method == "thread/list":
            self.calls.append((method, params))
            return {"data": [], "nextCursor": "native-next"}
        if method == "thread/read":
            self.calls.append((method, params))
            if self.missing:
                raise CodexError("thread not found")
            if self.unavailable:
                raise CodexError("unavailable")
            return {
                "thread": {
                    "id": params["threadId"],
                    "name": None,
                    "cwd": "/fixture",
                    "createdAt": 42,
                }
            }
        return await super().request(
            method, params, expected_generation=expected_generation
        )


def test_accepted_handoff_is_listed_even_before_native_index_and_never_resumed(
    tmp_path, monkeypatch
):
    bridge = DelayedIndex()
    app, _ = setup(tmp_path, monkeypatch, bridge)
    with TestClient(app) as client:
        login(client)
        item = propose(client)
        ready = review(client, item["id"], "Inspect exactly")
        client.post(
            f"/api/coding/handoffs/{item['id']}/start",
            headers=H,
            json={"previewToken": ready["previewToken"], "confirmed": True},
        ).raise_for_status()
        bridge.calls.clear()
        response = client.get("/api/codex/threads")
        assert response.status_code == 200, response.text
        listing = response.json()
        found = [row for row in listing["data"] if row["id"] == "ticket-thread"]
        assert len(found) == 1
        assert found[0]["name"] == "Inspect product"
        assert found[0]["leamHandoffId"] == item["id"]
        assert found[0]["leamHandoffTitle"] == "Inspect product"
        assert listing["nextCursor"] == "native-next"
        assert (
            "thread/read",
            {"threadId": "ticket-thread", "includeTurns": False},
        ) in bridge.calls
        assert all(
            method in {"thread/list", "thread/read"} for method, _ in bridge.calls
        )

        deleted = client.request(
            "DELETE",
            "/api/codex/threads/ticket-thread",
            headers=H,
            json={"confirmed": True, "deleteChildren": True, "stopRunning": True},
        )
        assert deleted.status_code == 200, deleted.text
        assert ("thread/delete", {"threadId": "ticket-thread"}) in bridge.calls

        # A native missing/deleted session is never resurrected from the receipt.
        missing = client.get("/api/codex/threads").json()
        assert all(row["id"] != "ticket-thread" for row in missing["data"])
        bridge.missing = False
        bridge.unavailable = True
        partial = client.get("/api/codex/threads").json()
        assert partial["leamHandoffsUnavailable"] is True
        assert all(row["id"] != "ticket-thread" for row in partial["data"])


def test_handoff_overlay_deduplicates_native_rows_and_preserves_pagination(
    tmp_path, monkeypatch
):
    bridge = DelayedIndex()
    app, _ = setup(tmp_path, monkeypatch, bridge)
    with app.state.store.connect() as db:
        db.execute(
            "INSERT INTO settings VALUES (?,?)",
            (
                "coding-handoff:fixture",
                json.dumps(
                    {
                        "state": "accepted",
                        "threadId": "ticket-thread",
                        "title": "Fallback",
                        "sourceThreadId": "source-conversation",
                        "updated": 42,
                        "id": "fixture",
                    }
                ),
            ),
        )
    original = bridge.request

    async def request(method, params, *, expected_generation=None):
        if method == "thread/list":
            bridge.calls.append((method, params))
            return {
                "data": [{"id": "ticket-thread", "name": "User renamed"}],
                "nextCursor": "next",
            }
        return await original(method, params, expected_generation=expected_generation)

    bridge.request = request
    with TestClient(app) as client:
        login(client)
        for query in ["", "?cursor=next"]:
            response = client.get("/api/codex/threads" + query).json()
            assert len([r for r in response["data"] if r["id"] == "ticket-thread"]) == 1
            assert (
                next(r for r in response["data"] if r["id"] == "ticket-thread")["name"]
                == "User renamed"
            )
            row = next(r for r in response["data"] if r["id"] == "ticket-thread")
            assert row["leamOrigin"] == "reviewed-companion-handoff"
            assert row["leamHandoffTitle"] == "Fallback"
            assert row["leamSourceThreadId"] == "source-conversation"
            assert response["nextCursor"] == "next"
        assert not any(method == "thread/read" for method, _ in bridge.calls)


def test_later_native_page_decorates_handoffs_outside_discovery_window_without_reads(
    tmp_path, monkeypatch
):
    bridge = DelayedIndex()
    app, _ = setup(tmp_path, monkeypatch, bridge)
    for index in range(12):
        app.state.store.set(
            f"coding-handoff:older-{index}",
            {
                "id": f"older-{index}",
                "state": "accepted",
                "threadId": f"thread-{index}",
                "title": f"Reviewed task {index}",
                "sourceThreadId": "companion-source",
                "updated": index,
            },
        )

    async def request(method, params, *, expected_generation=None):
        bridge.calls.append((method, params))
        assert method == "thread/list", "Page decoration must not fetch more metadata"
        assert params == {
            "limit": 50,
            "sourceKinds": ["cli", "vscode", "appServer"],
            "sortKey": "recency_at",
            "sortDirection": "desc",
            "archived": False,
            "cursor": "older",
        }
        return {"data": [{"id": "thread-0", "name": None}], "nextCursor": None}

    bridge.request = request
    with TestClient(app) as client:
        login(client)
        result = client.get("/api/codex/threads?cursor=older")
        assert result.status_code == 200, result.text
        row = result.json()["data"][0]
        assert row["name"] == "Reviewed task 0"
        assert row["leamHandoffTitle"] == "Reviewed task 0"
        assert row["leamSourceThreadId"] == "companion-source"
        assert len(bridge.calls) == 1
