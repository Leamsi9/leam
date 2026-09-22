import json
import time
import uuid

import httpx
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.ironclaw import IronClaw
from leam_api.vault import Vault

H = {"Origin": "http://testserver"}
DAY = {"date": "2026-10-25", "timezone": "Europe/London"}


def application(tmp_path, *, fail_first_create=False, delete_status=200):
    calls = []
    creates = []

    def receive(request):
        body = json.loads(request.content) if request.content else None
        if request.url.path.endswith("/threads") and request.method == "POST":
            creates.append(body)
            if fail_first_create and len(creates) == 1:
                raise httpx.ReadTimeout("lost receipt", request=request)
            return httpx.Response(
                200,
                json={
                    "thread": {
                        "thread_id": str(
                            uuid.uuid5(uuid.NAMESPACE_URL, body["client_action_id"])
                        )
                    }
                },
            )
        if request.method == "DELETE" and "/threads/" in request.url.path:
            return httpx.Response(delete_status, json={"deleted": True})
        if request.url.path.endswith("/session"):
            return httpx.Response(
                200,
                json={
                    "session_channel_extension_id": "web",
                    "tenant_id": "fixture-tenant",
                    "user_id": "fixture-user",
                },
            )
        if request.url.path.endswith("/timeline"):
            thread_id = request.url.path.split("/")[-2]
            return httpx.Response(
                200,
                json={
                    "thread": {
                        "thread_id": thread_id,
                        "scope": {
                            "tenant_id": "fixture-tenant",
                            "owner_user_id": "fixture-user",
                        },
                    },
                    "messages": [],
                },
            )
        if request.url.path.endswith("/messages"):
            calls.append(body)
            return httpx.Response(
                200, json={"accepted": True, "run_id": str(uuid.uuid4())}
            )
        return httpx.Response(200, json={})

    runtime = IronClaw(
        "http://127.0.0.1:46410",
        None,
        token="fixture",
        transport=httpx.MockTransport(receive),
    )
    return (
        create_app(
            tmp_path,
            {"http://testserver"},
            bootstrap="bootstrap-for-tests",
            codex=FakeCodex(),
            runtime=runtime,
        ),
        calls,
        creates,
    )


def seed_calendar(store, *, error=None):
    now = time.time()
    events = [
        {
            "id": "overnight",
            "title": "Overnight fixture",
            "start": "2026-10-24T22:30:00+00:00",
            "end": "2026-10-24T23:30:00+00:00",
            "allDay": False,
            "url": None,
        },
        {
            "id": "all-day",
            "title": "Date fixture",
            "start": "2026-10-24",
            "end": "2026-10-26",
            "allDay": True,
            "url": None,
        },
        {
            "id": "outside",
            "title": "Outside fixture",
            "start": "2026-10-26T00:00:00+00:00",
            "end": "2026-10-26T01:00:00+00:00",
            "allDay": False,
            "url": None,
        },
    ]
    with store.connect() as db:
        db.execute(
            "INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "account",
                "google",
                "fixture@example.test",
                Vault(store.path.parent).seal(
                    "account:account", {"clientId": "fixture", "token": {}}
                ),
                "connected",
                None,
                now,
                now,
                "subject",
            ),
        )
        db.execute("INSERT INTO calendar_scans VALUES (?,?,NULL)", ("account", now))
        db.execute(
            "INSERT INTO calendars VALUES (?,?,?,?,?,?,?)",
            (
                "calendar",
                "account",
                "external",
                "Fixture calendar",
                "Europe/London",
                1,
                now,
            ),
        )
        db.execute(
            "INSERT INTO calendar_snapshots VALUES (?,?,?,?,?,?)",
            (
                "calendar",
                "2026-10-24T23:00:00+00:00",
                "2026-10-26T00:00:00+00:00",
                json.dumps(events),
                now,
                error,
            ),
        )


