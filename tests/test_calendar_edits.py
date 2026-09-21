import copy
import json
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from test_accounts import H, connect, provider_transport
from test_api import FakeCodex, login

from leam_api.app import create_app


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_personal_event_edits_and_removal_recover_lost_responses_and_stop_stale_writes(
    tmp_path, provider
):
    auth = provider_transport(provider, [])
    external = {}
    writes = []
    lose = [False]
    reject = [False]

    def handle(request):
        path = request.url.path
        if path.endswith("/token") or "/userinfo" in path or path == "/v1.0/me":
            return auth.handler(request)
        if path.endswith("/calendarList"):
            return httpx2.Response(
                200,
                json={
                    "items": [
                        {"id": "primary", "summary": "Personal", "accessRole": "owner"}
                    ]
                },
            )
        if path == "/v1.0/me/calendars":
            return httpx2.Response(
                200,
                json={
                    "value": [{"id": "primary", "name": "Personal", "canEdit": True}]
                },
            )
        if request.method == "POST":
            payload = json.loads(request.content)
            external.update(
                {**payload, "id": "event-id", "etag": '"v1"', "@odata.etag": '"v1"'}
            )
            return httpx2.Response(201, json=external)
        if request.method == "GET":
            return (
                httpx2.Response(200, json=external)
                if external
                else httpx2.Response(404, json={})
            )
        writes.append(request)
        if reject[0]:
            reject[0] = False
            external["etag"] = external["@odata.etag"] = '"v3"'
            return httpx2.Response(412, json={})
        assert request.headers["If-Match"] == external["etag"]
        if request.method == "PATCH":
            external.update(json.loads(request.content))
            external["etag"] = external["@odata.etag"] = '"v2"'
            if lose[0]:
                raise httpx2.ReadTimeout("edit committed")
            return httpx2.Response(200, json=external)
        external.clear()
        if lose[0]:
            raise httpx2.ReadTimeout("deletion committed")
        return httpx2.Response(204)

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        account_transport=httpx2.MockTransport(handle),
    )
    with TestClient(app, client=("127.0.0.1", 1234)) as c:
        login(c)
        account = connect(c, provider)
        calendar = c.post(
            "/api/calendar/accounts/" + account["id"] + "/sync", json={}, headers=H
        ).json()["items"][0]
        item = c.post("/api/commitments", json={"title": "Focus"}, headers=H).json()
        schedule = {
            "calendarId": calendar["id"],
            "commitmentId": item["id"],
            "commitmentRevision": 1,
            "date": "2026-09-20",
            "time": "10:00",
            "timezone": "Europe/London",
            "minutes": 30,
            "fold": 0,
        }
        preview = c.post(
            "/api/calendar/actions/preview", json=schedule, headers=H
        ).json()
        creation = str(uuid4())
        assert (
            c.post(
                "/api/calendar/actions",
                json={
                    "requestId": creation,
                    "schedule": schedule,
                    "previewDigest": preview["digest"],
                },
                headers=H,
            ).status_code
            == 200
        )
        inspected = c.get("/api/calendar/actions/" + creation + "/event")
        assert inspected.status_code == 200, inspected.text
        assert inspected.json()["event"]["etag"] == '"v1"'
        bearer = {"authorization": "Bearer " + (tmp_path / "tools-token").read_text()}
        links = c.post(
            "/api/internal/tools",
            headers=bearer,
            json={
                "tool": "leam_calendar_links",
                "arguments": {"calendarId": calendar["id"], "limit": 1},
            },
        )
        assert links.status_code == 200, links.text
        assert links.json()["items"][0]["requestId"] == creation
        external["etag"] = external["@odata.etag"] = '"external-read-version"'
        live = c.post(
            "/api/internal/tools",
            headers=bearer,
            json={
                "tool": "leam_calendar_inspect",
                "arguments": {"creationId": creation},
            },
        )
        assert live.status_code == 200, live.text
        assert live.json()["event"]["etag"] == '"external-read-version"'
        assert live.json()["source"] == "leam:calendar-action/" + creation
        assert live.json()["checkedAt"] > 0
        assert writes == []
        external["etag"] = external["@odata.etag"] = '"v1"'
        edit = {
            k: v
            for k, v in schedule.items()
            if k not in ("calendarId", "commitmentId", "commitmentRevision")
        }
        change = {
            "requestId": str(uuid4()),
            "creationId": creation,
            "operation": "edit",
            "expectedEtag": '"v1"',
            "edit": {**edit, "title": "Later focus", "time": "11:00"},
        }
        lose[0] = True
        assert (
            c.post("/api/calendar/changes", json=change, headers=H).status_code == 502
        )
        other = {**change, "requestId": str(uuid4())}
        pending_conflict = c.post("/api/calendar/changes", json=other, headers=H)
        assert pending_conflict.status_code == 409
        assert "pending" in pending_conflict.text
        assert len(writes) == 1
    restarted = create_app(
        tmp_path,
        {"http://testserver"},
        codex=FakeCodex(),
        account_transport=httpx2.MockTransport(handle),
    )
    with TestClient(restarted) as c:
        assert (
            c.post(
                "/api/auth/login",
                json={"password": "long-password-for-tests"},
                headers=H,
            ).status_code
            == 200
        )
        assert (
            c.get("/api/calendar/actions/" + creation + "/changes").json()["items"][0][
                "state"
            ]
            == "pending"
        )
        retry = c.post("/api/calendar/changes", json=change, headers=H)
        assert retry.status_code == 200, retry.text
        assert retry.json()["event"]["title"] == "Later focus"
        assert len(writes) == 1
        assert (
            c.post("/api/calendar/changes", json=change, headers=H).json()
            == retry.json()
        )
        changed = {
            **change,
            "requestId": str(uuid4()),
            "edit": {**change["edit"], "title": "Overwrite"},
        }
        assert (
            c.post("/api/calendar/changes", json=changed, headers=H).status_code == 409
        )
        assert len(writes) == 1
        # Provider conversion to an all-day event is outside this timed-only edit scope.
        timed = copy.deepcopy(external)
        if provider == "google":
            external["start"] = {"date": "2026-09-20"}
            external["end"] = {"date": "2026-09-21"}
        else:
            external["isAllDay"] = True
            external["start"] = {"dateTime": "2026-09-20T00:00:00", "timeZone": "UTC"}
            external["end"] = {"dateTime": "2026-09-21T00:00:00", "timeZone": "UTC"}
        assert (
            "All-day"
            in c.get("/api/calendar/actions/" + creation + "/event").json()[
                "editBlocked"
            ]
        )
        all_day_change = {**change, "requestId": str(uuid4()), "expectedEtag": '"v2"'}
        assert (
            c.post(
                "/api/calendar/changes/preview", json=all_day_change, headers=H
            ).status_code
            == 409
        )
        assert (
            c.post("/api/calendar/changes", json=all_day_change, headers=H).status_code
            == 409
        )
        assert len(writes) == 1
        external.clear()
        external.update(timed)
        # A race after the pre-read must be caught by the actual conditional mutation.
        reject[0] = True
        racing = {
            **change,
            "requestId": str(uuid4()),
            "expectedEtag": '"v2"',
            "edit": {**change["edit"], "title": "Concurrent edit"},
        }
        assert (
            c.post("/api/calendar/changes", json=racing, headers=H).status_code == 409
        )
        assert (
            c.post("/api/calendar/changes", json=racing, headers=H).status_code == 409
        )
        assert len(writes) == 2
        assert (
            external["summary" if provider == "google" else "subject"] == "Later focus"
        )
        # An external invitation changes this into a meeting; never edit/remove it silently.
        external["attendees"] = [{"email": "someone@example.com"}]
        delete = {
            "requestId": str(uuid4()),
            "creationId": creation,
            "operation": "delete",
            "expectedEtag": '"v3"',
        }
        assert (
            c.post("/api/calendar/changes", json=delete, headers=H).status_code == 409
        )
        assert len(writes) == 2
        external["attendees"] = []
        assert (
            c.post("/api/calendar/changes", json=delete, headers=H).status_code == 502
        )
        recovered = c.post("/api/calendar/changes", json=delete, headers=H)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["deleted"] is True
        assert len(writes) == 3
        assert (
            c.get("/api/calendar/actions/" + creation + "/event").json()["deleted"]
            is True
        )
        assert (
            len(c.get("/api/calendar/actions/" + creation + "/changes").json()["items"])
            == 3
        )
