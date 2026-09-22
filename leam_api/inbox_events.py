"""Confirmed action outbox; notification failure never replays its source action."""

import asyncio
import json
import logging
import time
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException

from .inbox import Inbox, InboxCreate
from .restore_automation import state as automation_state

TOPIC = "inbox.automation"
CURSOR = "inbox.automation.cursor"
FAILURE = "inbox.automation.failure:"
MAX_FAILURES = 1024
MAX_ATTEMPTS = 3
BATCH = 20


def enqueue(db, *, event_id, subject, body, links):
    """Internal confirmed-mutation callback; actor is never accepted in user input."""
    request = InboxCreate(
        requestId=uuid5(NAMESPACE_URL, "leam:action-inbox:" + event_id),
        subject=subject[:200],
        body=body,
        links=links,
    )
    db.execute(
        "INSERT INTO events(topic,payload,created) VALUES (?,?,?)",
        (
            TOPIC,
            json.dumps(
                {"eventId": event_id, "request": request.model_dump(mode="json")}
            ),
            time.time(),
        ),
    )


def proposal_event(db, proposal, result):
    review = json.loads(proposal["review"])
    if review.get("actionActor") not in {"companion", "automation"}:
        return
    operation = proposal["operation"]
    if operation not in {
        "commitment.create",
        "commitment.edit",
        "commitment.progress",
        "commitment.subtask",
    }:
        return
    before = review.get("before") or {}
    commitment = result.get("commitment", result)
    if operation in {"commitment.edit", "commitment.subtask"}:
        comparable = {
            k: v for k, v in commitment.items() if k not in {"id", "revision"}
        }
        if all(before.get(k) == v for k, v in comparable.items()):
            return
    if operation == "commitment.progress":
        log = result["log"]
        done = (
            commitment["status"] == "completed"
            if commitment["kind"] == "task"
            else log["done"]
        )
        if before.get("value") == log["value"] and before.get("completed") == done:
            return
        outcome = "Marked complete" if done else "Progress updated"
    else:
        outcome = {
            "commitment.create": "Task added",
            "commitment.edit": "Task updated",
            "commitment.subtask": "Subtasks updated",
        }[operation]
    if (
        operation == "commitment.subtask"
        and json.loads(proposal["input"]).get("action") == "remove"
    ):
        outcome = "Subtask removed"
    title = commitment.get("title", "Commitment")[:160]
    enqueue(
        db,
        event_id="proposal:" + proposal["id"],
        subject=f"{outcome}: {title}",
        body=f"Leam applied this change: {outcome.lower()} for “{title}”. Review the linked task for its current status. Assignment alone does not start work or configure a reminder.",
        links=[
            {"type": "commitment", "id": commitment["id"]},
            {"type": "proposal", "id": proposal["id"]},
        ],
    )


def resource_event(db, item):
    enqueue(
        db,
        event_id="resource:" + item["id"],
        subject="Resource saved: " + item["title"],
        body="Leam saved this resource. Open the linked file to review it; saving a file does not mean any related task is complete.",
        links=[{"type": "resource", "id": item["id"]}],
    )


class InboxEvents:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    @staticmethod
    def cursor(db):
        row = db.execute("SELECT value FROM settings WHERE key=?", (CURSOR,)).fetchone()
        return json.loads(row[0]) if row else 0

    @staticmethod
    def failures(db):
        return [
            json.loads(row[0])
            for row in db.execute(
                "SELECT value FROM settings WHERE substr(key,1,?)=?",
                (len(FAILURE), FAILURE),
            )
        ]

    @classmethod
    def status(cls, db):
        failures = cls.failures(db)
        return {
            "queued": db.execute(
                "SELECT count(*) FROM events WHERE id>? AND topic=?",
                (cls.cursor(db), TOPIC),
            ).fetchone()[0],
            "retrying": sum(item["attempts"] < MAX_ATTEMPTS for item in failures),
            "failed": sum(item["attempts"] >= MAX_ATTEMPTS for item in failures),
            "paused": automation_state(db)["held"],
            "failures": sorted(failures, key=lambda item: item["eventId"])[:20],
        }

    def retry(self, event_id):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            key = FAILURE + str(event_id)
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            if not row:
                raise HTTPException(
                    404, "Notification failure is no longer awaiting retry"
                )
            failure = json.loads(row[0])
            failure.update(attempts=0, nextTry=self.clock())
            db.execute(
                "UPDATE settings SET value=? WHERE key=?", (json.dumps(failure), key)
            )
        return {"state": "queued", "eventId": event_id}

    def drain(self):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if automation_state(db)["held"]:
                return 0
            failures = self.failures(db)
            due = sorted(
                (
                    item
                    for item in failures
                    if item["attempts"] < MAX_ATTEMPTS
                    and item["nextTry"] <= self.clock()
                ),
                key=lambda item: (item["nextTry"], item["eventId"]),
            )[: BATCH // 2]
            events = [
                (
                    db.execute(
                        "SELECT * FROM events WHERE id=? AND topic=?",
                        (item["eventId"], TOPIC),
                    ).fetchone(),
                    item["attempts"],
                    False,
                )
                for item in due
            ]
            cursor = self.cursor(db)
            # Bound failure receipts; undelivered source events remain durable.
            room = min(BATCH - len(events), MAX_FAILURES - len(failures))
            if room > 0:
                events.extend(
                    (row, 0, True)
                    for row in db.execute(
                        "SELECT * FROM events WHERE id>? AND topic=? ORDER BY id LIMIT ?",
                        (cursor, TOPIC, room),
                    )
                )
            delivered = 0
            for row, attempts, fresh in events:
                if row is None:
                    continue  # Restores retain source events; do not fabricate success.
                event_id = row["id"]
                key = FAILURE + str(event_id)
                db.execute("SAVEPOINT inbox_delivery")
                try:
                    event = json.loads(row["payload"])
                    Inbox(self.store)._create(
                        db,
                        InboxCreate(**event["request"]),
                        origin="automation",
                        require_links=False,
                    )
                except (HTTPException, ValueError, KeyError, TypeError):
                    db.execute("ROLLBACK TO inbox_delivery")
                    attempts += 1
                    failure = {
                        "eventId": event_id,
                        "attempts": attempts,
                        "nextTry": self.clock() + min(300, 30 * 2 ** (attempts - 1)),
                        "error": "Action completed, but its Inbox note could not be delivered.",
                    }
                    db.execute(
                        "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(failure)),
                    )
                else:
                    db.execute("DELETE FROM settings WHERE key=?", (key,))
                    delivered += 1
                finally:
                    db.execute("RELEASE inbox_delivery")
                if fresh:
                    cursor = event_id
            if events:
                db.execute(
                    "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (CURSOR, json.dumps(cursor)),
                )
            return delivered

    async def run(self):
        while True:
            try:
                await asyncio.to_thread(self.drain)
            except Exception:  # noqa: BLE001 - keep recovery alive without exposing source content
                logging.getLogger(__name__).error(
                    "Action Inbox delivery worker failed; durable events remain queued"
                )
            await asyncio.sleep(5)