def test_agenda_actual_caller_snapshot_dst_and_local_cas(tmp_path):
    app, calls, creates = application(tmp_path)
    with TestClient(app) as c:
        assert c.get("/api/agenda", params=DAY).status_code == 401
        login(c)
        item = c.post(
            "/api/commitments", headers=H, json={"title": "Canonical fixture"}
        ).json()
        seed_calendar(
            app.state.store, error="Refresh failed; previous snapshot retained"
        )
        original = c.get("/api/today", params={"date": DAY["date"]}).json()
        r = c.get("/api/agenda", params=DAY)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["window"] == {
            "start": "2026-10-25T00:00:00+01:00",
            "end": "2026-10-26T00:00:00+00:00",
        }
        assert {x["eventId"] for x in data["events"]} == {"overnight", "all-day"}
        assert data["sources"]["calendar"]["state"] == "partial"
        assert data["sources"]["calendar"]["snapshots"][0]["state"] == "error"
        assert data["sources"]["email"]["state"] == "not_connected"
        row = data["commitments"][0]
        assert row["id"] == item["id"] and row["log"] == original["items"][0]["log"]
        body = {**DAY, "key": row["key"], "revision": 0, "disposition": "focus"}
        assert c.put("/api/agenda/triage", json=body).status_code == 403
        saved = c.put("/api/agenda/triage", headers=H, json=body)
        assert saved.status_code == 200, saved.text
        assert saved.json()["revision"] == 1
        assert c.put("/api/agenda/triage", headers=H, json=body).status_code == 409
        assert (
            c.get("/api/agenda", params=DAY).json()["commitments"][0]["triage"][
                "disposition"
            ]
            == "focus"
        )
        assert (
            c.get("/api/agenda", params={**DAY, "date": "2026-10-26"}).json()[
                "commitments"
            ][0]["triage"]["disposition"]
            == "none"
        )
        assert c.get("/api/today", params={"date": DAY["date"]}).json() == original
        assert (
            c.put(
                "/api/agenda/triage", headers=H, json={**body, "key": "invented"}
            ).status_code
            == 404
        )
        assert not calls and not creates


def test_agenda_chat_current_reference_raw_text_and_retry(tmp_path):
    app, calls, creates = application(tmp_path)
    with TestClient(app) as c:
        login(c)
        item = c.post(
            "/api/commitments", headers=H, json={"title": "Initial fixture"}
        ).json()
        assert c.get("/api/agenda/chat", params=DAY).json()["threadId"] is None
        r = c.post("/api/agenda/chat", headers=H, json=DAY)
        assert r.status_code == 200, r.text
        thread = r.json()["threadId"]
        assert (
            c.post("/api/agenda/chat", headers=H, json=DAY).json()["threadId"] == thread
        )
        assert len(creates) == 1
        assert (
            c.post(
                "/api/agenda/chat", headers=H, json={**DAY, "date": "2026-10-26"}
            ).json()["threadId"]
            != thread
        )
        send = f"/api/companion/threads/{thread}/messages"
        message = {"requestId": str(uuid.uuid4()), "text": "Exact user text\n界"}
        assert c.post(send, headers=H, json=message).status_code == 200
        first = calls[-1]
        assert first["content"] == message["text"]
        reference = first["model_context"]["reference_text"]
        assert "dailyAgenda" in reference and "Initial fixture" in reference
        assert len(reference.encode()) <= 8192
        assert (
            c.patch(
                f"/api/commitments/{item['id']}",
                headers=H,
                json={"revision": item["revision"], "title": "Latest fixture"},
            ).status_code
            == 200
        )
        assert c.post(send, headers=H, json=message).status_code == 200
        assert len(calls) == 1
        assert (
            c.post(
                send, headers=H, json={**message, "requestId": str(uuid.uuid4())}
            ).status_code
            == 200
        )
        assert "Latest fixture" in calls[-1]["model_context"]["reference_text"]
        assert "Initial fixture" not in calls[-1]["model_context"]["reference_text"]
        assert (
            c.get("/api/agenda", params={**DAY, "timezone": "../bad"}).status_code
            == 422
        )


