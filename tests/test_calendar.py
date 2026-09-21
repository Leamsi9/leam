from urllib.parse import parse_qs

import httpx2
import pytest
from fastapi.testclient import TestClient
from test_accounts import H, connect, provider_transport
from test_api import FakeCodex, login

from leam_api.app import create_app


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_calendar_sync_collects_pages_and_does_not_replace_snapshot_after_failure(
    tmp_path, provider
):
    calls = []
    auth = provider_transport(provider, [])
    failing = [False]

    def handle(request):
        if (
            request.url.path.endswith("/token")
            or "/userinfo" in request.url.path
            or request.url.path == "/v1.0/me"
        ):
            return auth.handler(request)
        calls.append(request)
        query = parse_qs(request.url.query.decode())
        if (
            "calendarList" in request.url.path
            or request.url.path == "/v1.0/me/calendars"
        ):
            if provider == "google":
                return httpx2.Response(
                    200,
                    json={
                        "items": [
                            {
                                "id": "primary@example.com",
                                "summary": "Personal",
                                "accessRole": "owner",
                                "timeZone": "Europe/London",
                            }
                        ]
                    },
                )
            return httpx2.Response(
                200,
                json={
                    "value": [{"id": "calendar-1", "name": "Personal", "canEdit": True}]
                },
            )
        if failing[0]:
            return httpx2.Response(503, json={"error": "not available"})
        if provider == "google":
            second = "pageToken" in query
            return httpx2.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "second" if second else "first",
                            "summary": "Second" if second else "First",
                            "start": {"dateTime": "2026-09-20T10:00:00+01:00"},
                            "end": {"dateTime": "2026-09-20T10:30:00+01:00"},
                        },
                        {
                            "id": "all-day",
                            "summary": "Holiday",
                            "start": {"date": "2026-09-20"},
                            "end": {"date": "2026-09-22"},
                        },
                    ],
                    **({} if second else {"nextPageToken": "page-two"}),
                },
            )
        second = "$skiptoken" in query
        return httpx2.Response(
            200,
            json={
                "value": [
                    {
                        "id": "second" if second else "first",
                        "subject": "Second" if second else "First",
                        "start": {
                            "dateTime": "2026-09-20T09:00:00.0000000",
                            "timeZone": "UTC",
                        },
                        "end": {
                            "dateTime": "2026-09-20T09:30:00.0000000",
                            "timeZone": "UTC",
                        },
                        "isAllDay": False,
                    },
                    {
                        "id": "all-day",
                        "subject": "Holiday",
                        "isAllDay": True,
                        "start": {
                            "dateTime": "2026-09-20T00:00:00.0000000",
                            "timeZone": "GMT Standard Time",
                        },
                        "end": {
                            "dateTime": "2026-09-22T00:00:00.0000000",
                            "timeZone": "GMT Standard Time",
                        },
                    },
                ],
                **(
                    {}
                    if second
                    else {
                        "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/calendars/calendar-1/calendarView?$skiptoken=page-two"
                    }
                ),
            },
        )

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
        listed = c.post(
            "/api/calendar/accounts/" + account["id"] + "/sync", json={}, headers=H
        )
        assert listed.status_code == 200, listed.text
        calendar = listed.json()["items"][0]
        body = {
            "start": "2026-09-20T00:00:00+00:00",
            "end": "2026-09-27T00:00:00+00:00",
        }
        synced = c.post(
            "/api/calendar/" + calendar["id"] + "/sync", json=body, headers=H
        )
        assert synced.status_code == 200, synced.text
        assert {e["title"] for e in synced.json()["events"]} == {
            "First",
            "Second",
            "Holiday",
        }
        holiday = next(e for e in synced.json()["events"] if e["id"] == "all-day")
        assert (holiday["start"], holiday["end"], holiday["allDay"]) == (
            "2026-09-20",
            "2026-09-22",
            True,
        )
        before = synced.json()["syncedAt"]
        failing[0] = True
        assert (
            c.post(
                "/api/calendar/" + calendar["id"] + "/sync", json=body, headers=H
            ).status_code
            == 502
        )
        cached = c.get("/api/calendar/" + calendar["id"]).json()
        assert cached["syncedAt"] == before
        assert len(cached["events"]) == 3
        assert cached["error"]
        assert c.delete("/api/accounts/" + account["id"], headers=H).status_code == 200
        assert c.get("/api/calendar/" + calendar["id"]).status_code == 404
        assert c.get("/api/calendar").json()["items"] == []


def test_microsoft_pagination_cannot_exfiltrate_credentials_or_publish_partial_data(
    tmp_path,
):
    auth = provider_transport("microsoft", [])
    calls = []

    def handle(request):
        if request.url.path.endswith("/token") or request.url.path == "/v1.0/me":
            return auth.handler(request)
        calls.append(str(request.url))
        return httpx2.Response(
            200,
            json={
                "value": [{"id": "calendar-1", "name": "Personal", "canEdit": True}],
                "@odata.nextLink": "https://evil.example/collect",
            },
        )

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        account_transport=httpx2.MockTransport(handle),
    )
    with TestClient(app) as c:
        login(c)
        account = connect(c, "microsoft")
        result = c.post(
            "/api/calendar/accounts/" + account["id"] + "/sync", json={}, headers=H
        )
        assert result.status_code == 502
        assert len(calls) == 1
        listing = c.get("/api/calendar").json()
        assert listing["items"] == []
        assert listing["accounts"][0]["error"]
        assert listing["accounts"][0]["calendarsListedAt"] is None
