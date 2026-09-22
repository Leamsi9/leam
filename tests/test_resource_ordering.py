"""Ordering through library API with real association and canonical parent state."""

import base64
import json

import pytest
from test_api import login
from test_artifacts import client_for
from test_resource_links import mutation
from test_resources import ORIGIN

from leam_api.artifacts import Artifacts
from leam_api.resource_links import Mutation, ResourceLinks
from leam_api.store import Store


def setup(path):
    store = Store(path)
    artifacts = Artifacts(store)
    for key, title, stamp in [
        ("a", "Zulu", 10),
        ("b", "alpha", 20),
        ("c", "Middle", 30),
        ("d", "Orphan", 40),
    ]:
        artifacts.publish(key, title, "text", key)
        store.set("artifact:" + key, {**artifacts.get(key), "publishedAt": stamp})
    alpha = store.create("commitment", {"title": "Alpha parent"})
    zulu = store.create("commitment", {"title": "Zulu parent"})
    links = ResourceLinks(store)
    links.mutate("a", Mutation(**mutation(alpha)))
    links.mutate("b", Mutation(**mutation(zulu)))
    return store, artifacts, links, alpha, zulu


def collect(client, **params):
    cursor, rows = None, []
    for _ in range(10):
        result = client.get(
            "/api/artifacts",
            params={**params, "limit": 1, **({"cursor": cursor} if cursor else {})},
        )
        assert result.status_code == 200, result.text
        page = result.json()
        rows.extend(page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            return rows
    pytest.fail("Cursor failed to terminate")


@pytest.mark.parametrize(
    "sort,order,expected",
    [
        ("title", "asc", ["b", "c", "d", "a"]),
        ("title", "desc", ["a", "d", "c", "b"]),
        ("parent", "asc", ["a", "b", "c", "d"]),
        ("parent", "desc", ["b", "a", "d", "c"]),
    ],
)
def test_orderings_stable_pages_and_orphans_last(tmp_path, sort, order, expected):
    setup(tmp_path)
    with client_for(tmp_path) as client:
        login(client)
        rows = collect(client, sort=sort, order=order)
        assert [row["id"] for row in rows] == expected
        assert all("content" not in row for row in rows)
        if sort == "parent":
            assert [row["sortParent"] is None for row in rows] == [
                False,
                False,
                True,
                True,
            ]
        page = client.get(
            "/api/artifacts", params={"sort": sort, "order": order, "limit": 1}
        ).json()
        for changes in [
            {"order": "asc" if order == "desc" else "desc"},
            {"q": "other"},
        ]:
            assert (
                client.get(
                    "/api/artifacts",
                    params={
                        "sort": sort,
                        "order": order,
                        "cursor": page["nextCursor"],
                        **changes,
                    },
                ).status_code
                == 422
            )


def test_parent_titles_are_canonical_and_deleted_targets_become_orphans(tmp_path):
    store, _, _, alpha, _ = setup(tmp_path)
    with client_for(tmp_path) as client:
        login(client)
        store.update(
            alpha["id"], "commitment", alpha["revision"], {"title": "ZZ renamed"}
        )
        rows = collect(client, sort="parent", order="asc")
        assert [r["id"] for r in rows] == ["b", "a", "c", "d"]
        assert rows[1]["sortParent"]["title"] == "ZZ renamed"
        with store.connect() as db:
            db.execute("DELETE FROM entities WHERE id=?", (alpha["id"],))
        rows = collect(client, sort="parent", order="desc")
        assert rows[0]["id"] == "b"
        assert all(row["sortParent"] is None for row in rows[1:])


def test_modified_tracks_link_and_unlink_but_not_read_or_exact_retry(
    tmp_path, monkeypatch
):
    _store, artifacts, links, alpha, _ = setup(tmp_path)
    from leam_api import resource_links

    monkeypatch.setattr(resource_links.time, "time", lambda: 2000)
    body = Mutation(**mutation(alpha, revision=1, operation="unlink"))
    links.mutate("a", body)
    before = artifacts.metadata(artifacts.get("a"))["modifiedAt"]
    assert before == 2000
    monkeypatch.setattr(resource_links.time, "time", lambda: 3000)
    links.mutate("a", body)
    assert artifacts.metadata(artifacts.get("a"))["modifiedAt"] == before
    with client_for(tmp_path) as client:
        login(client)
        assert (
            client.post(
                "/api/artifacts/_read", json={"ids": ["a"]}, headers=ORIGIN
            ).status_code
            == 200
        )
        assert client.get("/api/artifacts/a").json()["modifiedAt"] == before
        # Link timestamps from setup may be newer than the synthetic unlink time.
        rows = collect(client, sort="modified", order="asc")
        assert [row["modifiedAt"] for row in rows] == sorted(
            row["modifiedAt"] for row in rows
        )
        newest = collect(client)
        assert [row["modifiedAt"] for row in newest] == sorted(
            (r["modifiedAt"] for r in newest), reverse=True
        )


def test_legacy_cursor_and_filters_and_long_unicode_title(tmp_path):
    store = Store(tmp_path)
    artifacts = Artifacts(store)
    for key in ["a", "b", "c"]:
        artifacts.publish(key, "ΐ" * 200, "text", key)
        store.set("artifact:" + key, {**artifacts.get(key), "publishedAt": 10})
    cursor = base64.urlsafe_b64encode(json.dumps([10, "b"]).encode()).decode()
    assert [row["id"] for row in artifacts.list(cursor=cursor)["items"]] == ["a"]
    with client_for(tmp_path) as client:
        login(client)
        assert len(collect(client, sort="title", order="asc")) == 3
        assert (
            client.get("/api/artifacts", params={"sort": "unsupported"}).status_code
            == 422
        )
        assert (
            client.get(
                "/api/artifacts", params={"kind": "image", "sort": "title"}
            ).json()["items"]
            == []
        )