def test_agenda_pagination_and_instant_events_preserve_source_snapshot(tmp_path):
    app, _, _ = application(tmp_path)
    with TestClient(app) as c:
        login(c)
        assert (
            c.get("/api/agenda", params=DAY).json()["sources"]["calendar"]["state"]
            == "not_connected"
        )
        seed_calendar(app.state.store)
        with app.state.store.connect() as db:
            row = db.execute("SELECT body FROM calendar_snapshots").fetchone()
            events = json.loads(row[0]) + [
                {
                    "id": "instant-start",
                    "title": "Boundary instant",
                    "start": "2026-10-24T23:00:00+00:00",
                    "end": "2026-10-24T23:00:00+00:00",
                    "allDay": False,
                },
                {
                    "id": "instant-end",
                    "title": "Excluded boundary",
                    "start": "2026-10-26T00:00:00+00:00",
                    "end": "2026-10-26T00:00:00+00:00",
                    "allDay": False,
                },
            ]
            original = json.dumps(events)
            db.execute("UPDATE calendar_snapshots SET body=?", (original,))
        for n in range(3):
            assert (
                c.post(
                    "/api/commitments", headers=H, json={"title": f"Fixture {n}"}
                ).status_code
                == 200
            )
        first_page = c.get("/api/agenda", params={**DAY, "limit": 2}).json()
        assert len(first_page["events"]) == 2 and not first_page["commitments"]
        offset, rows = 0, []
        while offset is not None:
            r = c.get("/api/agenda", params={**DAY, "limit": 2, "offset": offset})
            assert r.status_code == 200
            data = r.json()
            rows.extend(data["commitments"] + data["events"])
            offset = data["nextOffset"]
        assert len(rows) == len({x["key"] for x in rows}) == 6
        event = next(x for x in rows if x.get("eventId") == "instant-start")
        result = c.put(
            "/api/agenda/triage",
            headers=H,
            json={
                **DAY,
                "key": event["key"],
                "revision": 0,
                "disposition": "dismissed",
            },
        )
        assert result.status_code == 200
        with app.state.store.connect() as db:
            assert (
                db.execute("SELECT body FROM calendar_snapshots").fetchone()[0]
                == original
            )
        assert c.get("/api/agenda", params={**DAY, "date": "2026-03-29"}).json()[
            "window"
        ] == {"start": "2026-03-29T00:00:00+00:00", "end": "2026-03-30T00:00:00+01:00"}
        assert (
            c.get("/api/agenda", params={**DAY, "date": "9999-12-31"}).status_code
            == 422
        )


def test_agenda_context_unicode_budget_fresh_calendar_and_no_provider_calls(tmp_path):
    app, calls, _ = application(tmp_path)
    with TestClient(app) as c:
        login(c)
        seed_calendar(app.state.store)
        for n in range(8):
            c.post(
                "/api/commitments",
                headers=H,
                json={"title": f"{n}" + "界" * 499, "notes": "😀" * 20000},
            )
        for n in range(10):
            app.state.store.create(
                "memory",
                {
                    "text": "memory reference " + "😀" * 1000,
                    "source": "source" + "界" * 1000,
                    "category": "preference",
                },
            )
        thread = c.post("/api/agenda/chat", headers=H, json=DAY).json()["threadId"]
        r = c.post(
            f"/api/companion/threads/{thread}/messages",
            headers=H,
            json={"text": "memory reference", "requestId": str(uuid.uuid4())},
        )
        assert r.status_code == 200, r.text
        reference = calls[-1]["model_context"]["reference_text"]
        assert len(reference.encode()) <= 8192
        context, _ = json.JSONDecoder().raw_decode(
            reference.split("<leam_context>", 1)[1]
        )
        assert (
            len(json.dumps(context["dailyAgenda"], ensure_ascii=False).encode()) <= 2048
        )
        assert context["dailyAgenda"]["partial"]
        assert context["dailyAgenda"]["items"][0]["id"]
        assert context["dailyAgenda"]["items"][0]["titlePartial"]
        assert context["dailyAgenda"]["calendarState"] == "current"
        with app.state.store.connect() as db:
            db.execute("UPDATE calendar_snapshots SET error='Fixture refresh error'")
        r = c.post(
            f"/api/companion/threads/{thread}/messages",
            headers=H,
            json={"text": "Current calendar coverage?", "requestId": str(uuid.uuid4())},
        )
        assert r.status_code == 200
        assert (
            '"calendarState": "partial"' in calls[-1]["model_context"]["reference_text"]
        )


def test_agenda_chat_uncertain_create_reuses_reserved_action_and_survives_restart(
    tmp_path,
):
    app, _, creates = application(tmp_path, fail_first_create=True)
    from leam_api.agenda import Agenda, Selection

    with TestClient(app) as c:
        login(c)
        first = c.post("/api/agenda/chat", headers=H, json=DAY)
        assert first.status_code == 502, first.text
        second = c.post("/api/agenda/chat", headers=H, json=DAY)
        assert second.status_code == 200, second.text
        assert creates[0] == creates[1]
        assert (
            Agenda(app.state.store).chat(Selection(**DAY))["threadId"]
            == second.json()["threadId"]
        )
        assert (
            c.post(
                "/api/agenda/chat", headers={"Origin": "https://other.test"}, json=DAY
            ).status_code
            == 403
        )
        assert len(creates) == 2


