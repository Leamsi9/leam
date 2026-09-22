"""Actual authenticated/domain HTTP callers for owner priority, separate from facts."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_backlog import review

from leam_api.backlog import Assessment, Backlog, router
from leam_api.store import Store
from leam_api.updates import Publication, Updates


def setup(tmp_path):
    backlog = Backlog(Store(tmp_path))
    for feature, lane, rank in [
        ("alpha", "ready", 1),
        ("beta", "ready", 2),
        ("gamma", "ready", 3),
        ("blocked", "blocked", 0),
    ]:
        backlog.upsert(
            Assessment(
                feature=feature,
                title=feature,
                currentStep="Await actual delivery",
                percent=20,
                deliveryState=lane,
                rank=rank,
                worker="worker-a",
            )
        )
    app = FastAPI()
    app.include_router(router(backlog))
    return backlog, TestClient(app)


def request(client, features=None, lane="ready"):
    snapshot = client.get("/api/backlog").json()
    return {
        "requestId": str(uuid4()),
        "revision": snapshot["ordering"]["revision"],
        "expectedRevisions": {
            item["feature"]: item["revision"] for item in snapshot["items"]
        },
        "lane": lane,
        "features": features or ["gamma", "alpha", "beta"],
    }


def test_order_persists_without_changing_assessments_and_survives_full_review(tmp_path):
    backlog, client = setup(tmp_path)
    original = {
        item["feature"]: item for item in client.get("/api/backlog").json()["items"]
    }
    action = request(client)
    first = client.post("/api/backlog/order", json=action)
    assert first.status_code == 200
    current = client.get("/api/backlog").json()
    assert [item["feature"] for item in current["items"]] == [
        "gamma",
        "alpha",
        "beta",
        "blocked",
    ]
    assert [item["rank"] for item in current["items"]] == [1, 2, 3, 4]
    assert {item["feature"]: {k: v for k, v in item.items() if k != "rank"} for item in current["items"]} == {key: {k: v for k, v in item.items() if k != "rank"} for key, item in original.items()}
    assert current["ordering"]["revision"] == action["revision"] + 1
    backlog.reconcile(
        review(backlog, changes={"alpha": {"rank": 0}, "gamma": {"rank": 99}})
    )
    restarted = Backlog(Store(tmp_path)).list()
    assert [item["feature"] for item in restarted["items"]][:3] == [
        "gamma",
        "alpha",
        "beta",
    ]
    assert restarted["review"]["current"]
    assert restarted["ordering"] == current["ordering"]
    assert client.post("/api/backlog/order", json=action).json() == first.json()


def test_exact_retry_never_reapplies_old_order_and_changed_id_payload_conflicts(
    tmp_path,
):
    _, client = setup(tmp_path)
    first_action = request(client)
    first = client.post("/api/backlog/order", json=first_action).json()
    second_action = request(client, ["beta", "gamma", "alpha"])
    assert client.post("/api/backlog/order", json=second_action).status_code == 200
    assert client.post("/api/backlog/order", json=first_action).json() == first
    assert [item["feature"] for item in client.get("/api/backlog").json()["items"]][
        :3
    ] == ["beta", "gamma", "alpha"]
    assert (
        client.post(
            "/api/backlog/order",
            json={**first_action, "features": ["alpha", "beta", "gamma"]},
        ).status_code
        == 409
    )


@pytest.mark.parametrize("change", ["order", "facts", "deployment", "dependency"])
def test_stale_inventory_or_order_cannot_overwrite_current_decisions(tmp_path, change):
    backlog, client = setup(tmp_path)
    stale = request(client)
    if change == "order":
        assert (
            client.post(
                "/api/backlog/order", json=request(client, ["beta", "alpha", "gamma"])
            ).status_code
            == 200
        )
    elif change == "deployment":
        from datetime import UTC, datetime

        Updates(backlog.store).publish(
            Publication(
                feature="beta",
                title="beta",
                summary="Deployment fixture",
                deploymentId="artifact",
                deployedAt=datetime.now(UTC),
            )
        )
    else:
        backlog.reconcile(
            review(
                backlog,
                changes={
                    "alpha": {"dependencies": ["beta"]}
                    if change == "dependency"
                    else {"currentStep": "New factual evidence"}
                },
            )
        )
    before = client.get("/api/backlog").json()
    assert client.post("/api/backlog/order", json=stale).status_code == 409
    assert client.get("/api/backlog").json()["ordering"] == before["ordering"]


@pytest.mark.parametrize(
    "features,status",
    [
        (["alpha", "beta"], 409),
        (["alpha", "beta", "blocked"], 409),
        (["alpha", "beta", "beta"], 422),
        (["alpha", "beta", "../secret"], 422),
    ],
)
def test_complete_lane_and_distinct_valid_ids_are_required(tmp_path, features, status):
    _, client = setup(tmp_path)
    prior = client.get("/api/backlog").json()["ordering"]["revision"]
    assert (
        client.post("/api/backlog/order", json=request(client, features)).status_code
        == status
    )
    assert client.get("/api/backlog").json()["ordering"]["revision"] == prior


def test_concurrent_orders_only_one_revision_wins(tmp_path):
    _, client = setup(tmp_path)
    actions = [request(client), request(client, ["beta", "alpha", "gamma"])]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda body: client.post("/api/backlog/order", json=body), actions)
        )
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert client.get("/api/backlog").json()["ordering"]["revision"] == actions[0]["revision"] + 1


def test_real_app_order_requires_authenticated_same_origin(tmp_path):
    from test_api import FakeCodex, login

    from leam_api.app import create_app

    backlog, _ = setup(tmp_path)
    app = create_app(
        tmp_path,
        {"http://testserver"},
        bootstrap="bootstrap-for-tests",
        codex=FakeCodex(),
    )
    with TestClient(app) as client:
        body = {
            "requestId": str(uuid4()),
            "revision": backlog.list()["ordering"]["revision"],
            "lane": "ready",
            "features": ["gamma", "alpha", "beta"],
            "expectedRevisions": {
                item["feature"]: item["revision"] for item in backlog.list()["items"]
            },
        }
        assert (
            client.post(
                "/api/backlog/order", json=body, headers={"origin": "http://testserver"}
            ).status_code
            == 401
        )
        login(client)
        assert (
            client.post(
                "/api/backlog/order",
                json=body,
                headers={"origin": "https://untrusted.example"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/backlog/order", json=body, headers={"origin": "http://testserver"}
            ).status_code
            == 200
        )


def test_global_rank_crosses_stages_and_lane_reorder_preserves_other_slots(tmp_path):
    backlog, client = setup(tmp_path)
    action = request(client, ["beta", "blocked", "alpha", "gamma"], lane="all")
    assert client.post("/api/backlog/order", json=action).status_code == 200
    rows = client.get("/api/backlog").json()["items"]
    assert [(r["feature"], r["rank"]) for r in rows] == [("beta",1),("blocked",2),("alpha",3),("gamma",4)]
    assert rows[1]["deliveryLane"] == "blocked"
    assert client.post("/api/backlog/order", json=request(client,["gamma","beta","alpha"])).status_code == 200
    rows = client.get("/api/backlog").json()["items"]
    assert [r["feature"] for r in rows] == ["gamma","blocked","beta","alpha"]
    backlog.reconcile(review(backlog, changes={"gamma":{"rank":99,"deliveryState":"handover"},"alpha":{"rank":0}}))
    again=Backlog(Store(tmp_path)).list()["items"]
    assert [(r["feature"],r["rank"]) for r in again] == [(r["feature"],r["rank"]) for r in rows]


def test_legacy_user_order_seeds_canonical_rank_and_new_items_append(tmp_path):
    backlog, client=setup(tmp_path)
    backlog.store.set("backlog:user-order", {"revision":3,"lanes":{"ready":["gamma","alpha","beta"]},"updatedAt":1})
    assert [r["feature"] for r in client.get("/api/backlog").json()["items"]] == ["gamma","alpha","beta","blocked"]
    assert client.post("/api/backlog/order",json=request(client,["beta","alpha","gamma"])).status_code==200
    backlog.upsert(Assessment(feature="new",title="New",currentStep="New scope",percent=0,rank=0,deliveryState="handover"))
    rows=client.get("/api/backlog").json()["items"]
    assert rows[-1]["feature"]=="new"
    assert [r["rank"] for r in rows]==[1,2,3,4,5]


def test_upgrade_persists_legacy_order_before_any_new_drag(tmp_path):
    backlog, client = setup(tmp_path)
    backlog.store.set("backlog:user-order", {"revision":3,"lanes":{"ready":["gamma","alpha","beta"]},"updatedAt":1})
    restarted=Backlog(Store(tmp_path))
    initial=restarted.list()
    assert initial["ordering"]["features"] == ["gamma","alpha","beta","blocked"]
    restarted.reconcile(review(restarted,changes={"gamma":{"rank":99,"deliveryState":"blocked"},"alpha":{"rank":0,"deliveryState":"handover"}}))
    later=Backlog(Store(tmp_path)).list()
    assert later["ordering"]["features"] == initial["ordering"]["features"]
    assert [r["rank"] for r in later["items"]] == [1,2,3,4]
