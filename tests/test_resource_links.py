"""Authenticated resource association callers; canonical data and exact retries."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from test_api import login
from test_artifacts import client_for
from test_backlog import assessment

from leam_api.artifacts import Artifacts
from leam_api.backlog import Backlog
from leam_api.backups import Backups, restore
from leam_api.resource_links import Mutation, ResourceLinks, Target
from leam_api.store import Store
from leam_api.updates import QA, Acceptance, Publication, Updates

H = {"origin": "http://testserver"}


def prepare(path):
    store = Store(path)
    Artifacts(store).publish("report", "Report", "markdown", "# Generated")
    task = store.create("commitment", {"title": "Canonical task", "status": "active"})
    return store, task


def mutation(target, revision=0, operation="link", **changes):
    return {
        "requestId": str(uuid4()),
        "revision": revision,
        "operation": operation,
        "targetType": "commitment",
        "targetId": target["id"],
        **changes,
    }


def test_links_require_auth_origin_and_resolve_both_directions_without_copying(
    tmp_path,
):
    store, task = prepare(tmp_path)
    with client_for(tmp_path) as client:
        assert client.get("/api/artifacts/report/links").status_code == 401
        assert (
            client.get(
                "/api/resource-links",
                params={"targetType": "commitment", "targetId": task["id"]},
            ).status_code
            == 401
        )
        login(client)
        body = mutation(task)
        assert client.post("/api/artifacts/report/links", json=body).status_code == 403
        response = client.post("/api/artifacts/report/links", json=body, headers=H)
        assert response.status_code == 200 and response.json()["revision"] == 1
        row = client.get("/api/artifacts/report/links").json()["items"][0]
        assert row["title"] == task["title"] and row["available"]
        reverse = client.get(
            "/api/resource-links",
            params={"targetType": "commitment", "targetId": task["id"]},
        )
        assert reverse.status_code == 200
        assert reverse.json()["items"][0]["id"] == "report"
        assert reverse.headers["cache-control"] == "no-store"
        updated = store.update(
            task["id"], "commitment", task["revision"], {"title": "Renamed canonically"}
        )
        row = client.get("/api/artifacts/report/links").json()["items"][0]
        assert (
            row["title"] == updated["title"] and row["revision"] == updated["revision"]
        )
        assert "Canonical task" not in str(store.get("resource-links:report"))
        assert "Renamed" not in str(store.get("resource-links:report"))


def test_exact_old_retry_does_not_relink_after_unlink_and_conflicts_do_not_mutate(
    tmp_path,
):
    store, task = prepare(tmp_path)
    service = ResourceLinks(store)
    first = Mutation(**mutation(task))
    receipt = service.mutate("report", first)
    unlink = Mutation(**mutation(task, revision=1, operation="unlink"))
    assert service.mutate("report", unlink)["revision"] == 2
    assert service.mutate("report", first) == receipt
    assert service.get("report")["items"] == []
    with pytest.raises(HTTPException) as error:
        service.mutate(
            "report", Mutation(**{**first.model_dump(), "operation": "unlink"})
        )
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        service.mutate("report", Mutation(**mutation(task)))
    assert error.value.status_code == 409
    assert store.entities("commitment")[0]["id"] == task["id"]


def test_concurrent_revision_guards_and_per_resource_limit(tmp_path, monkeypatch):
    from leam_api import resource_links

    store, task = prepare(tmp_path)
    other = store.create("commitment", {"title": "Other"})
    service = ResourceLinks(store)

    def link(row):
        try:
            return service.mutate("report", Mutation(**mutation(row)))
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(link, [task, other]))
    assert len([r for r in results if r == 409]) == 1
    linked = service.get("report")["items"][0]["targetId"]
    monkeypatch.setattr(resource_links, "MAX_PER_RESOURCE", 1)
    remaining = other if linked == task["id"] else task
    with pytest.raises(HTTPException) as error:
        service.mutate("report", Mutation(**mutation(remaining, revision=1)))
    assert error.value.status_code == 413
    assert len(service.get("report")["items"]) == 1


def test_feature_link_tracks_backlog_to_latest_deployment_and_completed_uat(tmp_path):
    store, _ = prepare(tmp_path)
    Backlog(store).upsert(assessment())
    service, updates = ResourceLinks(store), Updates(store)
    body = Mutation(**mutation({"id": "recovery-controls"}, targetType="feature"))
    service.mutate("report", body)
    assert service.get("report")["items"][0]["location"] == "backlog"
    now = datetime.now(UTC)
    published = updates.publish(
        Publication(
            feature="recovery-controls",
            title="Now deployed",
            summary="Fixture",
            deploymentId="one",
            deployedAt=now,
        )
    )
    row = service.get("report")["items"][0]
    assert row["location"] == "updates" and row["updateId"] == published["id"]
    qa = updates.qa(published["id"], QA(deploymentId="one", state="passed"))
    updates.uat(
        published["id"],
        Acceptance(deploymentId="one", revision=qa["revision"], state="passed"),
    )
    assert service.get("report")["items"][0]["completed"] is True
    next_version = updates.publish(
        Publication(
            feature="recovery-controls",
            title="New deployment",
            summary="Fixture",
            deploymentId="two",
            deployedAt=now + timedelta(seconds=1),
        )
    )
    row = service.get("report")["items"][0]
    assert row["updateId"] == next_version["id"] and not row["completed"]
    assert service.get("report")["revision"] == 1


def test_deleted_task_and_declined_proposal_are_minimal_unavailable_and_unlinkable(
    tmp_path,
):
    store, task = prepare(tmp_path)
    with client_for(tmp_path) as client:
        login(client)
        proposed = client.post(
            "/api/proposals",
            headers=H,
            json={
                "requestId": str(uuid4()),
                "threadId": "fixture",
                "operation": "coding.handoff",
                "input": {"title": "Sensitive title", "instructions": "Synthetic task"},
                "reason": "Fixture",
            },
        ).json()
        assert (
            client.post(
                "/api/artifacts/report/links",
                headers=H,
                json=mutation(proposed, targetType="proposal"),
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/proposals/{proposed['id']}/decline", headers=H, json={}
            ).status_code
            == 200
        )
        row = client.get("/api/artifacts/report/links").json()["items"][0]
        assert row["available"] is False and row["title"] is None
        assert "Sensitive" not in str(row)
        assert (
            client.post(
                "/api/artifacts/report/links",
                headers=H,
                json=mutation(
                    proposed, revision=1, operation="unlink", targetType="proposal"
                ),
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/artifacts/report/links",
                headers=H,
                json=mutation(task, revision=2),
            ).status_code
            == 200
        )
        store.delete(task["id"], "commitment", task["revision"])
        assert (
            client.get("/api/artifacts/report/links").json()["items"][0]["available"]
            is False
        )
        assert (
            client.post(
                "/api/artifacts/report/links",
                headers=H,
                json=mutation(task, revision=3, operation="unlink"),
            ).status_code
            == 200
        )


def test_target_search_pagination_reverse_pages_and_invalid_or_missing_ids(tmp_path):
    store, task = prepare(tmp_path)
    service = ResourceLinks(store)
    service.mutate("report", Mutation(**mutation(task)))
    Artifacts(store).publish("another", "Another", "text", "text")
    service.mutate("another", Mutation(**mutation(task)))
    with client_for(tmp_path) as client:
        login(client)
        options = client.get(
            "/api/resource-links/targets",
            params={"type": "commitment", "q": "CANONICAL"},
        ).json()
        assert options["items"][0]["targetId"] == task["id"]
        page = client.get(
            "/api/resource-links",
            params={"targetType": "commitment", "targetId": task["id"], "limit": 1},
        ).json()
        assert page["items"][0]["id"] == "another" and page["total"] == 2
        last = client.get(
            "/api/resource-links",
            params={
                "targetType": "commitment",
                "targetId": task["id"],
                "limit": 1,
                "cursor": page["nextCursor"],
            },
        ).json()
        assert last["items"][0]["id"] == "report" and last["nextCursor"] is None
        assert (
            client.get(
                "/api/resource-links",
                params={"targetType": "commitment", "targetId": "../bad"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/artifacts/report/links",
                headers=H,
                json=mutation({"id": str(uuid4())}, revision=1),
            ).status_code
            == 404
        )
        assert client.get("/api/artifacts/missing/links").status_code == 404


def test_resource_associations_survive_backup_without_cross_installation_leak(tmp_path):
    from leam_api.vault import Vault

    store, task = prepare(tmp_path / "source")
    Vault(store.path.parent)
    service = ResourceLinks(store)
    body = Mutation(**mutation(task))
    receipt = service.mutate("report", body)
    backup = Backups(store)
    archive = backup.path(backup.create()["id"])
    target = tmp_path / "restored"
    restore(archive, target, store.path.parent)
    restored = ResourceLinks(Store(target))
    assert restored.get("report") == service.get("report")
    assert restored.mutate("report", body) == receipt
    other = ResourceLinks(Store(tmp_path / "other"))
    assert (
        other.reverse(Target(targetType="commitment", targetId=task["id"]))["items"]
        == []
    )


def test_pending_coding_approval_link_survives_accepted_dispatch(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_coding_handoff import propose, review, setup

    app, _bridge = setup(tmp_path, monkeypatch)
    Artifacts(app.state.store).publish("report", "Report", "text", "Fixture")
    with TestClient(app) as client:
        login(client)
        item = propose(client)
        result = client.post(
            "/api/artifacts/report/links",
            headers=H,
            json=mutation(item, targetType="proposal"),
        )
        assert result.status_code == 200
        ready = review(client, item["id"], "Explicit fixture task")
        response = client.post(
            f"/api/coding/handoffs/{item['id']}/start",
            headers=H,
            json={"previewToken": ready["previewToken"], "confirmed": True},
        )
        assert response.status_code == 200 and response.json()["state"] == "accepted"
        row = client.get("/api/artifacts/report/links").json()["items"][0]
        assert row["targetId"] == item["id"] and row["state"] == "complete"
        assert row["available"] and row["location"] == "approvals"


def test_total_relation_quota_and_target_selector_pages(tmp_path, monkeypatch):
    from leam_api import resource_links

    store, task = prepare(tmp_path)
    service = ResourceLinks(store)
    monkeypatch.setattr(resource_links, "MAX_LINKS", 1)
    service.mutate("report", Mutation(**mutation(task)))
    Artifacts(store).publish("second", "Second", "text", "Fixture")
    with pytest.raises(HTTPException) as error:
        service.mutate("second", Mutation(**mutation(task)))
    assert error.value.status_code == 413
    other = store.create("commitment", {"title": "Other"})
    first = service.targets("commitment", limit=1)
    last = service.targets("commitment", limit=1, cursor=first["nextCursor"])
    assert {first["items"][0]["targetId"], last["items"][0]["targetId"]} == {
        task["id"],
        other["id"],
    }
    assert last["nextCursor"] is None


def test_direct_canonical_records_beyond_first_page_are_authenticated_and_read_only(
    tmp_path,
):
    store, _task = prepare(tmp_path)
    updates = Updates(store)
    now = datetime.now(UTC)
    first_update = None
    for n in range(52):
        saved = updates.publish(
            Publication(
                feature=f"feature-{n}",
                title=f"Feature {n}",
                summary="Fixture",
                deploymentId=f"version-{n}",
                deployedAt=now,
            )
        )
        first_update = first_update or saved
    with client_for(tmp_path) as client:
        assert client.get(f"/api/updates/{first_update['id']}").status_code == 401
        assert client.get(f"/api/proposals/{uuid4()}").status_code == 401
        login(client)
        first_proposal = None
        for n in range(52):
            response = client.post(
                "/api/proposals",
                headers=H,
                json={
                    "requestId": str(uuid4()),
                    "threadId": "fixture",
                    "operation": "commitment.create",
                    "input": {"title": f"Task {n}"},
                    "reason": "Fixture",
                },
            )
            assert response.status_code == 200
            first_proposal = first_proposal or response.json()
        assert first_update["id"] not in {
            i["id"] for i in client.get("/api/updates").json()["items"]
        }
        assert first_proposal["id"] not in {
            i["id"] for i in client.get("/api/proposals").json()["items"]
        }
        before = client.get("/api/proposals/status").json()
        detail = client.get(f"/api/proposals/{first_proposal['id']}")
        assert (
            detail.status_code == 200
            and detail.json()["fingerprint"] == first_proposal["fingerprint"]
        )
        assert detail.json()["unread"] is True
        assert client.get("/api/proposals/status").json() == before
        detail = client.get(f"/api/updates/{first_update['id']}")
        assert detail.status_code == 200 and detail.json()["id"] == first_update["id"]
        assert detail.headers["cache-control"] == "no-store"
        assert client.get(f"/api/proposals/{uuid4()}").status_code == 404
        assert client.get(f"/api/updates/{uuid4()}").status_code == 404
