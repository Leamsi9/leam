"""Revisioned resource associations; target records remain authoritative."""

import hashlib
import json
import re
import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, model_validator

from .artifacts import ID, metadata
from .commitments import Input
from .updates import Updates

TargetType = Literal["commitment", "feature", "proposal"]
PREFIX = "resource-links:"
MAX_PER_RESOURCE = 32
MAX_LINKS = 10000
FEATURE = re.compile(r"[a-z0-9][a-z0-9-]{0,79}\Z")


class Target(Input):
    targetType: TargetType
    targetId: str = Field(min_length=1, max_length=96)

    @model_validator(mode="after")
    def identity(self):
        if self.targetType == "feature":
            if not FEATURE.fullmatch(self.targetId):
                raise ValueError("Invalid feature identity")
        else:
            self.targetId = str(UUID(self.targetId))
        return self


class Mutation(Target):
    requestId: UUID
    revision: int = Field(ge=0, strict=True)
    operation: Literal["link", "unlink"]


def exists(db, table):
    return (
        db.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


class ResourceLinks:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def resource(db, key):
        if not ID.fullmatch(key):
            raise HTTPException(404, "Resource not found")
        row = db.execute(
            "SELECT json_remove(value,'$.content','$.contentBase64') FROM settings WHERE key=?",
            ("artifact:" + key,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Resource not found")
        return metadata(json.loads(row[0]))

    @staticmethod
    def saved(db, resource_id):
        row = db.execute(
            "SELECT value FROM settings WHERE key=?", (PREFIX + resource_id,)
        ).fetchone()
        return json.loads(row[0]) if row else {"revision": 0, "items": []}

    @staticmethod
    def target(db, target_type, target_id):
        base = {
            "targetType": target_type,
            "targetId": target_id,
            "available": False,
            "title": None,
            "state": None,
            "url": None,
        }
        if target_type == "commitment":
            row = db.execute(
                "SELECT body,revision FROM entities WHERE id=? AND kind='commitment'",
                (target_id,),
            ).fetchone()
            if row:
                body = json.loads(row["body"])
                return {
                    **base,
                    "available": True,
                    "title": body["title"],
                    "state": body.get("status", "active"),
                    "revision": row["revision"],
                    "capacityId": body.get("capacityId"),
                    "owner": body.get("owner", "user"),
                    "stage": body.get("stage", "todo"),
                    "dueDate": body.get("dueDate"),
                    "location": "goals",
                }
        elif target_type == "feature":
            if exists(db, "deployment_updates"):
                row = db.execute(
                    "SELECT * FROM deployment_updates WHERE feature=? AND superseded=0 ORDER BY sequence DESC LIMIT 1",
                    (target_id,),
                ).fetchone()
                if row:
                    update = Updates.item(row)
                    return {
                        **base,
                        "available": True,
                        "title": update["title"],
                        "state": update["stage"],
                        "revision": update["revision"],
                        "updateId": update["id"],
                        "location": "updates",
                        "completed": update["completed"],
                    }
            if exists(db, "backlog_assessments"):
                row = db.execute(
                    "SELECT body,revision FROM backlog_assessments WHERE feature=?",
                    (target_id,),
                ).fetchone()
                if row:
                    body = json.loads(row["body"])
                    return {
                        **base,
                        "available": True,
                        "title": body["title"],
                        "state": body.get("deliveryState", "queued"),
                        "revision": row["revision"],
                        "location": "backlog",
                    }
        elif target_type == "proposal":
            row = db.execute(
                "SELECT operation,input,state,updated FROM proposals WHERE id=?",
                (target_id,),
            ).fetchone()
            if row and row["state"] not in ("declined", "superseded"):
                body = json.loads(row["input"])
                return {
                    **base,
                    "available": True,
                    "title": body.get("title") or body.get("name") or row["operation"],
                    "state": row["state"],
                    "operation": row["operation"],
                    "updatedAt": row["updated"],
                    "location": "approvals",
                }
        return base

    def get(self, resource_id):
        with self.store.connect() as db:
            db.execute("BEGIN")
            self.resource(db, resource_id)
            value = self.saved(db, resource_id)
            return {
                "resourceId": resource_id,
                "revision": value["revision"],
                "items": [
                    {
                        **self.target(db, item["targetType"], item["targetId"]),
                        "linkedAt": item["linkedAt"],
                    }
                    for item in value["items"]
                ],
            }

    def mutate(self, resource_id, body):
        fingerprint = hashlib.sha256(
            json.dumps(
                [resource_id, body.model_dump(mode="json")], sort_keys=True
            ).encode()
        ).hexdigest()
        receipt_key = "resource-link-receipt:" + str(body.requestId)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM settings WHERE key=?",
                ("resource-deleted:" + resource_id,),
            ).fetchone():
                raise HTTPException(
                    410, "Resource was deleted; its associations cannot be restored"
                )
            prior = db.execute(
                "SELECT value FROM settings WHERE key=?", (receipt_key,)
            ).fetchone()
            if prior:
                value = json.loads(prior[0])
                if value["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "Link request identity belongs to a different change"
                    )
                return value["result"]
            self.resource(db, resource_id)
            value = self.saved(db, resource_id)
            if value["revision"] != body.revision:
                raise HTTPException(
                    409, "Resource links changed; refresh before editing"
                )
            matching = [
                r
                for r in value["items"]
                if (r["targetType"], r["targetId"]) == (body.targetType, body.targetId)
            ]
            changed = False
            if body.operation == "link":
                if not self.target(db, body.targetType, body.targetId)["available"]:
                    raise HTTPException(404, "Linked target is unavailable")
                if not matching:
                    total = db.execute(
                        "SELECT coalesce(sum(json_array_length(value,'$.items')),0) FROM settings WHERE key LIKE 'resource-links:%'"
                    ).fetchone()[0]
                    if len(value["items"]) >= MAX_PER_RESOURCE or total >= MAX_LINKS:
                        raise HTTPException(413, "Resource association limit reached")
                    value["items"].append(
                        {
                            "targetType": body.targetType,
                            "targetId": body.targetId,
                            "linkedAt": time.time(),
                        }
                    )
                    changed = True
            elif matching:
                value["items"] = [r for r in value["items"] if r not in matching]
                changed = True
            if changed:
                value["revision"] += 1
                value["updatedAt"] = time.time()
                db.execute(
                    "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (PREFIX + resource_id, json.dumps(value)),
                )
            result = {
                "resourceId": resource_id,
                "revision": value["revision"],
                "operation": body.operation,
                "targetType": body.targetType,
                "targetId": body.targetId,
            }
            db.execute(
                "INSERT INTO settings VALUES (?,?)",
                (
                    receipt_key,
                    json.dumps({"fingerprint": fingerprint, "result": result}),
                ),
            )
            return result

    def reverse(self, target, *, limit=30, cursor=None):
        if cursor and not ID.fullmatch(cursor):
            raise HTTPException(422, "Invalid resource cursor")
        with self.store.connect() as db:
            db.execute("BEGIN")
            # Associations contain identifiers only; fetch current resource metadata.
            condition = "EXISTS(SELECT 1 FROM json_each(s.value,'$.items') j WHERE json_extract(j.value,'$.targetType')=? AND json_extract(j.value,'$.targetId')=?)"
            args = [target.targetType, target.targetId]
            total = db.execute(
                "SELECT count(*) FROM settings s WHERE s.key LIKE 'resource-links:%' AND "
                + condition,
                args,
            ).fetchone()[0]
            rows = db.execute(
                "SELECT substr(s.key,16) FROM settings s WHERE s.key LIKE 'resource-links:%' AND "
                + condition
                + " AND s.key>? ORDER BY s.key LIMIT ?",
                args + [PREFIX + (cursor or ""), limit + 1],
            ).fetchall()
            items = [self.resource(db, row[0]) for row in rows[:limit]]
            return {
                "items": items,
                "target": self.target(db, target.targetType, target.targetId),
                "nextCursor": rows[limit - 1][0] if len(rows) > limit else None,
                "total": total,
            }

    def targets(self, target_type, q="", limit=30, cursor=None):
        if cursor:
            Target(targetType=target_type, targetId=cursor)
        with self.store.connect() as db:
            db.execute("BEGIN")
            if target_type == "commitment":
                sql = "SELECT id FROM entities WHERE kind='commitment' AND id>? AND instr(lower(json_extract(body,'$.title')),lower(?))>0 ORDER BY id LIMIT ?"
            elif target_type == "proposal":
                sql = "SELECT id FROM proposals WHERE state NOT IN ('declined','superseded') AND id>? AND instr(lower(coalesce(json_extract(input,'$.title'),json_extract(input,'$.name'),operation)),lower(?))>0 ORDER BY id LIMIT ?"
            else:
                sources = []
                if exists(db, "backlog_assessments"):
                    sources.append(
                        "SELECT feature id,json_extract(body,'$.title') title FROM backlog_assessments b WHERE NOT EXISTS(SELECT 1 FROM deployment_updates d WHERE d.feature=b.feature AND d.superseded=0)"
                        if exists(db, "deployment_updates")
                        else "SELECT feature id,json_extract(body,'$.title') title FROM backlog_assessments"
                    )
                if exists(db, "deployment_updates"):
                    sources.append(
                        "SELECT feature id,json_extract(body,'$.title') title FROM deployment_updates WHERE superseded=0"
                    )
                if not sources:
                    return {"items": [], "nextCursor": None}
                sql = (
                    "SELECT id FROM ("
                    + " UNION ".join(sources)
                    + ") WHERE id>? AND instr(lower(title),lower(?))>0 ORDER BY id LIMIT ?"
                )
            rows = db.execute(sql, (cursor or "", q, limit + 1)).fetchall()
            return {
                "items": [self.target(db, target_type, row[0]) for row in rows[:limit]],
                "nextCursor": rows[limit - 1][0] if len(rows) > limit else None,
            }


def router(links):
    routes = APIRouter()

    @routes.get("/api/resource-links/targets")
    def targets(
        type: TargetType,
        q: str = Query(default="", max_length=200),
        limit: int = Query(default=30, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=96),
    ):
        try:
            return links.targets(type, q, limit, cursor)
        except ValueError as error:
            raise HTTPException(422, "Invalid target cursor") from error

    @routes.get("/api/resource-links")
    def reverse(
        targetType: TargetType,
        targetId: str = Query(max_length=96),
        limit: int = Query(default=30, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=96),
    ):
        try:
            target = Target(targetType=targetType, targetId=targetId)
        except ValueError as error:
            raise HTTPException(422, "Invalid target identity") from error
        return links.reverse(target, limit=limit, cursor=cursor)

    @routes.get("/api/artifacts/{resource_id}/links")
    def read(resource_id: str):
        return links.get(resource_id)

    @routes.post("/api/artifacts/{resource_id}/links")
    def mutate(resource_id: str, body: Mutation):
        return links.mutate(resource_id, body)

    return routes
