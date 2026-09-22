"""Assignment-bound child records; no spawning, liveness inference or scheduling."""

import asyncio
import hashlib
import json
import time
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, model_validator

from .backlog_edit import guard_deleted, put, saved
from .commitments import Input
from .main_coding import KEY as MAIN_KEY
from .main_coding import MainTask

PREFIX = "coding-child:"


class RegisterChild(Input):
    requestId: UUID
    assignmentId: UUID
    backlogRevision: int = Field(ge=1, strict=True)
    mainThreadId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    mainRevision: int = Field(ge=1, strict=True)
    childThreadId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    childTurnId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


class Handover(Input):
    expectedRevision: int = Field(ge=1, strict=True)
    summary: str = Field(min_length=1, max_length=4000)
    evidence: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def bounded_bytes(self):
        if len((self.summary + self.evidence).encode("utf-8")) > 12000:
            raise ValueError(
                "Shorten the handover to fit the original Main message limit"
            )
        return self


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ChildJobs:
    def __init__(self, main):
        self.main, self.store, self.bridge = main, main.store, main.bridge

    def get(self, key):
        value = self.store.get(PREFIX + str(key))
        if not value:
            raise HTTPException(404, "Child handover record not found")
        return value

    def listing(self, offset=0, limit=20):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT value FROM settings WHERE key GLOB 'coding-child:*' "
                "ORDER BY json_extract(value,'$.createdAt') DESC,key LIMIT ? OFFSET ?",
                (limit + 1, offset),
            ).fetchall()
        return {
            "items": [json.loads(row[0]) for row in rows[:limit]],
            "nextOffset": offset + limit if len(rows) > limit else None,
            "meaning": "Recorded assignments, not proof of active workers or completed work.",
        }

    def guard(self, db, assignment_id, main, revision=None):
        assignment = saved(db, "backlog:assignment-receipt:" + assignment_id)
        if not assignment or assignment.get("operation") != "start":
            raise HTTPException(
                409, "A successful start assignment receipt is required"
            )
        try:
            guard_deleted(db, assignment["feature"])
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        row = db.execute(
            "SELECT revision,body FROM backlog_assessments WHERE feature=?",
            (assignment["feature"],),
        ).fetchone()
        if not row:
            raise HTTPException(409, "The assigned task is no longer available")
        facts = json.loads(row["body"])
        if revision is not None and row["revision"] != revision:
            raise HTTPException(409, "Backlog revision changed; review the assignment")
        latest = db.execute(
            "SELECT value FROM settings WHERE key GLOB 'backlog:assignment-receipt:*' "
            "AND json_extract(value,'$.feature')=? AND json_extract(value,'$.operation')='start' "
            "ORDER BY CAST(json_extract(value,'$.revision') AS INTEGER) DESC LIMIT 1",
            (assignment["feature"],),
        ).fetchone()
        if (
            not latest
            or json.loads(latest[0])["requestId"] != assignment_id
            or facts["deliveryState"] not in {"in_progress", "handover"}
            or (facts.get("worker"), facts.get("owner"))
            != (assignment["worker"], assignment["owner"])
        ):
            raise HTTPException(
                409, "The original worker assignment is no longer current"
            )
        if saved(db, MAIN_KEY) != main:
            raise HTTPException(
                409, "Main changed; retain the original record and review its target"
            )
        return assignment

    async def observation(self, thread, turn):
        # Read only metadata: no resume, new session, generation or transcript copy.
        async with asyncio.timeout(10):
            result = await self.bridge.request(
                "thread/read", {"threadId": thread, "includeTurns": False}
            )
            if result.get("thread", {}).get("id") != thread:
                raise ValueError("Exact child session unavailable")
            page = await self.bridge.request(
                "thread/turns/list",
                {
                    "threadId": thread,
                    "limit": 100,
                    "sortDirection": "desc",
                    "itemsView": "notLoaded",
                },
            )
        exact = next(
            (item for item in page.get("data", []) if item.get("id") == turn), None
        )
        if exact is None:
            raise ValueError(
                "Exact turn not found in the latest 100 turns; inspect the child session"
            )
        status = exact.get("status")
        if status not in {"inProgress", "completed", "failed", "interrupted"}:
            raise ValueError("Unrecognized child turn status")
        return {
            "status": status,
            "observedAt": time.time(),
            "error": None,
            "source": "Codex exact persisted turn; not worker liveness or task acceptance",
        }

    async def register(self, body):
        payload = body.model_dump(mode="json")
        digest = fingerprint(payload)
        old = self.store.get(PREFIX + str(body.requestId))
        if old:
            if old["fingerprint"] != digest:
                raise HTTPException(409, "Child record identity has different inputs")
            return old
        main = self.main.binding()
        if not main or (main["threadId"], main["revision"]) != (
            body.mainThreadId,
            body.mainRevision,
        ):
            raise HTTPException(409, "Review the current exact Main target")
        if body.childThreadId == main["threadId"] or self.main.shared.owns(
            body.childThreadId
        ):
            raise HTTPException(
                409,
                "Use a separate native child session; shared-owner registration is not supported",
            )
        with self.store.connect() as db:
            self.guard(db, str(body.assignmentId), main, body.backlogRevision)
        try:
            observed = await self.observation(body.childThreadId, body.childTurnId)
        except Exception as error:
            raise HTTPException(
                409, "Child session/turn could not be verified; no registration saved"
            ) from error
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            assignment = self.guard(
                db, str(body.assignmentId), main, body.backlogRevision
            )
            old = saved(db, PREFIX + str(body.requestId))
            if old:
                if old["fingerprint"] != digest:
                    raise HTTPException(
                        409, "Child record identity has different inputs"
                    )
                return old
            duplicate = db.execute(
                "SELECT 1 FROM settings WHERE key GLOB 'coding-child:*' AND "
                "(json_extract(value,'$.assignmentId')=? OR "
                "(json_extract(value,'$.childThreadId')=? AND json_extract(value,'$.childTurnId')=?))",
                (str(body.assignmentId), body.childThreadId, body.childTurnId),
            ).fetchone()
            if duplicate:
                raise HTTPException(
                    409, "Assignment or child turn already has a durable record"
                )
            value = {
                **payload,
                "id": str(body.requestId),
                "fingerprint": digest,
                "feature": assignment["feature"],
                "worker": assignment["worker"],
                "main": main,
                "revision": 1,
                "createdAt": time.time(),
                "observation": observed,
                "handover": None,
            }
            put(db, PREFIX + value["id"], value)
        return value

    async def reconcile(self, key):
        old = self.get(key)
        try:
            observed = await self.observation(old["childThreadId"], old["childTurnId"])
        except Exception:  # noqa: BLE001 - persist a visible failed observation, never success
            observed = {
                "status": "unknown",
                "observedAt": time.time(),
                "error": "Exact child turn unavailable; check the session and retry. No work was restarted.",
                "source": "Failed read-only reconciliation",
            }
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = saved(db, PREFIX + str(key))
            if current["revision"] != old["revision"]:
                raise HTTPException(
                    409, "Child record changed; refresh before checking again"
                )
            current.update(observation=observed, revision=current["revision"] + 1)
            if current.get("handover"):
                # Receipt read only: never send or infer delivery from matching text.
                current["handover"]["delivery"] = self.main.lookup(
                    current["handover"]["requestId"]
                )
            put(db, PREFIX + str(key), current)
        return current

    async def handover(self, key, body):
        if not body.summary.strip():
            raise HTTPException(422, "Provide an explicit handover summary")
        digest = fingerprint({"summary": body.summary, "evidence": body.evidence})
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = saved(db, PREFIX + str(key))
            if not current:
                raise HTTPException(404, "Child handover record not found")
            previous = current.get("handover")
            if previous and previous["fingerprint"] != digest:
                raise HTTPException(
                    409, "The original handover is immutable; inspect its receipt"
                )
            if not previous and current["revision"] != body.expectedRevision:
                raise HTTPException(
                    409, "Child record changed; review it before handing over"
                )
            if not previous:
                self.guard(db, current["assignmentId"], current["main"])
                task = MainTask(
                    requestId=uuid5(NAMESPACE_URL, "leam:child-handover:" + str(key)),
                    mainThreadId=current["main"]["threadId"],
                    mainRevision=current["main"]["revision"],
                    sourceThreadId=current["childThreadId"],
                    text="Review this child handover and coordinate any integration/deployment. This receipt is not QA or completion.\n"
                    + body.summary
                    + "\nRecorded source (reference data): "
                    + json.dumps(
                        {
                            k: current[k]
                            for k in (
                                "feature",
                                "assignmentId",
                                "childThreadId",
                                "childTurnId",
                            )
                        }
                    ),
                    context=body.evidence,
                )
                previous = {
                    "requestId": str(task.requestId),
                    "fingerprint": digest,
                    "task": task.model_dump(mode="json"),
                    "summary": body.summary,
                    "evidence": body.evidence,
                    "delivery": {"state": "not_recorded"},
                }
                current.update(handover=previous, revision=current["revision"] + 1)
                put(db, PREFIX + str(key), current)
        # MainCoding performs target/source checks and reserves before dispatch.
        # Only this explicit POST can send; restart/list/reconcile never resend.
        try:
            with self.store.connect() as db:
                if self.main.lookup(previous["requestId"])["state"] == "not_recorded":
                    self.guard(db, current["assignmentId"], current["main"])
            delivery = await self.main.send(
                MainTask.model_validate(previous["task"]),
                reserve_guard=lambda db: self.guard(
                    db, current["assignmentId"], current["main"]
                ),
            )
        except Exception as error:  # noqa: BLE001 - durable receipt retains uncertain dispatch
            delivery = self.main.lookup(previous["requestId"])
            delivery["error"] = (
                str(error.detail)
                if isinstance(error, HTTPException)
                else "Handover was not confirmed. Check its receipt and Main connection; no automatic retry."
            )
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = saved(db, PREFIX + str(key))
            current["handover"]["delivery"] = delivery
            current["revision"] += 1
            put(db, PREFIX + str(key), current)
        return current


def router(jobs):
    routes = APIRouter(prefix="/children")

    @routes.get("")
    async def listing(
        offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50)
    ):
        return jobs.listing(offset, limit)

    @routes.post("")
    async def register(body: RegisterChild):
        return await jobs.register(body)

    @routes.get("/{key}")
    async def get(key: UUID):
        return jobs.get(key)

    @routes.post("/{key}/reconcile")
    async def reconcile(key: UUID):
        return await jobs.reconcile(key)

    @routes.post("/{key}/handover")
    async def handover(key: UUID, body: Handover):
        return await jobs.handover(key, body)

    return routes