def test_confirmed_chat_delete_retires_only_matching_daily_binding(tmp_path):
    from leam_api.agenda import Agenda, Selection

    app, calls, creates = application(tmp_path)
    with TestClient(app) as c:
        login(c)
        thread = c.post("/api/agenda/chat", headers=H, json=DAY).json()["threadId"]
        other_day = {**DAY, "date": "2026-10-26"}
        other = c.post("/api/agenda/chat", headers=H, json=other_day).json()["threadId"]
        message = {"requestId": str(uuid.uuid4()), "text": "Preserve action receipt"}
        assert (
            c.post(
                f"/api/companion/threads/{thread}/messages", headers=H, json=message
            ).status_code
            == 200
        )
        with app.state.store.connect() as db:
            audit = [tuple(r) for r in db.execute("SELECT * FROM runtime_actions")]
        deletion = f"/api/companion/threads/{thread}"
        assert (
            c.request(
                "DELETE", deletion, headers=H, json={"confirmed": False}
            ).status_code
            == 422
        )
        assert c.get("/api/agenda/chat", params=DAY).json()["threadId"] == thread
        assert (
            c.request(
                "DELETE", deletion, headers=H, json={"confirmed": True}
            ).status_code
            == 200
        )
        assert c.get("/api/agenda/chat", params=DAY).json()["threadId"] is None
        assert app.state.store.get("companion-agenda:" + thread) is None
        assert c.get("/api/agenda/chat", params=other_day).json()["threadId"] == other
        fresh = c.post("/api/agenda/chat", headers=H, json=DAY).json()["threadId"]
        assert fresh != thread
        assert creates[0]["client_action_id"] != creates[-1]["client_action_id"]
        # A stale reverse mapping cannot retire its newer replacement.
        app.state.store.set("companion-agenda:" + thread, DAY)
        assert (
            c.request(
                "DELETE", deletion, headers=H, json={"confirmed": True}
            ).status_code
            == 200
        )
        assert Agenda(app.state.store).chat(Selection(**DAY))["threadId"] == fresh
        with app.state.store.connect() as db:
            assert [
                tuple(r) for r in db.execute("SELECT * FROM runtime_actions")
            ] == audit
        assert len(calls) == 1


def test_native_busy_delete_preserves_daily_binding(tmp_path):
    app, _, _ = application(tmp_path, delete_status=409)
    with TestClient(app) as c:
        login(c)
        thread = c.post("/api/agenda/chat", headers=H, json=DAY).json()["threadId"]
        result = c.request(
            "DELETE",
            f"/api/companion/threads/{thread}",
            headers=H,
            json={"confirmed": True},
        )
        assert result.status_code == 409
        assert c.get("/api/agenda/chat", params=DAY).json()["threadId"] == thread
        assert app.state.store.get("companion-agenda:" + thread) == DAY


def test_focused_offpage_commitment_returns_on_first_page_after_reload(tmp_path):
    app, calls, creates = application(tmp_path)
    with TestClient(app) as c:
        login(c)
        for title in ("A fixture", "B fixture", "Z focused fixture"):
            assert (
                c.post("/api/commitments", headers=H, json={"title": title}).status_code
                == 200
            )
        seed_calendar(app.state.store)
        before = c.get("/api/agenda", params={**DAY, "limit": 2}).json()
        assert not before["commitments"]
        last = c.get("/api/agenda", params={**DAY, "offset": 4, "limit": 2}).json()[
            "commitments"
        ][0]
        assert last["title"] == "Z focused fixture"
        assert (
            c.put(
                "/api/agenda/triage",
                headers=H,
                json={**DAY, "key": last["key"], "revision": 0, "disposition": "focus"},
            ).status_code
            == 200
        )
        reloaded = c.get("/api/agenda", params={**DAY, "limit": 2}).json()
        assert reloaded["commitments"][0]["id"] == last["id"]
        assert reloaded["commitments"][0]["triage"]["disposition"] == "focus"
        assert reloaded["events"][0] == before["events"][0]
        assert reloaded["total"] == before["total"]
        assert not calls and not creates
