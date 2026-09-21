from fastapi.testclient import TestClient
from test_api import FakeCodex, login
from test_ticket_chat import make_ticket

from leam_api.app import create_app

HEADERS = {"origin": "http://testserver"}
CATALOG = [
    {
        "model": "gpt-5.6-sol",
        "supportedReasoningEfforts": [
            {"reasoningEffort": "medium"},
            {"reasoningEffort": "high"},
        ],
    },
    {
        "model": "gpt-5.6-luna",
        "supportedReasoningEfforts": [
            {"reasoningEffort": "low"},
            {"reasoningEffort": "medium"},
        ],
    },
]


class Models(FakeCodex):
    def __init__(self):
        super().__init__()
        self.catalog = CATALOG

    async def request(self, method, params, *, expected_generation=None):
        result = await super().request(
            method, params, expected_generation=expected_generation
        )
        if method == "model/list":
            return {"data": self.catalog}
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": "new-coding", "cwd": params.get("cwd")}}
        return result


def test_default_and_saved_selection_reach_both_new_session_callers(
    tmp_path, monkeypatch
):
    codex = Models()
    app, _, ticket = make_ticket(tmp_path, monkeypatch, codex)
    with TestClient(app) as client:
        assert client.get("/api/settings/coding-model").status_code == 401
        login(client)
        assert client.get("/api/settings/coding-model").json() == {
            "model": "gpt-5.6-sol",
            "reasoningEffort": "medium",
        }
        assert (
            client.post(
                "/api/codex/threads", json={"cwd": str(tmp_path)}, headers=HEADERS
            ).status_code
            == 200
        )
        assert codex.calls[-1] == (
            "thread/start",
            {
                "cwd": str(tmp_path),
                "model": "gpt-5.6-sol",
                "config": {"model_reasoning_effort": "medium"},
            },
        )
        selected = {"model": "gpt-5.6-luna", "reasoningEffort": "low"}
        assert (
            client.post("/api/settings/coding-model", json=selected).status_code == 403
        )
        assert (
            client.post(
                "/api/settings/coding-model", json=selected, headers=HEADERS
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/updates/{ticket['id']}/chat", headers=HEADERS
            ).status_code
            == 200
        )
        assert codex.calls[-1] == (
            "thread/start",
            {
                "cwd": str(tmp_path),
                "model": "gpt-5.6-luna",
                "config": {"model_reasoning_effort": "low"},
            },
        )
    restarted = create_app(tmp_path, {"http://testserver"}, codex=Models())
    with TestClient(restarted) as client:
        assert (
            client.post(
                "/api/auth/login",
                json={"password": "long-password-for-tests"},
                headers=HEADERS,
            ).status_code
            == 200
        )
        assert client.get("/api/settings/coding-model").json() == selected


def test_invalid_or_disappeared_model_fails_before_start_or_ticket_reservation(
    tmp_path, monkeypatch
):
    codex = Models()
    app, _, ticket = make_ticket(tmp_path, monkeypatch, codex)
    with TestClient(app) as client:
        login(client)
        for body in [
            {"model": "missing", "reasoningEffort": "medium"},
            {"model": "gpt-5.6-luna", "reasoningEffort": "high"},
        ]:
            assert (
                client.post(
                    "/api/settings/coding-model", json=body, headers=HEADERS
                ).status_code
                == 422
            )
        codex.catalog = []
        url = f"/api/updates/{ticket['id']}/chat"
        assert client.post(url, headers=HEADERS).status_code == 422
        assert client.get(url).json()["state"] == "notCreated"
        assert (
            client.post(
                "/api/codex/threads", json={"cwd": str(tmp_path)}, headers=HEADERS
            ).status_code
            == 422
        )
        assert not any(m == "thread/start" for m, _ in codex.calls)
        codex.catalog = CATALOG
        assert client.post(url, headers=HEADERS).status_code == 200


def test_new_defaults_never_override_existing_resume_or_turn(tmp_path):
    codex = Models()
    app = create_app(
        tmp_path, {"http://testserver"}, bootstrap="bootstrap-for-tests", codex=codex
    )
    with TestClient(app) as client:
        login(client)
        assert (
            client.post(
                "/api/settings/coding-model",
                json={"model": "gpt-5.6-luna", "reasoningEffort": "low"},
                headers=HEADERS,
            ).status_code
            == 200
        )
        codex.calls.clear()
        assert (
            client.post(
                "/api/codex/threads/existing/connect",
                json={"handoffConfirmed": True},
                headers=HEADERS,
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/codex/threads/existing/turns",
                json={"requestId": "existing-thread-exact", "text": "Exact user text"},
                headers=HEADERS,
            ).status_code
            == 200
        )
        for method, params in codex.calls:
            assert method != "model/list"
            assert not ({"model", "effort", "config"} & params.keys())
