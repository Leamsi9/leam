"""API/proposal/reconciliation callers; isolated SQLite and controlled provider."""

import json
from uuid import uuid4

import pytest
from test_api import login, make
from test_obligation_reconciliation import candidate, exchange, setup

from leam_api import approval_policy
from leam_api.commitments import Commitment, Commitments
from leam_api.proposals import Propose

H = {"origin": "http://testserver"}


def child(title="First step", **fields):
    return {"id": str(uuid4()), "title": title, **fields}


def proposal(client, card, **change):
    response = client.post(
        "/api/proposals",
        headers=H,
        json={
            "requestId": str(uuid4()),
            "threadId": "board-chat",
            "operation": "commitment.subtask",
            "input": {"id": card["id"], "revision": card["revision"], **change},
            "reason": "Explicit reviewed change to one card subtask",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_card_defaults_metadata_and_subtasks_survive_legacy_client_and_progress(
    tmp_path,
):
    client, _ = make(tmp_path)
    with client:
        login(client)
        board = client.post(
            "/api/capacities", headers=H, json={"name": "Health"}
        ).json()
        a, b = child(owner="leam", children=[child("Nested")]), child("Other step")
        response = client.post(
            "/api/commitments",
            headers=H,
            json={
                "title": "Walk",
                "capacityId": board["id"],
                "owner": "leam",
                "stage": "blocked",
                "priority": "high",
                "dueDate": "2026-10-01",
                "subtasks": [a, b],
            },
        )
        assert response.status_code == 200, response.text
        card = response.json()
        edited = client.patch(
            "/api/commitments/" + card["id"],
            headers=H,
            json={"revision": 1, "notes": "An old client edits only its known fields"},
        ).json()
        assert edited["owner"] == "leam" and edited["stage"] == "blocked"
        assert (
            edited["subtasks"] == card["subtasks"]
            and edited["capacityId"] == board["id"]
        )
        progress = client.put(
            f"/api/commitments/{card['id']}/progress/2026-09-21",
            headers=H,
            json={"revision": 0, "commitmentRevision": 2, "operation": "complete"},
        ).json()
        assert progress["commitment"]["status"] == "completed"
        assert (
            progress["commitment"]["stage"] == "blocked"
        )  # lifecycle overrides the display stage
        assert progress["commitment"]["subtasks"] == card["subtasks"]
        assert progress["log"]["done"] and progress["log"]["revision"] == 1
        created = client.post(
            "/api/commitments", headers=H, json={"title": "Legacy shape"}
        ).json()
        assert (
            created["owner"],
            created["stage"],
            created["priority"],
            created["subtasks"],
        ) == ("user", "todo", "normal", [])


def test_exact_subtask_revision_preserves_siblings_and_parent_completion(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        a, b = child(children=[child("Leaf")]), child("Sibling", owner="leam")
        card = client.post(
            "/api/commitments", headers=H, json={"title": "Card", "subtasks": [a, b]}
        ).json()
        url = f"/api/commitments/{card['id']}/subtasks"
        change = {
            "revision": 1,
            "action": "edit",
            "subtaskId": a["children"][0]["id"],
            "status": "completed",
            "owner": "leam",
        }
        response = client.post(url, headers=H, json=change)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["subtasks"][1] == card["subtasks"][1]
        assert saved["subtasks"][0]["children"][0]["status"] == "completed"
        assert saved["status"] == "active" and saved["revision"] == 2
        assert (
            client.get(f"/api/commitments/{card['id']}/history").json()["items"] == []
        )
        assert client.post(url, headers=H, json=change).status_code == 409
        assert client.post(
            url,
            headers=H,
            json={"revision": 2, "action": "remove", "subtaskId": a["id"]},
        ).json()["subtasks"] == [card["subtasks"][1]]


@pytest.mark.parametrize(
    "subtasks",
    [
        [child(children=[child(children=[child(children=[child()])])])],
        [child(str(i)) for i in range(33)],
        [
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "title": "Root",
                "children": [
                    {"id": "11111111-1111-4111-8111-111111111111", "title": "Duplicate"}
                ],
            }
        ],
        [child(startDate="2026-10-02", endDate="2026-10-01")],
        [child(notes="x" * 501)],
    ],
)
def test_bounded_subtask_tree_rejected_through_api(tmp_path, subtasks):
    client, _ = make(tmp_path)
    with client:
        login(client)
        response = client.post(
            "/api/commitments",
            headers=H,
            json={"title": "Invalid tree", "subtasks": subtasks},
        )
        assert response.status_code == 422, response.text
        assert client.get("/api/commitments").json()["items"] == []


def test_subtask_proposal_is_reviewed_once_and_stale_change_cannot_overwrite(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        card = client.post("/api/commitments", headers=H, json={"title": "Card"}).json()
        subtask_id = str(uuid4())
        saved = proposal(
            client,
            card,
            action="add",
            subtaskId=subtask_id,
            title="Leam step",
            owner="leam",
        )
        assert (
            saved["state"] == "pending"
            and saved["review"]["approval"]["mode"] == "manual"
        )
        assert "does not start execution" in saved["review"]["assignment"]
        assert client.get("/api/commitments").json()["items"][0]["subtasks"] == []
        url = f"/api/proposals/{saved['id']}/approve"
        approved = client.post(
            url, headers=H, json={"fingerprint": saved["fingerprint"]}
        )
        assert approved.status_code == 200, approved.text
        assert client.post(url, headers=H, json={}).json() == approved.json()
        current = client.get("/api/commitments").json()["items"][0]
        assert (
            len(current["subtasks"]) == 1 and current["subtasks"][0]["id"] == subtask_id
        )
        stale = proposal(
            client, current, action="edit", subtaskId=subtask_id, notes="Planned note"
        )
        client.patch(
            f"/api/commitments/{card['id']}",
            headers=H,
            json={"revision": current["revision"], "notes": "Concurrent edit"},
        )
        result = client.post(
            f"/api/proposals/{stale['id']}/approve", headers=H, json={}
        )
        assert result.status_code == 409
        assert (
            client.get("/api/commitments").json()["items"][0]["subtasks"][0]["notes"]
            == ""
        )


@pytest.mark.asyncio
async def test_subtask_trusted_policy_auto_receipt_and_generic_manual(tmp_path):
    store, proposals, runtime, _ = setup(tmp_path, [])
    approval_policy.update(
        store, approval_policy.ApprovalPolicy(revision=1, todayRequiresApproval=False)
    )
    card = Commitments(store).create(Commitment(title="Card"))
    request = Propose(
        requestId=uuid4(),
        threadId="today",
        operation="commitment.subtask",
        input={
            "id": card["id"],
            "revision": 1,
            "action": "add",
            "subtaskId": str(uuid4()),
            "title": "Draft outline",
            "owner": "leam",
        },
        reason="User explicitly requested this subtask",
    )
    saved = await proposals.propose(request, origin="today")
    assert (
        saved["state"] == "complete"
        and saved["review"]["approval"]["mode"] == "automatic"
    )
    assert (
        saved["result"]["owner"] == "user"
        and saved["result"]["subtasks"][0]["owner"] == "leam"
    )
    manual = await proposals.propose(
        request.model_copy(
            update={
                "requestId": uuid4(),
                "input": {**request.input, "revision": 2, "subtaskId": str(uuid4())},
            }
        )
    )
    assert (
        manual["state"] == "pending"
        and manual["review"]["approval"]["mode"] == "manual"
    )
    assert runtime.calls == []
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM daily_logs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM reminder_jobs").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_today_uses_board_labels_and_small_child_operation_without_execution(
    tmp_path,
):
    text = "Put my launch card in Work and assign it to Leam"
    store, proposals, runtime, domain = setup(tmp_path, [])
    approval_policy.update(
        store, approval_policy.ApprovalPolicy(revision=1, todayRequiresApproval=False)
    )
    board = store.create("capacity", {"name": "Work", "note": "", "record": ""})
    runtime.rows = [
        candidate(
            text,
            title="Launch",
            changes={"capacityId": board["id"], "owner": "leam", "stage": "todo"},
        )
    ]
    result = await domain.reconcile(exchange(text))
    assert result["outcome"] == "changes_confirmed_complete"
    card = store.entities("commitment")[0]
    context = json.loads(runtime.calls[0][0]["messages"][-1]["content"])
    assert context["capacities"] == [{"id": board["id"], "revision": 1, "name": "Work"}]
    assert card["capacityId"] == board["id"] and card["owner"] == "leam"
    text = "Add a Research options subtask to Launch assigned to Leam"
    runtime.rows = [
        {
            **candidate(
                text, title="Launch", kind="update", target="commitment:" + card["id"]
            ),
            "subtask": {"action": "add", "title": "Research options", "owner": "leam"},
        }
    ]
    first = await domain.reconcile(exchange(text, "child-add"))
    assert first["outcome"] == "changes_confirmed_complete"
    saved = store.entities("commitment")[0]
    assert saved["subtasks"][0]["owner"] == "leam"
    assert proposals.get(first["proposalIds"][0])["operation"] == "commitment.subtask"
    again = await domain.reconcile(exchange(text, "child-repeat"))
    assert (
        again["outcome"] == "no_action"
        and len(store.entities("commitment")[0]["subtasks"]) == 1
    )
    assert (
        len(runtime.calls) == 3
    )  # only the existing one reconciliation call per exchange
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM daily_logs").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_today_does_not_assign_unknown_board_or_invent_subtask_deadline(tmp_path):
    text = "Create my launch card"
    store, proposals, runtime, domain = setup(
        tmp_path,
        [candidate(text, title="Launch", changes={"capacityId": str(uuid4())})],
    )
    result = await domain.reconcile(exchange(text))
    assert (
        result["outcome"] == "clarification_needed"
        and store.entities("commitment") == []
    )
    card = Commitments(store).create(Commitment(title="Launch"))
    text = "Add a review step to Launch"
    runtime.rows = [
        {
            **candidate(
                text, title="Launch", kind="update", target="commitment:" + card["id"]
            ),
            "subtask": {"action": "add", "title": "Review", "dueDate": "2026-10-01"},
        }
    ]
    result = await domain.reconcile(exchange(text, "date"))
    assert result["outcome"] == "clarification_needed"
    assert store.entities("commitment")[0]["subtasks"] == []
    assert proposals.list(None)["items"] == []


def test_legacy_stored_card_defaults_do_not_rewrite_record(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        store = client.app.state.store
        raw = Commitment(title="Existing card").model_dump(mode="json")
        for key in ("owner", "stage", "priority", "dueDate", "subtasks"):
            raw.pop(key)
        card = store.create("commitment", raw)
        with store.connect() as db:
            before = dict(
                db.execute(
                    "SELECT * FROM entities WHERE id=?", (card["id"],)
                ).fetchone()
            )
        read = client.get("/api/commitments").json()["items"][0]
        assert read["owner"] == "user" and read["subtasks"] == []
        with store.connect() as db:
            assert (
                dict(
                    db.execute(
                        "SELECT * FROM entities WHERE id=?", (card["id"],)
                    ).fetchone()
                )
                == before
            )


def test_subtask_remove_proposal_envelope_and_empty_edit_validation(tmp_path):
    client, _ = make(tmp_path)
    with client:
        login(client)
        item = child()
        card = client.post(
            "/api/commitments", headers=H, json={"title": "Card", "subtasks": [item]}
        ).json()
        empty = client.post(
            "/api/proposals",
            headers=H,
            json={
                "requestId": str(uuid4()),
                "threadId": "board-chat",
                "operation": "commitment.subtask",
                "input": {
                    "id": card["id"],
                    "revision": 1,
                    "action": "edit",
                    "subtaskId": item["id"],
                },
                "reason": "No changed fields",
            },
        )
        assert empty.status_code == 422
        saved = proposal(client, card, action="remove", subtaskId=item["id"])
        approved = client.post(
            f"/api/proposals/{saved['id']}/approve",
            headers=H,
            json={"fingerprint": saved["fingerprint"]},
        )
        assert approved.status_code == 200, approved.text
        assert client.get("/api/commitments").json()["items"][0]["subtasks"] == []
