import json
import time
from datetime import UTC, datetime, timedelta

import httpx2
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.backlog import Backlog, Assessment
from leam_api.updates import Publication, QA

H = {"origin": "http://testserver"}


def make(tmp_path):
    return create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )


def seed(app, client):
    routine = client.post(
        "/api/routines",
        headers=H,
        json={
            "title": "Evening pause",
            "message": "Review your day",
            "time": "23:59",
            "timezone": "UTC",
            "days": [0, 1, 2, 3, 4, 5, 6],
        },
    ).json()
    assert "id" in routine
    client.post(
        "/api/routines/" + routine["id"] + "/run",
        headers=H,
        json={"revision": 1, "requestId": "00000000-0000-4000-8000-000000000001"},
    )
    rule = client.post(
        "/api/routine-event-rules",
        headers=H,
        json={
            "title": "Arrived",
            "message": "Take a breath",
            "source": "phone",
            "eventType": "arrival",
            "match": {"field": "location", "equals": "home"},
        },
    ).json()
    store = app.state.store
    store.create(
        "routine_event",
        {"attributes": {"private": "raw-secret-value"}, "payload": "never publish raw"},
    )
    update = app.state.updates.publish(
        Publication(
            feature="routine-fixture",
            title="Time routines",
            summary="Fixture build progress",
            deploymentId="fixture-deployment",
            deployedAt=datetime.now(UTC) - timedelta(minutes=10),
        )
    )
    app.state.updates.qa(
        update["id"],
        QA(
            deploymentId="fixture-deployment",
            state="passed",
            details="source and caller pass",
        ),
    )
    Backlog(store).upsert(
        Assessment(
            feature="unfinished-fixture",
            title="Unfinished fixture",
            currentStep="Testing remaining behavior",
            percent=64,
            assessedAt=datetime.now(UTC) - timedelta(minutes=30),
        )
    )
    return routine, rule


def test_context_api_is_shared_sourced_bounded_and_read_only(tmp_path):
    app = make(tmp_path)
    with TestClient(app, client=("127.0.0.1", 1234)) as client:
        assert client.get("/api/companion/overview").status_code == 401
        login(client)
        routine, rule = seed(app, client)
        response = client.get("/api/companion/context?kind=routine")
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["id"] == routine["id"] and item["source"].startswith("leam:")
        assert (
            item["observedAt"] >= item["dataAsOf"]
            and item["availability"] == "available"
        )
        rule_data = client.get("/api/companion/context?kind=event_rule").json()
        assert rule_data["items"][0]["hasCondition"] is True
        assert rule_data["items"][0]["match"] == {"field": "location", "equals": "home"}
        assert "raw-secret-value" not in json.dumps(rule_data)
        overview = client.get("/api/companion/overview").json()
        assert overview["project"]["name"] == "Leam"
        assert overview["project"]["deployedFeatures"] == 1
        assert overview["project"]["userAcceptedFeatures"] == 0
        assert overview["project"]["unfinishedFeatures"] == 1
        assert overview["project"]["staleAssessments"] == 1
        assert overview["project"]["estimateBasis"] == "deployment"
        assert "raw-secret-value" not in json.dumps(
            overview
        ) and "never publish raw" not in json.dumps(overview)
        bearer = {
            "authorization": "Bearer " + (tmp_path / "tools-token").read_text().strip()
        }
        result = client.post(
            "/api/internal/tools",
            headers=bearer,
            json={"tool": "leam_context", "arguments": {"kind": "routine"}},
        ).json()
        assert result["items"][0]["source"] == item["source"]
        assert result["items"][0]["dataAsOf"] == item["dataAsOf"]
        assert client.get("/api/routines").json()["items"][0]["revision"] == 1
        assert client.get("/api/companion/context?kind=event").status_code == 422


def test_empty_and_missing_sources_do_not_invent_project_or_freshness(tmp_path):
    app = make(tmp_path)
    with TestClient(app) as client:
        login(client)
        overview = client.get("/api/companion/overview").json()
        assert overview["project"] is None
        assert all(
            m["dataAsOf"] is None and m["availability"] == "empty"
            for m in overview["modules"]
        )
        with app.state.store.connect() as db:
            db.execute("DROP TABLE routine_runs")
        missing = client.get("/api/companion/context?kind=notification").json()
        assert (
            missing["items"] == []
            and missing["modules"][0]["availability"] == "unavailable"
        )
        assert missing["modules"][0]["dataAsOf"] is None
        assert not missing["totalIsExact"]
        assert "routine_runs" not in json.dumps(missing)


def test_search_pages_exclude_hidden_event_attributes_and_reads_do_not_write(tmp_path):
    app = make(tmp_path)
    with TestClient(app) as client:
        login(client)
        seed(app, client)
        for number in range(3):
            client.post(
                "/api/routine-event-rules",
                headers=H,
                json={
                    "title": f"Rule fixture {number}",
                    "message": "Reminder",
                    "source": "fixture",
                    "eventType": "ready",
                },
            )
        before = client.get("/api/routine-event-rules").json()
        first = client.get(
            "/api/companion/context?kind=event_rule&query=fixture&limit=2"
        ).json()
        second = client.get(
            "/api/companion/context?kind=event_rule&query=fixture&limit=2&offset=2"
        ).json()
        assert (
            len(first["items"]) == 2
            and first["nextOffset"] == 2
            and len(second["items"]) == 1
        )
        assert not {r["id"] for r in first["items"]} & {
            r["id"] for r in second["items"]
        }
        assert (
            client.get("/api/companion/context?query=raw-secret-value").json()["items"]
            == []
        )
        assert client.get("/api/routine-event-rules").json() == before
        notes = client.get("/api/companion/context?kind=notification").json()
        assert (
            notes["items"][0]["notificationKind"] == "routine"
            and notes["items"][0]["state"] == "ready"
        )
        assert notes["items"][0]["dataAsOf"] is not None


