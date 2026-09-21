"""Provider-returned point events must not invalidate the daily snapshot."""

import httpx2
import pytest
from fastapi.testclient import TestClient
from test_accounts import H, connect, provider_transport
from test_api import FakeCodex, login

from leam_api.app import create_app


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_sync_keeps_timed_instant_but_rejects_invalid_ranges(tmp_path, provider):
    auth = provider_transport(provider, [])
    mode = ["instant"]

    def handle(request):
        if (
            request.url.path.endswith("/token")
            or "/userinfo" in request.url.path
            or request.url.path == "/v1.0/me"
        ):
            return auth.handler(request)
        if (
            "calendarList" in request.url.path
            or request.url.path == "/v1.0/me/calendars"
        ):
            return httpx2.Response(
                200,
                json={
                    "items" if provider == "google" else "value": [
                        {
                            "id": "fixture-calendar",
                            "summary": "Fixture",
                            "name": "Fixture",
                            "accessRole": "owner",
                            "canEdit": True,
                        }
                    ]
                },
            )
        start = "2026-09-21T10:00:00+00:00"
        end = "2026-09-21T09:00:00+00:00" if mode[0] == "reversed" else start
        all_day = mode[0] == "empty-all-day"
        if provider == "google":
            event = {
                "id": "fixture-event",
                "summary": "Fixture point event",
                "start": {"date": "2026-09-21"} if all_day else {"dateTime": start},
                "end": {"date": "2026-09-21"} if all_day else {"dateTime": end},
            }
        else:
            event = {
                "id": "fixture-event",
                "subject": "Fixture point event",
                "isAllDay": all_day,
                "start": {"dateTime": start, "timeZone": "UTC"},
                "end": {"dateTime": end, "timeZone": "UTC"},
            }
        return httpx2.Response(
            200, json={"items" if provider == "google" else "value": [event]}
        )

    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
        account_transport=httpx2.MockTransport(handle),
    )
    with TestClient(app) as client:
        login(client)
        account = connect(client, provider)
        listed = client.post(
            f"/api/calendar/accounts/{account['id']}/sync", json={}, headers=H
        )
        assert listed.status_code == 200
        calendar = listed.json()["items"][0]["id"]
        url = f"/api/calendar/{calendar}/sync"
        window = {"start": "2026-09-21T00:00:00Z", "end": "2026-09-22T00:00:00Z"}
        result = client.post(url, json=window, headers=H)
        assert result.status_code == 200, result.text
        saved = result.json()
        assert len(saved["events"]) == 1
        event = saved["events"][0]
        assert event["start"] == event["end"] == "2026-09-21T10:00:00+00:00"
        assert event["allDay"] is False
        for invalid in ["reversed", "empty-all-day"]:
            mode[0] = invalid
            assert client.post(url, json=window, headers=H).status_code == 502
            current = client.get(f"/api/calendar/{calendar}").json()
            assert current["events"] == saved["events"]
            assert current["syncedAt"] == saved["syncedAt"]
            assert current["error"]
