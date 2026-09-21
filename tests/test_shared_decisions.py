import asyncio
import copy

import pytest
from shared_coding_fixtures import SHARED_THREAD
from test_shared_coding_api import api_owner

COMMAND = "item/commandExecution/requestApproval"
QUESTION = "item/tool/requestUserInput"
FILE = "item/fileChange/requestApproval"


def command(native=41):
    return {
        "id": native,
        "method": COMMAND,
        "params": {
            "threadId": SHARED_THREAD,
            "turnId": "fixture-turn",
            "itemId": "item-a",
            "startedAtMs": 1,
            "command": "printf 'hello'",
            "cwd": "/fixture",
            "reason": "Fixture review",
        },
    }


async def pending(client):
    await client.get(f"/api/codex/threads/{SHARED_THREAD}")
    return (await client.get("/api/codex/requests")).json()["items"]


async def respond(client, card, response):
    return await client.post(
        "/api/codex/shared/requests/" + card["id"],
        json={
            "generation": card["generation"],
            "requestId": "00000000-0000-4000-8000-000000000001",
            "response": response,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [41, "41"])
async def test_decision_exact_native_type_ack_and_two_browser_dedup(
    tmp_path, monkeypatch, native
):
    async with api_owner(tmp_path, monkeypatch, requests=[command(native)]) as (
        client,
        _,
        seen,
        other,
        _,
    ):
        card = (await pending(client))[0]
        assert card["transport"] == "ide-owner" and not card.get("unsupported")
        assert card["params"]["command"] == "printf 'hello'"
        replies = await asyncio.gather(
            *[respond(client, card, {"decision": "accept"}) for _ in range(3)]
        )
        assert all(r.status_code == 200 for r in replies)
        assert all(r.json()["state"] == "submitted" for r in replies)
        calls = [p for p in seen if p["method"].startswith("thread-follower-")]
        assert len(calls) == 1
        assert calls[0]["method"] == "thread-follower-command-approval-decision"
        assert (
            calls[0]["version"] == 1 and calls[0]["targetClientId"] == "fixture-owner"
        )
        assert calls[0]["params"] == {
            "conversationId": SHARED_THREAD,
            "requestId": native,
            "decision": "accept",
        }
        assert type(calls[0]["params"]["requestId"]) is type(native)
        assert not other.calls


@pytest.mark.asyncio
async def test_reorder_stable_but_removal_changed_params_and_reuse_tombstone(
    tmp_path, monkeypatch
):
    async with api_owner(tmp_path, monkeypatch, requests=[command(1), command(2)]) as (
        client,
        service,
        seen,
        _,
        _,
    ):
        cards = await pending(client)
        service.snapshot.state["requests"].reverse()
        assert [c["id"] for c in await pending(client)] == [
            cards[1]["id"],
            cards[0]["id"],
        ]
        service.snapshot.state["requests"] = [command(2)]
        await pending(client)
        assert (
            await respond(client, cards[0], {"decision": "accept"})
        ).status_code == 409
        service.snapshot.state["requests"] = [command(1), command(2)]
        new = await pending(client)
        assert new[0]["id"] != cards[0]["id"]
        service.snapshot.state["requests"][0]["params"]["command"] = "rm fixture"
        assert (
            await respond(client, new[0], {"decision": "accept"})
        ).status_code == 409
        assert not [p for p in seen if p["method"].startswith("thread-follower-")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad", ["secret", "oversized", "stdin", "network", "grant", "duplicate", "options"]
)
async def test_incomplete_or_broad_review_is_unsupported(tmp_path, monkeypatch, bad):
    packet = command()
    if bad == "secret":
        packet["method"] = QUESTION
        packet["params"]["questions"] = [
            {"id": "q", "header": "Password", "question": "Password?", "isSecret": True}
        ]
    if bad == "oversized":
        packet["params"]["command"] = "x" * 16385
    if bad == "stdin":
        packet["params"]["kind"] = "writeStdin"
    if bad == "network":
        packet["params"]["networkApprovalContext"] = {
            "host": "example.com",
            "protocol": "https",
        }
    if bad == "grant":
        packet["method"] = FILE
        packet["params"]["grantRoot"] = "/private"
    if bad == "options":
        packet["params"]["availableDecisions"] = ["acceptForSession"]
    packets = [packet, packet] if bad == "duplicate" else [packet]
    async with api_owner(tmp_path, monkeypatch, requests=packets) as (
        client,
        _,
        seen,
        _,
        _,
    ):
        cards = await pending(client)
        assert all(c.get("unsupported") for c in cards)
        assert all(set(c["params"]) == {"threadId"} for c in cards)
        assert not [p for p in seen if p["method"].startswith("thread-follower-")]


@pytest.mark.asyncio
async def test_question_requires_exact_ids_and_options_preserves_wire_body(
    tmp_path, monkeypatch
):
    packet = command("question-native")
    packet["method"] = QUESTION
    packet["params"] = {
        "threadId": SHARED_THREAD,
        "turnId": "fixture-turn",
        "itemId": "input-a",
        "isBlocking": True,
        "questions": [
            {
                "id": "choice",
                "header": "Choice",
                "question": "Which option?",
                "options": [
                    {"label": "A", "description": "First choice"},
                    {"label": "B", "description": "Second choice"},
                ],
            },
            {
                "id": "note",
                "header": "Note",
                "question": "Any note?",
                "isOther": True,
                "options": None,
            },
        ],
    }
    async with api_owner(tmp_path, monkeypatch, requests=[packet]) as (
        client,
        _,
        seen,
        _,
        _,
    ):
        card = (await pending(client))[0]
        assert (
            card["params"]["questions"][0]["options"][0]["description"]
            == "First choice"
        )
        for response in [
            {"answers": {}},
            {"answers": {"choice": {"answers": ["C"]}, "note": {"answers": ["text"]}}},
        ]:
            assert (await respond(client, card, response)).status_code == 422
        body = {
            "answers": {
                "choice": {"answers": ["B"]},
                "note": {"answers": [" exact answer "]},
            }
        }
        reply = await respond(client, card, body)
        assert reply.status_code == 200 and reply.json()["state"] == "submitted"
        call = [p for p in seen if p["method"].startswith("thread-follower-")][0]
        assert call["method"] == "thread-follower-submit-user-input"
        assert call["params"] == {
            "conversationId": SHARED_THREAD,
            "requestId": "question-native",
            "response": body,
        }


@pytest.mark.asyncio
async def test_file_review_complete_diff_and_change_invalidates_capability(
    tmp_path, monkeypatch
):
    packet = command()
    packet["method"] = FILE
    packet["params"].pop("command")
    packet["params"].pop("cwd")
    async with api_owner(tmp_path, monkeypatch, requests=[packet]) as (
        client,
        service,
        seen,
        _,
        _,
    ):
        assert (await pending(client))[0]["unsupported"]
        changes = [
            {
                "path": "/fixture/file.txt",
                "kind": {"type": "update", "move_path": None},
                "diff": "@@ -1 +1 @@\n-old\n+new",
            }
        ]
        service.snapshot.state["turns"][0]["items"].append(
            {
                "id": "item-a",
                "type": "fileChange",
                "changes": changes,
                "status": "inProgress",
            }
        )
        card = (await pending(client))[0]
        assert card["changes"] == changes and not card.get("unsupported")
        changes[0]["diff"] += "\n+changed"
        assert (await respond(client, card, {"decision": "accept"})).status_code == 409
        fresh = (await pending(client))[0]
        response = await respond(client, fresh, {"decision": "decline"})
        assert response.status_code == 200
        call = [p for p in seen if p["method"].startswith("thread-follower-")][0]
        assert (
            call["method"] == "thread-follower-file-approval-decision"
            and call["params"]["decision"] == "decline"
        )


@pytest.mark.asyncio
async def test_lost_ack_no_replay_changed_decision_rejected_and_resolution_honest(
    tmp_path, monkeypatch
):
    async with api_owner(tmp_path, monkeypatch, requests=[command()], drop=True) as (
        client,
        service,
        seen,
        _,
        _,
    ):
        card = (await pending(client))[0]
        receipt = (await respond(client, card, {"decision": "accept"})).json()
        assert receipt["state"] == "uncertain"
        assert (await respond(client, card, {"decision": "accept"})).json() == receipt
        assert (await respond(client, card, {"decision": "decline"})).status_code == 409
        assert (await pending(client))[0]["submitted"]
        service.snapshot.state["requests"] = []
        resolved = (await pending(client))[0]
        assert resolved["receipt"]["state"] == "resolved"
        assert "does not confirm tool execution" in resolved["receipt"]["detail"]
        assert len([p for p in seen if p["method"].startswith("thread-follower-")]) == 1


@pytest.mark.asyncio
async def test_recheck_after_pin_and_owner_await_before_write(tmp_path, monkeypatch):
    async with api_owner(tmp_path, monkeypatch, requests=[command()]) as (
        client,
        service,
        seen,
        _,
        _,
    ):
        card = (await pending(client))[0]
        original = service.commands.respond_request

        async def changed(owner, packet, response, revalidate):
            service.snapshot.state["requests"][0]["params"][
                "command"
            ] = "changed after review"
            return await original(owner, packet, response, revalidate)

        monkeypatch.setattr(service.commands, "respond_request", changed)
        assert (await respond(client, card, {"decision": "accept"})).status_code == 409
        assert not [p for p in seen if p["method"].startswith("thread-follower-")]


@pytest.mark.asyncio
async def test_generation_owner_and_origin_refused(tmp_path, monkeypatch):
    async with api_owner(tmp_path, monkeypatch, requests=[command()]) as (
        client,
        service,
        seen,
        _,
        owner,
    ):
        card = (await pending(client))[0]
        body = {
            "generation": card["generation"],
            "requestId": "00000000-0000-4000-8000-000000000001",
            "response": {"decision": "accept"},
        }
        assert (
            await client.post(
                "/api/codex/shared/requests/" + card["id"],
                json=body,
                headers={"Origin": "https://untrusted.example"},
            )
        ).status_code == 403
        stale = copy.deepcopy(card)
        stale["generation"] = "stale"
        assert (await respond(client, stale, {"decision": "accept"})).status_code == 409
        owner[0] = "changed-owner"
        assert (await respond(client, card, {"decision": "accept"})).status_code == 409
        assert not [p for p in seen if p["method"].startswith("thread-follower-")]


@pytest.mark.asyncio
async def test_canonical_owner_history_uses_complete_raw_file_diff(
    tmp_path, monkeypatch
):
    packet = command()
    packet["method"] = FILE
    packet["params"].pop("command")
    packet["params"].pop("cwd")
    async with api_owner(tmp_path, monkeypatch, requests=[packet]) as (
        client,
        service,
        seen,
        _,
        _,
    ):
        await pending(client)
        changes = [
            {
                "path": "/fixture/canonical.txt",
                "kind": {"type": "update", "move_path": None},
                "diff": "@@ -1 +1 @@\n-old\n+canonical",
            }
        ]
        canonical = copy.deepcopy(service.snapshot.state["turns"][0])
        canonical["items"] = [
            {
                "id": "item-a",
                "type": "fileChange",
                "changes": changes,
                "status": "inProgress",
            }
        ]
        service.snapshot.state["turns"] = []
        service.snapshot.state["turnHistory"] = {
            "kind": "canonical",
            "history": {
                "islands": [{"entries": [{"value": "turn-key"}]}],
                "entitiesByKey": {"turn-key": canonical},
            },
        }
        card = (await pending(client))[0]
        assert not card.get("unsupported") and card["changes"] == changes
        assert (await respond(client, card, {"decision": "accept"})).status_code == 200
        call = [p for p in seen if p["method"].startswith("thread-follower-")][0]
        assert call["method"] == "thread-follower-file-approval-decision"
        assert call["params"] == {
            "conversationId": SHARED_THREAD,
            "requestId": 41,
            "decision": "accept",
        }