def test_existing_record_search_and_pagination_reach_beyond_500(tmp_path):
    app = make(tmp_path)
    with TestClient(app) as client:
        login(client)
        with app.state.store.connect() as db:
            db.executemany(
                "INSERT INTO entities VALUES (?,?,?,?,?)",
                [
                    (
                        f"memory-{n:04}",
                        "memory",
                        1,
                        json.dumps(
                            {
                                "text": "last record" if n == 500 else "fixture",
                                "source": "User",
                            }
                        ),
                        time.time(),
                    )
                    for n in range(501)
                ],
            )
        result = client.get(
            "/api/companion/context?kind=memory&limit=1&offset=500"
        ).json()
        assert result["items"][0]["id"] == "memory-0500"
        assert (
            result["total"] == 501
            and result["totalIsExact"]
            and not result["truncated"]
        )
        match = client.get(
            "/api/companion/context?kind=memory&query=last%20record"
        ).json()
        assert match["total"] == 1 and match["items"][0]["id"] == "memory-0500"


def test_new_domain_limit_and_partial_calendar_are_honest(tmp_path):
    from leam_api.context_reader import ContextReader
    from leam_api.domain_tools import ContextQuery

    app = make(tmp_path)
    with TestClient(app) as client:
        login(client)
        with app.state.store.connect() as db:
            db.executemany(
                "INSERT INTO entities VALUES (?,?,?,?,?)",
                [
                    (
                        f"rule-{n:04}",
                        "routine_event_rule",
                        1,
                        json.dumps(
                            {"title": "fixture", "source": "test", "eventType": "test"}
                        ),
                        time.time(),
                    )
                    for n in range(501)
                ],
            )
        result = client.get("/api/companion/context?kind=event_rule&limit=1").json()
        assert result["truncated"] and not result["totalIsExact"]
        assert result["modules"][0]["availability"] == "partial"

        class Calendars:
            def list(self):
                return {
                    "items": [
                        {"id": "fresh", "syncedAt": time.time()},
                        {"id": "unknown", "syncedAt": None},
                    ]
                }

        result = ContextReader(app.state.store, Calendars()).query(
            ContextQuery(kind="calendar")
        )
        assert result["modules"][0]["availability"] == "partial"


def test_project_backlog_cap_propagates_partial_counts(tmp_path):
    app = make(tmp_path)
    with TestClient(app) as client:
        login(client)
        with app.state.store.connect() as db:
            db.executemany(
                "INSERT INTO backlog_assessments VALUES (?,?,?,?)",
                [
                    (
                        f"item-{n:04}",
                        1,
                        time.time(),
                        json.dumps(
                            {
                                "feature": f"item-{n:04}",
                                "title": "Fixture",
                                "percent": 25,
                                "assessedAt": datetime.now(UTC).isoformat(),
                            }
                        ),
                    )
                    for n in range(501)
                ],
            )
        result = client.get("/api/companion/context?kind=project").json()
        assert (
            result["project"]["countsTruncated"]
            and result["project"]["backlogTotal"] == 501
        )
        assert result["project"]["availability"] == "partial"
        assert (
            result["modules"][0]["truncated"]
            and result["modules"][0]["availability"] == "partial"
        )
        assert result["truncated"] and not result["totalIsExact"]


def test_actual_mcp_discovery_and_new_kind_caller_share_domain_reader(tmp_path):
    from leam_api.mcp_server import create_mcp_app

    app = make(tmp_path)
    token = (tmp_path / "tools-token").read_text().strip()
    server = create_mcp_app(
        "http://127.0.0.1:46400",
        token,
        transport=httpx2.ASGITransport(app=app, client=("127.0.0.1", 1234)),
    )
    with (
        TestClient(app) as browser,
        TestClient(
            server, base_url="https://127.0.0.1:46420", client=("127.0.0.1", 1234)
        ) as mcp,
    ):
        login(browser)
        routine, _ = seed(app, browser)
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
        }
        listed = mcp.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        ).json()
        tool = next(t for t in listed["result"]["tools"] if t["name"] == "leam_context")
        assert {"routine", "event_rule", "notification", "project"} <= set(
            tool["inputSchema"]["properties"]["kind"]["enum"]
        )
        result = mcp.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "leam_context",
                    "arguments": {
                        "kind": "routine",
                        "query": "Evening",
                        "offset": 0,
                        "limit": 1,
                    },
                },
            },
        )
        assert result.status_code == 200 and not result.json()["result"].get("isError")
        payload = result.json()["result"].get("structuredContent") or json.loads(
            result.json()["result"]["content"][0]["text"]
        )
        assert payload["items"][0]["id"] == routine["id"] and payload["items"][0][
            "source"
        ].startswith("leam:routine/")
        assert payload["items"][0]["dataAsOf"] is not None
