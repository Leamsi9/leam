import json

from fastapi.testclient import TestClient
from test_agenda import DAY, H, application, seed_calendar
from test_api import login


def test_explicit_selected_procedure_uses_day_context_and_exact_retry(tmp_path):
    app, calls, creates = application(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/procedures").status_code == 401
        login(client)
        catalog = client.get("/api/procedures").json()["items"]
        assert {p["id"] for p in catalog} == {"what-now", "overload"}
        assert all(p["adoption"]["state"] == "proposed" for p in catalog)
        assert not calls and not creates
        seed_calendar(app.state.store, error="Unavailable source")
        thread = client.post("/api/agenda/chat", json=DAY, headers=H).json()["threadId"]
        body = {
            "text": "Please help me decide.",
            "requestId": "procedure-request-1",
            "procedure": {"id": "what-now", "version": 1},
        }
        path = f"/api/companion/threads/{thread}/messages"
        assert client.post(path, json=body, headers=H).status_code == 200
        payload = calls[-1]
        assert payload["content"] == body["text"]
        text = payload["model_context"]["reference_text"]
        assert len(text.encode()) <= 8192
        context = json.loads(
            text.split("<leam_context>", 1)[1].removesuffix("</leam_context>")
        )
        assert context["selectedProcedure"]["id"] == "what-now"
        assert context["dailyAgenda"]["calendarState"] != "ready"
        assert "overload" not in json.dumps(context["selectedProcedure"]).lower()
        assert client.post(path, json=body, headers=H).status_code == 200
        assert len(calls) == 1
        assert (
            client.post(
                path,
                json={**body, "procedure": {"id": "overload", "version": 1}},
                headers=H,
            ).status_code
            == 409
        )
        assert (
            client.post(
                path,
                json={"text": "Just talking", "requestId": "ordinary-request-2"},
                headers=H,
            ).status_code
            == 200
        )
        assert "selectedProcedure" not in calls[-1]["model_context"]["reference_text"]


def test_catalog_adoption_cas_retirement_and_today_requirement(tmp_path):
    app, calls, _ = application(tmp_path)
    with TestClient(app) as client:
        login(client)
        body = {"version": 1, "revision": 0, "state": "adopted", "sourceRefs": []}
        assert client.put("/api/procedures/what-now", json=body).status_code == 403
        adopted = client.put("/api/procedures/what-now", json=body, headers=H)
        assert adopted.status_code == 200
        assert adopted.json()["adoption"]["revision"] == 1
        assert adopted.json()["personalSourceMapping"] == "not_linked"
        assert (
            client.put("/api/procedures/what-now", json=body, headers=H).status_code
            == 409
        )
        selected = {
            "text": "Help",
            "requestId": "procedure-request-3",
            "procedure": {"id": "what-now", "version": 1},
        }
        assert (
            client.post(
                "/api/companion/threads/unbound/messages", json=selected, headers=H
            ).status_code
            == 409
        )
        thread = client.post("/api/agenda/chat", json=DAY, headers=H).json()["threadId"]
        path = f"/api/companion/threads/{thread}/messages"
        assert (
            client.post(
                path,
                json={**selected, "procedure": {"id": "what-now", "version": 2}},
                headers=H,
            ).status_code
            == 409
        )
        assert (
            client.put(
                "/api/procedures/what-now",
                json={**body, "revision": 1, "state": "retired"},
                headers=H,
            ).status_code
            == 200
        )
        assert client.post(path, json=selected, headers=H).status_code == 409
        assert not calls


def test_private_source_refs_are_explicit_metadata_and_bounded_context(tmp_path):
    app, calls, _ = application(tmp_path)
    with TestClient(app) as client:
        login(client)
        source = {
            "sourceSha256": "a" * 64,
            "messageId": "11111111-1111-4111-8111-111111111111",
            "startLine": 2,
            "endLine": 5,
            "auditProcedureId": "P013",
        }
        body = {
            "version": 1,
            "revision": 0,
            "state": "proposed",
            "sourceRefs": [source],
        }
        item = client.put("/api/procedures/what-now", json=body, headers=H).json()
        assert item["personalSourceMapping"] == "owner_supplied_unverified"
        assert item["adoption"]["state"] == "proposed"
        assert (
            client.put(
                "/api/procedures/overload",
                json={**body, "sourceRefs": [source] * 9},
                headers=H,
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/api/procedures/overload",
                json={**body, "sourceRefs": [{**source, "endLine": 1}]},
                headers=H,
            ).status_code
            == 422
        )
        for i in range(10):
            app.state.store.create(
                "memory",
                {"text": "🧠" * 4000, "source": "s" * 1000, "category": "preference"},
            )
        seed_calendar(app.state.store)
        thread = client.post("/api/agenda/chat", json=DAY, headers=H).json()["threadId"]
        response = client.post(
            f"/api/companion/threads/{thread}/messages",
            json={
                "text": "What now?",
                "requestId": "unicode-procedure-request",
                "procedure": {"id": "what-now", "version": 1},
            },
            headers=H,
        )
        assert response.status_code == 200
        text = calls[-1]["model_context"]["reference_text"]
        assert len(text.encode()) <= 8192
        assert source["messageId"] not in text


def test_uncertain_retry_keeps_admitted_procedure_after_retirement(tmp_path):
    from leam_api.ironclaw import RuntimeError

    app, calls, _ = application(tmp_path)
    runtime = app.state.emails.classifier.runtime
    request = runtime.request
    lost = True

    async def uncertain(method, path, body=None):
        nonlocal lost
        result = await request(method, path, body)
        if path.endswith("/messages") and lost:
            lost = False
            raise RuntimeError("Synthetic lost receipt")
        return result

    runtime.request = uncertain
    with TestClient(app) as client:
        login(client)
        thread = client.post("/api/agenda/chat", json=DAY, headers=H).json()["threadId"]
        path = f"/api/companion/threads/{thread}/messages"
        body = {
            "text": "Help me start",
            "requestId": "uncertain-procedure-request",
            "procedure": {"id": "overload", "version": 1},
        }
        assert client.post(path, json=body, headers=H).status_code == 502
        assert (
            client.put(
                "/api/procedures/overload",
                json={
                    "version": 1,
                    "revision": 0,
                    "state": "retired",
                    "sourceRefs": [],
                },
                headers=H,
            ).status_code
            == 200
        )
        assert client.post(path, json=body, headers=H).status_code == 200
        assert calls[0] == calls[1]
        assert (
            client.post(
                path, json={**body, "requestId": "fresh-retired-request"}, headers=H
            ).status_code
            == 409
        )
        assert len(calls) == 2
