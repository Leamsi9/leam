import json

import httpx2
import pytest
from fastapi.testclient import TestClient
from test_accounts import H, connect, provider_transport
from test_api import FakeCodex, login

from leam_api.app import create_app


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_calendar_create_recovers_lost_response_without_duplicate_or_changed_payload(
    tmp_path, provider
):
    auth = provider_transport(provider, [])
    events = {}
    posts = []
    lose = [True]

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
            posts.append(payload)
            key = payload.get("id") or payload["transactionId"]
            if key in events:
                return httpx2.Response(
                    409 if provider == "google" else 201, json=events[key]
                )
            events[key] = {**payload, "id": key, "etag": '"event-v1"'}
            if lose[0]:
                lose[0] = False
                raise httpx2.ReadTimeout("provider committed; response lost")
            return httpx2.Response(201, json=events[key])
        return httpx2.Response(200, json=events[path.rsplit("/", 1)[-1]])

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        account_transport=httpx2.MockTransport(handle),
    )
    with TestClient(app) as c:
        login(c)
        account = connect(c, provider)
        calendar = c.post(
            "/api/calendar/accounts/" + account["id"] + "/sync", json={}, headers=H
        ).json()["items"][0]
        commitment = c.post(
            "/api/commitments", json={"title": "Work on Leam"}, headers=H
        ).json()
        schedule = {
            "calendarId": calendar["id"],
            "commitmentId": commitment["id"],
            "commitmentRevision": 1,
            "date": "2026-09-20",
            "time": "10:00",
            "timezone": "Europe/London",
            "minutes": 30,
            "fold": 0,
        }
        preview = c.post("/api/calendar/actions/preview", json=schedule, headers=H)
        assert preview.status_code == 200, preview.text
        body = {
            "requestId": "4acbfc58-44be-411e-950c-b11caa8b34b2",
            "schedule": schedule,
            "previewDigest": preview.json()["digest"],
        }
        assert c.post("/api/calendar/actions", json=body, headers=H).status_code == 502
        assert (
            c.patch(
                "/api/commitments/" + commitment["id"],
                json={"revision": 1, "title": "Updated afterwards"},
                headers=H,
            ).status_code
            == 200
        )
        assert c.delete("/api/accounts/" + account["id"], headers=H).status_code == 200
        missing = c.post("/api/calendar/actions", json=body, headers=H)
        assert missing.status_code == 404
        assert missing.headers.get("X-Leam-Action-Reserved") is None
        assert len(posts) == 1
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
        saved = c.get("/api/calendar/actions").json()["items"][0]
        assert saved["request"] == body
        account = connect(c, provider)
        assert (
            c.post(
                "/api/calendar/accounts/" + account["id"] + "/sync", json={}, headers=H
            ).json()["items"][0]["id"]
            == calendar["id"]
        )
        retried = c.post("/api/calendar/actions", json=body, headers=H)
        assert retried.status_code == 200, retried.text
        assert retried.json()["event"]["title"] == "Work on Leam"
        assert len(events) == 1
        assert posts[0] == posts[1]
        assert "attendees" not in posts[0]
        assert (
            c.post("/api/calendar/actions", json=body, headers=H).json()
            == retried.json()
        )
        assert len(posts) == 2
        assert c.delete("/api/accounts/" + account["id"], headers=H).status_code == 200
        assert (
            c.post("/api/calendar/actions", json=body, headers=H).json()
            == retried.json()
        )
        assert len(posts) == 2
        changed = {**body, "schedule": {**schedule, "minutes": 45}}
        assert (
            c.post("/api/calendar/actions", json=changed, headers=H).status_code == 409
        )


def test_calendar_preview_checks_dst_revision_and_write_access_before_external_effect(
    tmp_path,
):
    from datetime import datetime

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    with TestClient(app) as c:
        login(c)
        with app.state.store.connect() as db:
            db.execute(
                "INSERT INTO accounts VALUES ('a','google','test@example.com','unused','connected',NULL,NULL,0,'subject')"
            )
            db.execute(
                "INSERT INTO calendars VALUES ('c','a','primary','Personal','Europe/London',1,0)"
            )
        item = c.post("/api/commitments", json={"title": "Focus"}, headers=H).json()
        schedule = {
            "calendarId": "c",
            "commitmentId": item["id"],
            "commitmentRevision": 1,
            "date": "2026-03-29",
            "time": "01:30",
            "timezone": "Europe/London",
            "minutes": 30,
            "fold": 0,
        }
        gap = c.post("/api/calendar/actions/preview", json=schedule, headers=H)
        assert gap.status_code == 422
        assert "does not exist" in gap.text
        first = c.post(
            "/api/calendar/actions/preview",
            json={**schedule, "date": "2026-10-25"},
            headers=H,
        ).json()
        second = c.post(
            "/api/calendar/actions/preview",
            json={**schedule, "date": "2026-10-25", "fold": 1},
            headers=H,
        ).json()
        assert (
            datetime.fromisoformat(second["start"])
            - datetime.fromisoformat(first["start"])
        ).total_seconds() == 3600
        assert second["digest"] != first["digest"]
        assert (
            c.post(
                "/api/calendar/actions/preview",
                json={**schedule, "timezone": "Unknown/Zone"},
                headers=H,
            ).status_code
            == 422
        )
        # Renaming the reviewed calendar invalidates its digest, before any reservation/network call.
        with app.state.store.connect() as db:
            db.execute("UPDATE calendars SET name='Renamed' WHERE id='c'")
        body = {
            "requestId": "d78aa5b5-6c3f-40bb-a6b7-39b0d08a3505",
            "schedule": first["schedule"],
            "previewDigest": first["digest"],
        }
        stale = c.post("/api/calendar/actions", json=body, headers=H)
        assert stale.status_code == 409
        assert stale.headers["X-Leam-Action-Reserved"] == "no"
        assert c.get("/api/calendar/actions").json()["items"] == []
        assert (
            c.patch(
                "/api/commitments/" + item["id"],
                json={"revision": 1, "title": "Changed"},
                headers=H,
            ).status_code
            == 200
        )
        assert (
            c.post(
                "/api/calendar/actions/preview", json=first["schedule"], headers=H
            ).status_code
            == 409
        )
        with app.state.store.connect() as db:
            db.execute("UPDATE calendars SET can_write=0 WHERE id='c'")
        assert (
            c.post(
                "/api/calendar/actions/preview",
                json={**first["schedule"], "commitmentRevision": 2},
                headers=H,
            ).status_code
            == 409
        )


def test_calendar_action_history_pages_all_pending_actions(tmp_path):
    from uuid import uuid4

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    with TestClient(app) as c:
        login(c)
        with app.state.store.connect() as db:
            db.executemany(
                "INSERT INTO calendar_actions VALUES (?,?,?,?,?,?,NULL,NULL,?)",
                [(str(uuid4()), "hash", "c", "{}", "{}", "{}", i) for i in range(205)],
            )
        ids = []
        offset = 0
        while offset is not None:
            page = c.get(
                "/api/calendar/actions", params={"calendarId": "c", "offset": offset}
            ).json()
            ids.extend(a["requestId"] for a in page["items"])
            offset = page["nextOffset"]
        assert len(set(ids)) == 205
        assert c.get("/api/calendar/actions?calendarId=other").json()["items"] == []
