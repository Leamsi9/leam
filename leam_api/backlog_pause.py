"""User-owned queue suspension; distinct from ending a worker assignment."""

import hashlib
import json
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field

from .backlog import FeatureId
from .backlog_edit import put, saved
from .commitments import Input


class Pause(Input):
    requestId: UUID
    paused: bool = Field(strict=True)
    expectedRevisions: dict[FeatureId, int] = Field(min_length=1, max_length=500)
    allQueued: bool = False


def apply_pause(backlog, body):
    payload = body.model_dump(mode="json")
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()
    key = "backlog:pause-receipt:" + str(body.requestId)
    with backlog.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        prior = saved(db, key)
        if prior:
            if prior["fingerprint"] != fingerprint:
                raise HTTPException(
                    409, "Pause request ID already has different content"
                )
            return prior
        deployed = backlog.deployed(db)
        queued = {
            r["feature"]: r
            for r in db.execute("SELECT * FROM backlog_assessments")
            if r["feature"] not in deployed
            and backlog.item(r)["deliveryState"] in {"queued", "ready"}
        }
        if body.allQueued and set(body.expectedRevisions) != set(queued):
            raise HTTPException(409, "Queued work changed; refresh before pausing all")
        for feature, revision in body.expectedRevisions.items():
            if feature not in queued or queued[feature]["revision"] != revision:
                raise HTTPException(
                    409,
                    "Ticket changed or is already assigned; refresh before changing pause",
                )
        changed = []
        for feature in body.expectedRevisions:
            row = queued[feature]
            facts = json.loads(row["body"])
            override = saved(db, "backlog:user-fields:" + feature) or {}
            override.setdefault("fields", {})["paused"] = body.paused
            put(db, "backlog:user-fields:" + feature, override)
            facts["paused"] = body.paused
            db.execute(
                "UPDATE backlog_assessments SET body=?,revision=revision+1 WHERE feature=?",
                (json.dumps(facts), feature),
            )
            changed.append(
                {
                    "feature": feature,
                    "revision": row["revision"] + 1,
                    "paused": body.paused,
                }
            )
        receipt = {
            "requestId": str(body.requestId),
            "fingerprint": fingerprint,
            "items": changed,
            "paused": body.paused,
            "changedAt": backlog.clock(),
        }
        put(db, key, receipt)
    backlog.store.event(
        "backlog.changed", {"features": list(body.expectedRevisions), "source": "user"}
    )
    return receipt
