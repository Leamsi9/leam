import json
import uuid

import httpx2
from fastapi.testclient import TestClient
from test_accounts import connect, provider_transport
from test_agenda import DAY, H, application, seed_calendar
from test_api import login


def test_calendar_hide_exact_source_survives_days_and_undo_outside_snapshot(tmp_path):
    app, calls, _ = application(tmp_path)
    with TestClient(app) as client:
        login(client)
        seed_calendar(app.state.store)
        with app.state.store.connect() as db:
            saved = json.loads(
                db.execute("SELECT body FROM calendar_snapshots").fetchone()[0]
            )
            event = next(x for x in saved if x["id"] == "all-day")
            saved.append({**event, "id": "another-instance"})
            db.execute("UPDATE calendar_snapshots SET body=?", (json.dumps(saved),))
            original = [
                tuple(x) for x in db.execute("SELECT * FROM calendar_snapshots")
            ]
        snapshot = client.get("/api/agenda", params=DAY).json()
        event = next(x for x in snapshot["events"] if x["eventId"] == "all-day")
        assert event["visibility"] == {"hidden": False, "revision": 0}
        body = {"key": event["key"], "hidden": True, "revision": 0}
        result = client.put("/api/agenda/calendar-visibility", headers=H, json=body)
        assert result.status_code == 200, result.text
        assert result.json() == {"key": event["key"], "hidden": True, "revision": 1}
        assert (
            client.put(
                "/api/agenda/calendar-visibility", headers=H, json=body
            ).status_code
            == 409
        )
        for day in (DAY, {**DAY, "date": "2026-10-24", "timezone": "UTC"}):
            current = client.get("/api/agenda", params=day).json()
            assert current["hiddenEventCount"] == 1
            assert "all-day" not in [x["eventId"] for x in current["events"]]
            assert "another-instance" in [x["eventId"] for x in current["events"]]
        included = client.get(
            "/api/agenda", params={**DAY, "includeHidden": "true"}
        ).json()
        assert next(x for x in included["events"] if x["eventId"] == "all-day")[
            "visibility"
        ] == {"hidden": True, "revision": 1}
        with app.state.store.connect() as db:
            assert [
                tuple(x) for x in db.execute("SELECT * FROM calendar_snapshots")
            ] == original
            db.execute("UPDATE calendar_snapshots SET body='[]'")
        listed = client.get("/api/agenda/calendar-visibility").json()
        assert listed["total"] == 1 and listed["items"][0]["sourceAvailable"] is False
        assert listed["items"][0]["title"] == "Date fixture"
        assert (
            client.put(
                "/api/agenda/calendar-visibility",
                headers=H,
                json={**body, "hidden": False, "revision": 1},
            ).status_code
            == 200
        )
        assert client.get("/api/agenda/calendar-visibility").json()["items"] == []
        with app.state.store.connect() as db:
            db.execute("UPDATE calendar_snapshots SET body=?", (json.dumps(saved),))
        restored = client.get("/api/agenda", params=DAY).json()
        assert next(x for x in restored["events"] if x["eventId"] == "all-day")[
            "visibility"
        ] == {"hidden": False, "revision": 2}
        assert (
            client.put(
                "/api/agenda/calendar-visibility", headers=H, json=body
            ).status_code
            == 409
        )
        assert not calls


