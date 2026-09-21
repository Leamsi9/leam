"""User-controlled memory review policy; never a model-supplied grant."""

import json

from fastapi import HTTPException
from pydantic import Field, StrictBool

from .commitments import Input

KEY = "memory.approval-policy.v1"


class MemoryPolicy(Input):
    revision: int = Field(ge=0, strict=True)
    createRequiresApproval: StrictBool = False
    editRequiresApproval: StrictBool = False
    removeRequiresApproval: StrictBool = False


def read(db):
    row = db.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    return MemoryPolicy(**json.loads(row["value"])) if row else MemoryPolicy(revision=0)


def get(store):
    with store.connect() as db:
        return read(db).model_dump()


def update(store, value):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = read(db)
        if value.revision != current.revision:
            raise HTTPException(
                409, "Memory approval preferences changed; refresh before saving"
            )
        result = value.model_copy(update={"revision": current.revision + 1})
        db.execute(
            "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (KEY, result.model_dump_json()),
        )
        return result.model_dump()


def decision(db, operation):
    if not operation.startswith("memory."):
        return {"mode": "manual"}
    policy = read(db)
    required = getattr(policy, operation.split(".")[1] + "RequiresApproval")
    return {
        "mode": "manual" if required else "automatic",
        "policyRevision": policy.revision,
    }
