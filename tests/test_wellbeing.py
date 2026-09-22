from uuid import uuid4

from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.wellbeing import wellbeing_context

ORIGIN = {"Origin": "http://testserver"}


def client_for(path):
    return TestClient(
        create_app(
            path,
            {"http://testserver"},
            bootstrap="bootstrap-for-tests",
            codex=FakeCodex(),
        )
    )


def checkin():
    return {
        "requestId": str(uuid4()),
        "day": "2026-09-22",
        "timezone": "Europe/London",
        "mood": 4,
        "energy": 2,
        "stress": None,
        "notes": "A quiet morning",
    }


def test_wellbeing_caller_auth_idempotency_edit_revision_delete(tmp_path):
    with client_for(tmp_path) as client:
        assert client.get("/api/wellbeing?day=2026-09-22").status_code == 401
        login(client)
        body = checkin()
        saved = client.post("/api/wellbeing", headers=ORIGIN, json=body)
        assert saved.status_code == 200, saved.text
        row = saved.json()
        assert client.post("/api/wellbeing", headers=ORIGIN, json=body).json() == row
        assert (
            client.post(
                "/api/wellbeing", headers=ORIGIN, json={**body, "notes": "Different"}
            ).status_code
            == 409
        )
        assert len(client.get("/api/wellbeing?day=2026-09-22").json()["items"]) == 1
        edited = client.patch(
            "/api/wellbeing/" + row["id"],
            headers=ORIGIN,
            json={**body, "revision": 1, "mood": 3},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["revision"] == 2
        assert (
            client.request(
                "DELETE",
                "/api/wellbeing/" + row["id"],
                headers=ORIGIN,
                json={"revision": 1},
            ).status_code
            == 409
        )
        assert (
            client.request(
                "DELETE",
                "/api/wellbeing/" + row["id"],
                headers=ORIGIN,
                json={"revision": 2},
            ).status_code
            == 200
        )
        assert client.get("/api/wellbeing?day=2026-09-22").json()["items"] == []
        assert (
            client.post("/api/wellbeing", headers=ORIGIN, json=body).status_code == 409
        )


def test_wellbeing_validation_isolation_and_scoped_bounded_context(tmp_path):
    from leam_api.store import Store

    with client_for(tmp_path / "one") as one, client_for(tmp_path / "two") as two:
        login(one)
        login(two)
        body = checkin()
        for patch in [
            {"day": "2026-02-30"},
            {"timezone": "Invalid/Zone"},
            {"mood": 6},
            {"stress": True},
        ]:
            assert (
                one.post(
                    "/api/wellbeing", headers=ORIGIN, json={**body, **patch}
                ).status_code
                == 422
            )
        assert one.post("/api/wellbeing", headers=ORIGIN, json=body).status_code == 200
        assert two.get("/api/wellbeing?day=2026-09-22").json()["items"] == []
        store = Store(tmp_path / "one")
        assert wellbeing_context(store, "unbound") is None
        store.set("wellbeing-thread:bound", {"day": "2026-09-22"})
        context = wellbeing_context(store, "bound")
        assert context["checkins"][0]["mood"] == 4
        assert "not diagnoses" in context["meaning"]
        store.set("wellbeing-thread:elsewhere", {"day": "2026-09-21"})
        assert wellbeing_context(store, "elsewhere")["checkins"] == []
