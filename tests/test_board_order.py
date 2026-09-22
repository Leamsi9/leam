"""Authenticated caller coverage; order metadata never mutates canonical cards."""

from uuid import uuid4

from test_api import login, make

ORIGIN = {"origin": "http://testserver"}


def seed(client):
    login(client)
    capacity = client.post(
        "/api/capacities", json={"name": "Work"}, headers=ORIGIN
    ).json()["id"]
    cards = []
    for title, stage in [("Alpha", "todo"), ("Beta", "blocked"), ("Gamma", "todo")]:
        response = client.post(
            "/api/commitments",
            json={
                "title": title,
                "capacityId": capacity,
                "stage": stage,
                "kind": "habit",
            },
            headers=ORIGIN,
        )
        assert response.status_code == 200, response.text
        cards.append(response.json())
    return capacity, cards


def order(client, capacity):
    return client.get("/api/commitments").json()["boardOrders"][
        capacity or "unassigned"
    ]


def request(client, capacity, card, before=None, lane=None):
    current = order(client, capacity)
    return {
        "requestId": str(uuid4()),
        "capacityId": capacity,
        "revision": current["revision"],
        "membershipToken": current["membershipToken"],
        "cardId": card["id"],
        "beforeId": before and before["id"],
        "lane": lane,
    }


def put(client, body):
    return client.put("/api/commitments/order", json=body, headers=ORIGIN)


def test_order_is_authenticated_origin_checked_and_shape_bounded(tmp_path):
    client, codex = make(tmp_path)
    with client:
        assert client.get("/api/commitments").status_code == 401
        assert (
            client.put("/api/commitments/order", json={}, headers=ORIGIN).status_code
            == 401
        )
        capacity, cards = seed(client)
        body = request(client, capacity, cards[2], cards[0])
        assert (
            client.put(
                "/api/commitments/order",
                json=body,
                headers={"origin": "https://evil.invalid"},
            ).status_code
            == 403
        )
        for field, value in [
            ("requestId", "bad"),
            ("revision", -1),
            ("revision", True),
            ("membershipToken", "bad"),
            ("lane", "unknown"),
            ("beforeId", cards[2]["id"]),
        ]:
            assert put(client, {**body, field: value}).status_code == 422
        assert put(client, {**body, "priority": "high"}).status_code == 422
        assert order(client, capacity)["revision"] == 0
        assert codex.calls == []


def test_move_preserves_cards_daily_progress_and_hidden_relative_order(tmp_path):
    client, _ = make(tmp_path)
    with client:
        capacity, cards = seed(client)
        a, b, c = cards
        log = client.put(
            f"/api/commitments/{a['id']}/progress/2026-09-21",
            json={"revision": 0, "commitmentRevision": 1, "operation": "toggle"},
            headers=ORIGIN,
        )
        assert log.status_code == 200
        before = client.get("/api/commitments").json()["items"]
        history = client.get(f"/api/commitments/{a['id']}/history").json()
        result = put(client, request(client, capacity, c, a, "todo"))
        assert result.status_code == 200, result.text
        assert result.json()["ids"] == [c["id"], a["id"], b["id"]]
        assert client.get("/api/commitments").json()["items"] == before
        assert client.get(f"/api/commitments/{a['id']}/history").json() == history
        # Send the first todo card to this lane's end, preserving the blocked card.
        result = put(client, request(client, capacity, c, lane="todo"))
        assert result.json()["ids"] == [a["id"], c["id"], b["id"]]


def test_exact_retry_after_later_order_returns_old_receipt_without_reapplying(tmp_path):
    client, _ = make(tmp_path)
    with client:
        capacity, cards = seed(client)
        first = request(client, capacity, cards[2], cards[0])
        receipt = put(client, first)
        assert receipt.status_code == 200
        second = put(client, request(client, capacity, cards[0], cards[2]))
        assert second.json()["revision"] == 2
        replay = put(client, first)
        assert replay.json() == receipt.json()
        assert order(client, capacity) == second.json()
        assert put(client, {**first, "beforeId": cards[1]["id"]}).status_code == 409
    # Reopen the actual application over the same isolated store.
    client2, _ = make(tmp_path)
    with client2:
        auth = client2.post(
            "/api/auth/login",
            json={"password": "long-password-for-tests"},
            headers=ORIGIN,
        )
        assert auth.status_code == 200
        assert put(client2, first).json() == receipt.json()
        assert order(client2, capacity) == second.json()


def test_stale_revision_or_changed_member_fails_closed(tmp_path):
    client, _ = make(tmp_path)
    with client:
        capacity, cards = seed(client)
        stale = request(client, capacity, cards[2], cards[0])
        assert put(client, stale).status_code == 200
        assert put(client, {**stale, "requestId": str(uuid4())}).status_code == 409
        stale = request(client, capacity, cards[2], cards[0])
        edit = client.patch(
            f"/api/commitments/{cards[1]['id']}",
            json={"revision": 1, "stage": "todo"},
            headers=ORIGIN,
        )
        assert edit.status_code == 200, edit.text
        assert put(client, stale).status_code == 409
        assert order(client, capacity)["revision"] == 1


def test_lane_and_cross_capacity_destinations_are_revalidated(tmp_path):
    client, _ = make(tmp_path)
    with client:
        capacity, cards = seed(client)
        assert (
            put(
                client, request(client, capacity, cards[0], cards[1], "todo")
            ).status_code
            == 409
        )
        assert (
            put(client, request(client, capacity, cards[1], lane="todo")).status_code
            == 409
        )
        foreign = client.post(
            "/api/commitments", json={"title": "Elsewhere"}, headers=ORIGIN
        ).json()
        assert (
            put(client, request(client, capacity, cards[0], foreign)).status_code == 409
        )
        assert (
            put(client, request(client, capacity, foreign, cards[0])).status_code == 409
        )
        assert order(client, capacity)["revision"] == 0
        # Membership token also covers a card that moved out of this board.
        stale = request(client, capacity, cards[0], cards[2])
        assert (
            client.patch(
                f"/api/commitments/{cards[2]['id']}",
                json={"revision": 1, "capacityId": None},
                headers=ORIGIN,
            ).status_code
            == 200
        )
        assert put(client, stale).status_code == 409
        assert cards[2]["id"] not in order(client, capacity)["ids"]
        assert cards[2]["id"] in order(client, None)["ids"]


def test_new_members_append_without_rewriting_existing_order(tmp_path):
    client, _ = make(tmp_path)
    with client:
        capacity, cards = seed(client)
        result = put(client, request(client, capacity, cards[2], cards[0])).json()
        new = client.post(
            "/api/commitments",
            json={"title": "Aardvark", "capacityId": capacity},
            headers=ORIGIN,
        ).json()
        refreshed = order(client, capacity)
        assert refreshed["ids"] == result["ids"] + [new["id"]]
        assert refreshed["revision"] == result["revision"]
        assert refreshed["membershipToken"] != result["membershipToken"]
