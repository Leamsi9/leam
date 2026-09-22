"""Exact native identities and explicit protection through authenticated callers."""

import pytest
from test_api import login
from test_conversation_management import make

from leam_api.codex import CodexError


def test_catalog_filters_only_explicit_internal_metadata_and_keeps_identity(tmp_path):
    client, _, _, codex = make(tmp_path)
    original = codex.request

    async def request(method, params, *, expected_generation=None):
        if method == "thread/list":
            codex.calls.append((method, params))
            return {
                "data": [
                    {"id": "first", "name": "Same title", "source": "vscode"},
                    {"id": "first", "name": "Same title", "source": "vscode"},
                    {"id": "second", "name": "Same title", "source": "cli"},
                    {"id": "ticket", "originator": "leam"},
                    {
                        "id": "qa-sounding",
                        "name": "A synthetic test",
                        "originator": "leam",
                    },
                    {"id": "child", "source": {"subAgent": "review"}},
                    {"id": "parented", "parentThreadId": "first"},
                    {"id": "ambient", "threadSource": "ambient_suggestions"},
                    {"id": "temporary", "ephemeral": True},
                ],
                "nextCursor": "native cursor",
            }
        return await original(method, params, expected_generation=expected_generation)

    codex.request = request
    with client:
        login(client)
        client.app.state.store.set(
            "ticket-chat:ticket", {"state": "ready", "threadId": "ticket"}
        )
        result = client.get("/api/codex/threads?cursor=previous").json()
        assert [x["id"] for x in result["data"]] == [
            "first",
            "second",
            "ticket",
            "qa-sounding",
        ]
        assert result["nextCursor"] == "native cursor"
        assert result["leamFilteredCount"] == 4
        assert result["data"][2]["leamPurpose"] == "update"
        assert result["data"][2]["leamDeleteProtected"] is True
        assert result["data"][3]["leamPurpose"] == "leam-origin"
        assert codex.calls == [
            (
                "thread/list",
                {
                    "limit": 50,
                    "sourceKinds": ["cli", "vscode", "appServer"],
                    "sortKey": "recency_at",
                    "sortDirection": "desc",
                    "archived": False,
                    "cursor": "previous",
                },
            )
        ]


@pytest.mark.parametrize(
    "phase,code,status,notice",
    [
        ("preflight", -32000, 409, "No deletion was requested"),
        ("delete", -32601, 501, "does not support permanent"),
        ("delete", -32000, 502, "outcome may be uncertain"),
    ],
)
def test_removal_errors_keep_preflight_and_dispatch_outcomes_distinct(
    tmp_path, phase, code, status, notice
):
    client, _, _, codex = make(tmp_path)

    async def request(method, params, *, expected_generation=None):
        codex.calls.append((method, params))
        if (
            phase == "preflight" and method == "thread/read"
        ) or method == "thread/delete":
            raise CodexError("private-native-detail", rpc_code=code)
        return {"thread": {"id": params["threadId"], "parentThreadId": None}}

    codex.request = request
    with client:
        login(client)
        response = client.request(
            "DELETE",
            "/api/codex/threads/ordinary",
            headers={"origin": "http://testserver"},
            json={"confirmed": True, "deleteChildren": True, "stopRunning": True},
        )
        assert response.status_code == status
        assert notice in response.text and "private-native-detail" not in response.text
        assert sum(method == "thread/delete" for method, _ in codex.calls) == (
            0 if phase == "preflight" else 1
        )


def test_handoff_recovery_cannot_reintroduce_explicit_native_internal_thread(tmp_path):
    client, _, _, codex = make(tmp_path)

    async def request(method, params, *, expected_generation=None):
        codex.calls.append((method, params))
        if method == "thread/list":
            return {"data": [], "nextCursor": "next-native"}
        assert method == "thread/read"
        return {
            "thread": {
                "id": params["threadId"],
                "source": {"subAgent": "review"},
                "parentThreadId": "parent",
            }
        }

    codex.request = request
    with client:
        login(client)
        client.app.state.store.set(
            "coding-handoff:fixture",
            {
                "state": "accepted",
                "threadId": "child",
                "id": "fixture",
                "title": "Synthetic handoff",
                "updated": 1,
            },
        )
        result = client.get("/api/codex/threads").json()
        assert all(row["id"] != "child" for row in result["data"])
        assert result["leamFilteredCount"] == 1
        assert result["nextCursor"] == "next-native"