def test_calendar_sync_and_daily_model_do_not_reintroduce_hidden_instance(tmp_path):
    app, runtime_calls, _ = application(tmp_path)
    auth = provider_transport("google", [])
    provider_calls = []

    def receive(request):
        provider_calls.append(request)
        if request.url.path.endswith("/token") or "/userinfo" in request.url.path:
            return auth.handler(request)
        assert request.method == "GET"
        if request.url.path.endswith("/calendarList"):
            return httpx2.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "fixture-calendar",
                            "summary": "Fixture calendar",
                            "accessRole": "owner",
                        }
                    ]
                },
            )
        return httpx2.Response(
            200,
            json={
                "items": [
                    {
                        "id": "series_occurrence_one",
                        "recurringEventId": "series",
                        "summary": "Hidden source title",
                        "start": {"date": "2026-10-24"},
                        "end": {"date": "2026-10-27"},
                    },
                    {
                        "id": "series_occurrence_two",
                        "recurringEventId": "series",
                        "summary": "Visible source title",
                        "start": {"date": "2026-10-24"},
                        "end": {"date": "2026-10-27"},
                    },
                ]
            },
        )

    app.state.accounts.transport = httpx2.MockTransport(receive)
    with TestClient(app) as client:
        login(client)
        account = connect(client)
        listed = client.post(f"/api/calendar/accounts/{account['id']}/sync", headers=H)
        calendar = listed.json()["items"][0]["id"]
        endpoint = f"/api/calendar/{calendar}/sync"
        window = {"start": "2026-10-24T00:00:00Z", "end": "2026-10-28T00:00:00Z"}
        assert client.post(endpoint, headers=H, json=window).status_code == 200
        item = next(
            x
            for x in client.get("/api/agenda", params=DAY).json()["events"]
            if x["eventId"] == "series_occurrence_one"
        )
        before = len(provider_calls)
        assert (
            client.put(
                "/api/agenda/calendar-visibility",
                headers=H,
                json={"key": item["key"], "hidden": True, "revision": 0},
            ).status_code
            == 200
        )
        assert len(provider_calls) == before
        assert client.post(endpoint, headers=H, json=window).status_code == 200
        current = client.get("/api/agenda", params=DAY).json()
        assert [x["eventId"] for x in current["events"]] == ["series_occurrence_two"]
        assert len(client.get(f"/api/calendar/{calendar}").json()["events"]) == 2
        before = len(provider_calls)
        thread = client.post("/api/agenda/chat", headers=H, json=DAY).json()["threadId"]
        message = {"text": "What is on my agenda?", "requestId": str(uuid.uuid4())}
        assert (
            client.post(
                f"/api/companion/threads/{thread}/messages", headers=H, json=message
            ).status_code
            == 200
        )
        reference = runtime_calls[-1]["model_context"]["reference_text"]
        assert (
            "Visible source title" in reference
            and "Hidden source title" not in reference
        )
        assert len(provider_calls) == before


def test_visibility_is_calendar_scoped_paged_and_survives_app_restart(tmp_path):
    app, _, _ = application(tmp_path)
    with TestClient(app) as client:
        login(client)
        seed_calendar(app.state.store)
        with app.state.store.connect() as db:
            db.execute(
                "INSERT INTO calendars SELECT 'other-calendar',account_id,'other-external','Other calendar',timezone,can_write,listed FROM calendars WHERE id='calendar'"
            )
            db.execute(
                "INSERT INTO calendar_snapshots SELECT 'other-calendar',start,end,body,synced,error FROM calendar_snapshots WHERE calendar_id='calendar'"
            )
        data = client.get("/api/agenda", params=DAY).json()
        first = next(
            x
            for x in data["events"]
            if x["eventId"] == "all-day" and x["calendarId"] == "calendar"
        )
        body = {"key": first["key"], "hidden": True, "revision": 0}
        assert (
            client.put("/api/agenda/calendar-visibility", json=body).status_code == 403
        )
        assert (
            client.put(
                "/api/agenda/calendar-visibility", headers=H, json=body
            ).status_code
            == 200
        )
        items = client.get("/api/agenda", params=DAY).json()["events"]
        assert any(
            x["eventId"] == "all-day" and x["calendarId"] == "other-calendar"
            for x in items
        )
        second = next(
            x
            for x in items
            if x["eventId"] == "overnight" and x["calendarId"] == "calendar"
        )
        assert (
            client.put(
                "/api/agenda/calendar-visibility",
                headers=H,
                json={**body, "key": second["key"]},
            ).status_code
            == 200
        )
        page = client.get("/api/agenda/calendar-visibility", params={"limit": 1}).json()
        assert page["total"] == 2 and page["nextOffset"] == 1
        next_page = client.get(
            "/api/agenda/calendar-visibility",
            params={"offset": page["nextOffset"], "limit": 1},
        ).json()
        assert (
            next_page["nextOffset"] is None
            and page["items"][0]["key"] != next_page["items"][0]["key"]
        )
        for invalid in [
            {**body, "key": "calendar:" + "0" * 64},
            {**body, "key": "commitment:" + "0" * 64},
            {**body, "hidden": "true"},
        ]:
            assert client.put(
                "/api/agenda/calendar-visibility", headers=H, json=invalid
            ).status_code in (404, 422)
    restarted, _, _ = application(tmp_path)
    with TestClient(restarted) as client:
        assert client.get("/api/agenda/calendar-visibility").status_code == 401
        assert (
            client.post(
                "/api/auth/login",
                headers=H,
                json={"password": "long-password-for-tests"},
            ).status_code
            == 200
        )
        data = client.get("/api/agenda", params=DAY).json()
        assert data["hiddenEventCount"] == 2
        assert not any(x["calendarId"] == "calendar" for x in data["events"])
        assert client.get("/api/agenda/calendar-visibility").json()["total"] == 2
