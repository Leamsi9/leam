"""User policy for trusted product-origin changes; origin is never model input."""

import json

from fastapi import HTTPException
from pydantic import Field, StrictBool

from .commitments import Input

KEY = "proposals.approval-policy.v1"
ALLOWED = {
    "agenda.triage",
    "commitment.create",
    "commitment.edit",
    "commitment.progress",
    "commitment.subtask",
    "capacity.create",
    "capacity.edit",
}


class ApprovalPolicy(Input):
    revision: int = Field(ge=0, strict=True)
    todayRequiresApproval: StrictBool = False
    goalsRequiresApproval: StrictBool = False


def read(db):
    row = db.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    return ApprovalPolicy(**json.loads(row[0])) if row else ApprovalPolicy(revision=0)


def get(store):
    with store.connect() as db:
        return read(db).model_dump()


def update(store, policy):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = read(db)
        if current.revision != policy.revision:
            raise HTTPException(409, "Approval settings changed; refresh before saving")
        value = policy.model_copy(
            update={"revision": current.revision + 1}
        ).model_dump()
        db.execute(
            "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (KEY, json.dumps(value)),
        )
    return value


def decision(db, operation, origin):
    if operation == "agenda.triage" and origin != "today":
        return {"mode": "manual"}
    if operation not in ALLOWED or origin not in ("today", "goals"):
        return {"mode": "manual"}
    policy = read(db)
    required = getattr(policy, origin + "RequiresApproval")
    return {
        "mode": "manual" if required else "automatic",
        "policyRevision": policy.revision,
        "origin": origin,
    }
